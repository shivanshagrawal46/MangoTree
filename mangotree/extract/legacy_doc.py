"""Text extraction for legacy binary Word documents (Word 97-2003 ``.doc``).

Why this module exists
----------------------
`python-docx` reads OOXML only. A real `.doc` is an OLE2 compound file with an
entirely different internal structure, so python-docx fails on it with a
misleading complaint about content types. Seven attachments in this corpus are
genuine OLE2 documents — deeds of trust, promissory notes, a chain of title, a
legal description — and Outlook mail will keep producing them, because title
companies and attorneys still send `.doc`.

No LibreOffice or antiword is installed here, so conversion by subprocess is not
available. This is a direct reader.

How the format actually works
-----------------------------
Text is not stored contiguously. The `WordDocument` stream holds a File
Information Block (FIB) which points at a *piece table* living in a separate
`0Table`/`1Table` stream. The piece table lists runs ("pieces") of text, each
with a file offset and a flag saying whether that run is compressed CP1252 or
raw UTF-16LE — a single document routinely mixes both. Walking the piece table is
the only way to recover the text in the right order with the right encoding.

The crude alternative — scanning the stream for printable byte runs — produces
text in near-arbitrary order, interleaved with deleted revisions and field
codes. For a promissory note, out-of-order text with stale revisions silently
mixed in is worse than no text: it reads as authoritative and is wrong. So the
piece table is the primary path, the crude scan is a clearly-labelled fallback,
and which one produced the text is recorded on the artifact.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from mangotree.core.logging import logger

OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

#: FIB field offsets within the WordDocument stream (Word 97 and later).
_FIB_FLAGS = 0x000A          # fWhichTblStm lives here
_FIB_FC_CLX = 0x01A2         # offset of the piece table in the Table stream
_FIB_LCB_CLX = 0x01A6        # its length
_FLAG_WHICH_TABLE = 0x0200   # set => "1Table", clear => "0Table"

#: Piece-table markers.
_CLXT_PRC = 0x01   # a property modifier block, skipped
_CLXT_PCDT = 0x02  # the piece descriptor table itself

#: Set in a piece's fc when the run is 8-bit CP1252 rather than UTF-16LE.
_FC_COMPRESSED = 0x40000000

#: Word's in-band control characters. \x07 is a cell/row mark, \x0b a line break,
#: \x0c a page break, \x13-\x15 delimit field codes whose *result* is what a
#: reader sees, \x01 an embedded object placeholder.
_CONTROL = {
    "\x00": "", "\x01": "", "\x02": "", "\x05": "", "\x08": "",
    "\x07": "\t", "\x0b": "\n", "\x0c": "\n\n", "\x0e": "", "\x0f": "",
    "\x13": "", "\x14": "", "\x15": "", "\x1e": "-", "\x1f": "",
    "\r": "\n",
}


@dataclass
class LegacyDocResult:
    text: str
    method: str
    pieces: int = 0
    confidence: float = 1.0
    warnings: List[str] = None

    def __post_init__(self) -> None:
        if self.warnings is None:
            self.warnings = []


def is_legacy_doc(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(8) == OLE2_MAGIC
    except Exception:
        return False


def _clean(raw: str) -> str:
    out = []
    for char in raw:
        if char in _CONTROL:
            out.append(_CONTROL[char])
        elif ord(char) < 0x20 and char not in "\n\t":
            continue
        else:
            out.append(char)
    text = "".join(out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{3,}", "  ", text)
    return text.strip()


def _read_piece_table(table: bytes, fc_clx: int, lcb_clx: int) -> List[Tuple[int, int, bool]]:
    """Return [(start_cp, end_cp, is_compressed_with_fc)] as (fc, length, compressed).

    The CLX is a run of optional Prc blocks followed by exactly one Pcdt. Prc
    blocks carry formatting and must be stepped over by their declared length —
    they are variable-sized, so they cannot be skipped by assumption.
    """
    clx = table[fc_clx: fc_clx + lcb_clx]
    cursor = 0
    while cursor < len(clx):
        marker = clx[cursor]
        if marker == _CLXT_PRC:
            if cursor + 3 > len(clx):
                break
            cb_grpprl = struct.unpack_from("<H", clx, cursor + 1)[0]
            cursor += 3 + cb_grpprl
            continue
        if marker == _CLXT_PCDT:
            lcb = struct.unpack_from("<I", clx, cursor + 1)[0]
            plc = clx[cursor + 5: cursor + 5 + lcb]
            return _parse_plcpcd(plc)
        # Anything else means the structure is not what we think it is; stopping
        # is better than walking off into arbitrary bytes.
        break
    return []


def _parse_plcpcd(plc: bytes) -> List[Tuple[int, int, bool]]:
    """PlcPcd: (n+1) character positions, then n eight-byte piece descriptors."""
    if len(plc) < 4 + 8:
        return []
    # n from the total size: (n+1)*4 CPs + n*8 PCDs == len
    n = (len(plc) - 4) // 12
    if n <= 0:
        return []

    cps = [struct.unpack_from("<I", plc, i * 4)[0] for i in range(n + 1)]
    pieces: List[Tuple[int, int, bool]] = []
    pcd_base = (n + 1) * 4
    for i in range(n):
        offset = pcd_base + i * 8
        if offset + 8 > len(plc):
            break
        fc_raw = struct.unpack_from("<I", plc, offset + 2)[0]
        compressed = bool(fc_raw & _FC_COMPRESSED)
        fc = fc_raw & ~_FC_COMPRESSED
        if compressed:
            # A compressed run stores one byte per character, and its recorded
            # fc is doubled. Halving it is not an optimisation; the offset is
            # simply wrong otherwise.
            fc //= 2
        char_count = cps[i + 1] - cps[i]
        byte_len = char_count if compressed else char_count * 2
        pieces.append((fc, byte_len, compressed))
    return pieces


def extract_legacy_doc(path: Path) -> LegacyDocResult:
    """Read a Word 97-2003 document. Piece table first, crude scan as a fallback."""
    import olefile

    if not olefile.isOleFile(str(path)):
        return LegacyDocResult(text="", method="not_ole2", confidence=0.0,
                               warnings=["file is not an OLE2 compound document"])

    with olefile.OleFileIO(str(path)) as ole:
        streams = {"/".join(entry) for entry in ole.listdir()}
        if "WordDocument" not in streams:
            return LegacyDocResult(
                text="", method="no_word_stream", confidence=0.0,
                warnings=[f"no WordDocument stream; streams present: {sorted(streams)[:10]}"],
            )

        word_stream = ole.openstream("WordDocument").read()

        table_name: Optional[str] = None
        try:
            flags = struct.unpack_from("<H", word_stream, _FIB_FLAGS)[0]
            preferred = "1Table" if (flags & _FLAG_WHICH_TABLE) else "0Table"
            for candidate in (preferred, "1Table", "0Table"):
                if candidate in streams:
                    table_name = candidate
                    break
        except Exception as exc:
            logger.debug("FIB flag read failed for %s: %s", path.name, exc)

        if table_name:
            try:
                table = ole.openstream(table_name).read()
                fc_clx = struct.unpack_from("<I", word_stream, _FIB_FC_CLX)[0]
                lcb_clx = struct.unpack_from("<I", word_stream, _FIB_LCB_CLX)[0]
                if 0 < lcb_clx and fc_clx + lcb_clx <= len(table):
                    pieces = _read_piece_table(table, fc_clx, lcb_clx)
                    if pieces:
                        chunks = []
                        for fc, byte_len, compressed in pieces:
                            raw = word_stream[fc: fc + byte_len]
                            if not raw:
                                continue
                            if compressed:
                                chunks.append(raw.decode("cp1252", errors="replace"))
                            else:
                                chunks.append(raw.decode("utf-16-le", errors="replace"))
                        text = _clean("".join(chunks))
                        if text:
                            return LegacyDocResult(
                                text=text, method="ole2_piece_table",
                                pieces=len(pieces), confidence=1.0,
                            )
            except Exception as exc:
                logger.debug("Piece-table read failed for %s: %s", path.name, exc)

    # Fallback. Labelled honestly and given a low confidence, because ordering
    # and revision-cleanliness are not guaranteed by this path.
    text = _crude_scan(word_stream)
    if text:
        return LegacyDocResult(
            text=text, method="ole2_crude_scan", confidence=0.45,
            warnings=["piece table unreadable; text order and completeness are "
                      "not guaranteed and deleted revisions may be included"],
        )
    return LegacyDocResult(text="", method="ole2_failed", confidence=0.0,
                           warnings=["no text recovered by either path"])


def _crude_scan(stream: bytes, min_run: int = 6) -> str:
    """Printable runs, both CP1252 and UTF-16LE. Order is not trustworthy."""
    found: List[str] = []

    for match in re.finditer(rb"(?:[\x20-\x7e]{%d,})" % min_run, stream):
        found.append(match.group().decode("cp1252", errors="replace"))

    utf16 = re.finditer(rb"(?:[\x20-\x7e]\x00){%d,}" % min_run, stream)
    for match in utf16:
        found.append(match.group().decode("utf-16-le", errors="replace"))

    # Word stores internal machinery as ordinary strings; without dropping it the
    # output is mostly font tables and style names.
    noise = re.compile(
        r"^(?:Microsoft|Word\.Document|MSWordDoc|Times New Roman|Arial|Calibri|"
        r"Cambria|Symbol|Wingdings|Normal\.dot|Root Entry|WordDocument|"
        r"SummaryInformation|DocumentSummaryInformation|CompObj|ObjectPool|"
        r"[\d\s\.\-_]+)$",
        re.I,
    )
    kept = []
    seen = set()
    for item in found:
        cleaned = item.strip()
        if len(cleaned) < min_run or noise.match(cleaned):
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        kept.append(cleaned)

    return _clean("\n".join(kept))
