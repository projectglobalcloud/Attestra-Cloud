"""
Looking inside a dataset.

A custody platform that will not let the holder see its own data is a strange
thing to ask anyone to trust, so this reads the head of the stored file and
describes it: a table for delimited data, the text itself for text, and the
file served back for anything a browser can render — an image, a video, a PDF.
Nothing here changes a byte, and nothing is loaded whole: a preview reads a
capped window from the front of the file, whatever its size.

Who may look is decided elsewhere (services/access.py). The answer is the party
that holds the dataset, and the registry. A buyer who has not bought it yet
gets the listing's description, not the data — the whole point of the platform
is that ownership moves without the data being copied around.
"""

from __future__ import annotations

import csv
import io
import json
import mimetypes
from pathlib import Path

# How much of a file a preview may read, and how much of it to show.
HEAD_BYTES = 512 * 1024
MAX_ROWS = 60
MAX_TEXT_LINES = 200
MAX_CELL = 200

TEXT_SUFFIXES = {".csv", ".tsv", ".txt", ".json", ".jsonl", ".ndjson", ".md",
                 ".log", ".xml", ".yaml", ".yml", ".ini", ".cfg", ".py", ".sql"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif"}
VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".m4v", ".ogv"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".ogg", ".m4a", ".flac"}


def _human(size: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:,.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def _sniff_kind(path: Path, head: bytes) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in AUDIO_SUFFIXES:
        return "audio"
    if suffix == ".pdf" or head[:5] == b"%PDF-":
        return "pdf"
    if suffix in {".csv", ".tsv"}:
        return "table"
    if suffix in {".json", ".jsonl", ".ndjson"}:
        return "json"
    if suffix in TEXT_SUFFIXES:
        return "text"
    # No useful extension: decide by what the bytes look like. A NUL in the
    # first block is the oldest and still the best binary test there is.
    if b"\x00" in head[:8192]:
        if head[:8] in (b"\x89PNG\r\n\x1a\n",) or head[:3] == b"\xff\xd8\xff":
            return "image"
        return "binary"
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        return "binary"
    first = text.splitlines()[0] if text.splitlines() else ""
    if first.count(",") >= 2:
        return "table"
    return "text"


def _read_head(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(HEAD_BYTES)


def _table(head: bytes, delimiter: str = ",") -> dict:
    """Header and the first rows, from the window we read."""
    text = head.decode("utf-8", errors="replace")
    # The last line of a capped read is usually cut in half; drop it.
    lines = text.splitlines()
    if len(lines) > 1 and not text.endswith("\n"):
        lines = lines[:-1]
    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)
    rows = []
    for row in reader:
        rows.append([(cell[:MAX_CELL] + "…") if len(cell) > MAX_CELL else cell
                     for cell in row])
        if len(rows) > MAX_ROWS:
            break
    if not rows:
        return {"columns": [], "rows": []}
    header, body = rows[0], rows[1:MAX_ROWS]
    # A header is only a header if it looks like one rather than like data.
    looks_like_header = all(cell and not cell.replace(".", "", 1).lstrip("-").isdigit()
                            for cell in header)
    if not looks_like_header:
        body = rows[:MAX_ROWS]
        header = [f"column {i + 1}" for i in range(len(rows[0]))]
    return {"columns": header, "rows": body}


def describe(path: Path | str, *, total_bytes: int | None = None) -> dict:
    """What this file is, and as much of it as is safe to show."""
    path = Path(path)
    if not path.exists():
        return {"kind": "missing",
                "note": "the stored file is not on this server any more"}

    size = total_bytes if total_bytes is not None else path.stat().st_size
    head = _read_head(path)
    kind = _sniff_kind(path, head)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    out = {
        "kind": kind,
        "filename": path.name,
        "mime": mime,
        "bytes": size,
        "size_display": _human(size),
        "truncated": size > len(head),
        "read_bytes": len(head),
    }

    if kind == "table":
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        table = _table(head, delimiter)
        out.update(table)
        out["shown_rows"] = len(table["rows"])
    elif kind == "json":
        text = head.decode("utf-8", errors="replace")
        try:
            out["text"] = json.dumps(json.loads(text), indent=2)[:60_000]
            out["kind"] = "text"
        except ValueError:
            # A truncated document, or JSON Lines: show it as it reads.
            out["text"] = "\n".join(text.splitlines()[:MAX_TEXT_LINES])
            out["kind"] = "text"
    elif kind == "text":
        text = head.decode("utf-8", errors="replace")
        lines = text.splitlines()[:MAX_TEXT_LINES]
        out["text"] = "\n".join(lines)
        out["shown_lines"] = len(lines)
    elif kind == "binary":
        window = head[:512]
        rows = []
        for offset in range(0, len(window), 16):
            chunk = window[offset:offset + 16]
            hexed = " ".join(f"{b:02x}" for b in chunk)
            ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            rows.append(f"{offset:08x}  {hexed:<47}  {ascii_}")
        out["text"] = "\n".join(rows)
        out["note"] = ("This file is not text, and not a format the browser can "
                       "display. The first 512 bytes are shown as they are stored.")
    # image / video / audio / pdf are served by the file endpoint instead.
    return out
