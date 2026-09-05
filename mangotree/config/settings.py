"""Environment-backed settings. Secrets live in .env only (git-ignored)."""
from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _require(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise RuntimeError(
            f"Missing required setting '{name}'. Copy .env.example to .env and fill it in."
        )
    return val


@dataclass(frozen=True)
class Settings:
    mongo_uri: str
    mongo_db: str
    anthropic_api_key: str
    voyage_api_key: str
    gmail_client_secret: str
    gmail_token_path: str
    backfill_since: datetime
    raw_store: Path
    disk_corpus_root: Path
    #: Optional. Enables the cross-provider OCR tier for pages Anthropic's
    #: content policy refuses. Without it the cascade falls back to offline OCR.
    openai_api_key: str = ""
    #: Separate OpenAI key for the GPT-6 Astra second reader (admin directive
    #: 2026-09-04), so its spend shows on its own line in the OpenAI console,
    #: apart from the OCR fallback. Falls back to ``openai_api_key`` if unset.
    openai_api_key_critic: str = ""
    #: Microsoft Graph, delegated device-code flow. Tenant and client ids are
    #: public identifiers, not secrets — the only credential in this flow is the
    #: refresh token in the git-ignored cache, and it is bound to one mailbox.
    #: Optional so the whole system still loads before Outlook is connected.
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_mailbox: str = ""
    graph_token_cache: str = ".secrets/graph_token_cache.json"

    @property
    def graph_configured(self) -> bool:
        return bool(self.graph_tenant_id and self.graph_client_id and self.graph_mailbox)

    @property
    def backfill_since_gmail(self) -> str:
        """Gmail search uses YYYY/MM/DD in `after:` queries."""
        return self.backfill_since.strftime("%Y/%m/%d")


def load_settings() -> Settings:
    user = urllib.parse.quote_plus(_require("MONGO_USERNAME"))
    pwd = urllib.parse.quote_plus(_require("MONGO_PASSWORD"))
    cluster = _require("MONGO_CLUSTER")

    since_raw = os.getenv("BACKFILL_SINCE", "2023-10-01").strip()
    since = datetime.strptime(since_raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    raw_store = Path(os.getenv("RAW_STORE", "raw_store"))
    if not raw_store.is_absolute():
        raw_store = PROJECT_ROOT / raw_store

    return Settings(
        mongo_uri=f"mongodb+srv://{user}:{pwd}@{cluster}/?appName=MangoTree",
        mongo_db=os.getenv("MONGO_DB", "samtacode46_db"),
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        voyage_api_key=_require("VOYAGE_API_KEY"),
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        openai_api_key_critic=os.environ.get("OPENAI_API_KEY_CRITIC", "").strip() or os.environ.get("OPENAI_API_KEY", ""),
        gmail_client_secret=os.getenv("GMAIL_CLIENT_SECRET", "client_secret.json"),
        gmail_token_path=os.getenv("GMAIL_TOKEN_PATH", "gmail_token.json"),
        backfill_since=since,
        raw_store=raw_store,
        disk_corpus_root=Path(os.getenv("DISK_CORPUS_ROOT", "")),
        graph_tenant_id=os.getenv("GRAPH_TENANT_ID", "").strip(),
        graph_client_id=os.getenv("GRAPH_CLIENT_ID", "").strip(),
        graph_mailbox=os.getenv("GRAPH_MAILBOX", "").strip(),
        graph_token_cache=os.getenv(
            "GRAPH_TOKEN_CACHE", ".secrets/graph_token_cache.json"
        ).strip(),
    )


SETTINGS = load_settings()
