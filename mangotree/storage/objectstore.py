"""Content-addressed store for original bytes.

Why originals do not live in MongoDB
------------------------------------
``docs/00-ARCHITECTURE.md`` puts originals in object storage and keeps MongoDB
as the *document layer* (artifacts, cleaned text, OCR output, chunks). GridFS was
a convenient start, but it charges cluster storage — and cluster storage is
expensive, capped, and the wrong shape for 684 MB of PDFs and video.
Measured cost of the shortcut: 175 MB of cluster storage for ~370 emails.

So: bytes go to a content-addressed object store, MongoDB keeps the pointer.

``LocalObjectStore`` is the implementation today and writes under ``RAW_STORE``.
``S3ObjectStore`` slots in behind the same three methods with no caller changes —
which is the entire point of the interface.

Layout shards by the first two hex pairs of the SHA-256, keeping directories
small enough for fast listing::

    raw_store/ab/cd/abcdef...  +  abcdef....meta.json
"""
from __future__ import annotations

import json
import shutil
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from mangotree.core.logging import logger


class ObjectStore(ABC):
    @abstractmethod
    def put(self, sha256: str, data: bytes, filename: str, metadata: dict) -> str: ...

    @abstractmethod
    def get(self, sha256: str) -> Optional[bytes]: ...

    @abstractmethod
    def exists(self, sha256: str) -> bool: ...


class LocalObjectStore(ObjectStore):
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256[2:4] / sha256

    def exists(self, sha256: str) -> bool:
        return self._path(sha256).exists()

    def put(self, sha256: str, data: bytes, filename: str, metadata: dict) -> str:
        target = self._path(sha256)
        if target.exists():
            return str(target)

        target.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp name then rename, so an interrupted run can never
        # leave a truncated file that looks complete under its content hash.
        tmp = target.with_suffix(".partial")
        tmp.write_bytes(data)
        tmp.replace(target)

        meta_path = target.with_name(target.name + ".meta.json")
        meta_path.write_text(
            json.dumps(
                {
                    "sha256": sha256,
                    "original_filename": filename,
                    "size": len(data),
                    "stored_at": datetime.now(timezone.utc).isoformat(),
                    **{k: str(v) for k, v in metadata.items()},
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return str(target)

    def put_file(self, sha256: str, source: Path, metadata: dict) -> str:
        """Copy a file in without loading it into memory (large media)."""
        target = self._path(sha256)
        if target.exists():
            return str(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".partial")
        shutil.copyfile(source, tmp)
        tmp.replace(target)

        meta_path = target.with_name(target.name + ".meta.json")
        meta_path.write_text(
            json.dumps(
                {
                    "sha256": sha256,
                    "original_filename": source.name,
                    "size": source.stat().st_size,
                    "source_path": str(source),
                    "stored_at": datetime.now(timezone.utc).isoformat(),
                    **{k: str(v) for k, v in metadata.items()},
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return str(target)

    def get(self, sha256: str) -> Optional[bytes]:
        path = self._path(sha256)
        return path.read_bytes() if path.exists() else None

    def stats(self) -> dict:
        files = [p for p in self.root.rglob("*") if p.is_file() and not p.name.endswith(".meta.json")]
        return {
            "objects": len(files),
            "bytes": sum(p.stat().st_size for p in files),
            "root": str(self.root),
        }


class SpacesObjectStore(ObjectStore):
    """DigitalOcean Spaces (S3-compatible) — the company's own server.

    Same content-addressed layout as the local store: ``ab/cd/<sha256>``, with
    the original filename and content type kept as object metadata so a
    download comes back as the real file, not a hash. A local directory acts
    as a read-through cache so a file opened twice costs one download.

    Configured from .env:
        OBJECT_STORE=spaces
        DO_SPACES_KEY, DO_SPACES_SECRET, DO_SPACES_REGION (e.g. nyc3),
        DO_SPACES_BUCKET, optional DO_SPACES_ENDPOINT (defaults to
        https://<region>.digitaloceanspaces.com)
    """

    def __init__(self, *, key: str, secret: str, region: str, bucket: str,
                 endpoint: Optional[str] = None, cache_root: Optional[Path] = None, prefix: str = "originals") -> None:
        import boto3
        from botocore.config import Config

        self.endpoint = endpoint or f"https://{region}.digitaloceanspaces.com"
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = boto3.client(
            "s3", region_name=region, endpoint_url=self.endpoint,
            aws_access_key_id=key, aws_secret_access_key=secret,
            config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
        )
        self.cache = LocalObjectStore(cache_root) if cache_root else None

    def _key(self, sha256: str) -> str:
        return f"{self.prefix}/{sha256[:2]}/{sha256[2:4]}/{sha256}"

    def exists(self, sha256: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(sha256))
            return True
        except Exception:
            return False

    @staticmethod
    def _content_type(filename: str, metadata: dict) -> str:
        import mimetypes
        ct = metadata.get("content_type") if isinstance(metadata, dict) else None
        return str(ct or mimetypes.guess_type(filename or "")[0] or "application/octet-stream")

    def put(self, sha256: str, data: bytes, filename: str, metadata: dict) -> str:
        key = self._key(sha256)
        if not self.exists(sha256):
            meta = {"sha256": sha256, "original-filename": (filename or "")[:900],
                    "stored-at": datetime.now(timezone.utc).isoformat()}
            meta.update({k.replace("_", "-")[:60]: str(v)[:900] for k, v in (metadata or {}).items()
                         if isinstance(v, (str, int, float)) and k not in ("content_type",)})
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data,
                                   ContentType=self._content_type(filename, metadata),
                                   ContentDisposition=f'inline; filename="{(filename or sha256)[:200]}"',
                                   Metadata=meta)
        if self.cache:
            self.cache.put(sha256, data, filename, metadata)
        return f"s3://{self.bucket}/{key}"

    def get(self, sha256: str) -> Optional[bytes]:
        if self.cache:
            hit = self.cache.get(sha256)
            if hit is not None:
                return hit
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=self._key(sha256))
            data = obj["Body"].read()
        except Exception as exc:
            logger.warning("Spaces get %s: %s", sha256[:12], exc)
            return None
        if self.cache:
            try:
                self.cache.put(sha256, data, "", {})
            except Exception:
                pass
        return data

    def presigned_url(self, sha256: str, *, expires: int = 900) -> str:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": self._key(sha256)}, ExpiresIn=expires)

    def stats(self) -> dict:
        n = size = 0
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix + "/"):
            for o in page.get("Contents", []):
                n += 1
                size += o.get("Size", 0)
        return {"objects": n, "bytes": size, "root": f"s3://{self.bucket}/{self.prefix}", "endpoint": self.endpoint}


_store: Optional[ObjectStore] = None


def get_object_store() -> ObjectStore:
    global _store
    if _store is None:
        import os
        from mangotree.config.settings import SETTINGS

        if os.environ.get("OBJECT_STORE", "").strip().lower() == "spaces":
            _store = SpacesObjectStore(
                key=os.environ["DO_SPACES_KEY"], secret=os.environ["DO_SPACES_SECRET"],
                region=os.environ.get("DO_SPACES_REGION", "nyc3"), bucket=os.environ["DO_SPACES_BUCKET"],
                endpoint=os.environ.get("DO_SPACES_ENDPOINT") or None,
                cache_root=SETTINGS.raw_store,
            )
            logger.info("Object store: DigitalOcean Spaces %s (cache %s)", _store.bucket, SETTINGS.raw_store)
        else:
            _store = LocalObjectStore(SETTINGS.raw_store)
            logger.info("Object store: local at %s", SETTINGS.raw_store)
    return _store
