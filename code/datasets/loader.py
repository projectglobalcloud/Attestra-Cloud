"""
Turning a real dataset file into protocol blocks.

This is the bridge between "a 100 MB CSV on disk" and "n elements of Z_q that
the scheme can tag".  It is short, but it is where two decisions that shape the
whole system's performance are made, so both are spelled out.

DECISION 1 -- BLOCK SIZE
    The file is cut into fixed-size blocks. n = ceil(filesize / block_size).
    Since tagging costs O(n*s) exponentiations, block size is the single knob
    controlling how long it takes to prepare a dataset.  See config.py for why
    this does not weaken the security guarantee.

DECISION 2 -- HOW A BLOCK BECOMES AN ELEMENT OF Z_q
    A BLS12-381 scalar holds ~255 bits, so a 256 KB block cannot be embedded
    directly.  Two modes:

      "hashed"  m_j = SHA-512(block) mod q.
                Works for any block size.  Soundness is preserved because
                computing SHA-512(block) still requires POSSESSING the block:
                a cloud that deleted the data cannot produce m_j, so it cannot
                produce a valid DP.  This is the default.

      "raw"     m_j = int(block) mod q, for blocks of <= 31 bytes.
                What the base paper uses (8-byte blocks).  Used by the
                benchmark suite so our numbers are comparable to the paper's.

    The tag binds m_j, not the raw bytes, in both modes -- the algebra is
    identical.  Only the map from bytes to Z_q differs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from config import BLOCK_MODE, DEFAULT_BLOCK_SIZE, DATASET_DIR
from crypto.hashes import hash_block
from crypto.pairing import CURVE_ORDER

__all__ = [
    "FileChunks",
    "chunk_file",
    "chunk_bytes",
    "load_manifest",
    "list_datasets",
    "dataset_shards",
    "file_abstract_for",
]

# A block larger than this cannot be embedded directly in Z_q.
MAX_RAW_BLOCK_SIZE = 31


@dataclass
class FileChunks:
    """A file, cut into blocks and reduced to Z_q."""

    file_id: str
    path: Path | None
    file_abstract: bytes
    blocks: list[int]
    block_size: int
    mode: str
    total_bytes: int

    @property
    def n_blocks(self) -> int:
        return len(self.blocks)

    def __str__(self) -> str:
        mb = self.total_bytes / 1024 / 1024
        return (
            f"{self.file_id}: {mb:.2f} MB -> {self.n_blocks} blocks "
            f"of {self.block_size} B ({self.mode} mode)"
        )


def _to_scalar(block: bytes, mode: str) -> int:
    """Map one block of bytes to an element of Z_q."""
    if mode == "hashed":
        return hash_block(block)
    if mode == "raw":
        if len(block) > MAX_RAW_BLOCK_SIZE:
            raise ValueError(
                f"'raw' mode needs blocks of at most {MAX_RAW_BLOCK_SIZE} bytes "
                f"so the value fits in Z_q without wrapping; got {len(block)}. "
                f"Use 'hashed' mode for real files."
            )
        return int.from_bytes(block, "big") % CURVE_ORDER
    raise ValueError(f"unknown block mode {mode!r}; expected 'hashed' or 'raw'")


def file_abstract_for(file_id: str, total_bytes: int, block_size: int, mode: str) -> bytes:
    """
    Build the file abstract F.

    F is bound into every tag via H3(F||j), which stops a tag computed for one
    file from being replayed as a tag for another.  It therefore has to commit
    to everything that identifies the file AND to how it was cut up -- two
    different chunkings of the same bytes are different files as far as the
    protocol is concerned.
    """
    payload = f"{file_id}|{total_bytes}|{block_size}|{mode}".encode()
    return hashlib.sha256(payload).digest()


def chunk_bytes(
    data: bytes,
    file_id: str,
    block_size: int = DEFAULT_BLOCK_SIZE,
    mode: str = BLOCK_MODE,
) -> FileChunks:
    """Chunk an in-memory buffer. Used by tests and small demos."""
    blocks = [
        _to_scalar(data[i:i + block_size], mode)
        for i in range(0, len(data), block_size)
    ]
    return FileChunks(
        file_id=file_id,
        path=None,
        file_abstract=file_abstract_for(file_id, len(data), block_size, mode),
        blocks=blocks,
        block_size=block_size,
        mode=mode,
        total_bytes=len(data),
    )


def chunk_file(
    path: Path,
    file_id: str | None = None,
    block_size: int = DEFAULT_BLOCK_SIZE,
    mode: str = BLOCK_MODE,
    max_blocks: int | None = None,
) -> FileChunks:
    """
    Chunk a file from disk, streaming so that a 100 MB shard never has to be
    held in memory in full.

    `max_blocks` truncates to the first N blocks. That is a demo convenience
    for keeping a live walkthrough short -- it is NOT a protocol feature, and
    the file abstract records the real total so a truncated run can never be
    confused with a full one.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"no such dataset file: {path}")

    file_id = file_id or path.name
    total_bytes = path.stat().st_size

    blocks: list[int] = []
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(block_size)
            if not chunk:
                break
            blocks.append(_to_scalar(chunk, mode))
            if max_blocks is not None and len(blocks) >= max_blocks:
                break

    return FileChunks(
        file_id=file_id,
        path=path,
        file_abstract=file_abstract_for(file_id, total_bytes, block_size, mode),
        blocks=blocks,
        block_size=block_size,
        mode=mode,
        total_bytes=total_bytes,
    )


# --------------------------------------------------------------------------
# Navigating generated datasets
# --------------------------------------------------------------------------

def list_datasets(root: Path = DATASET_DIR) -> list[str]:
    """Names of every generated dataset found on disk."""
    root = Path(root)
    if not root.exists():
        return []
    return sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and (p / "manifest.json").exists()
    )


def load_manifest(name: str, root: Path = DATASET_DIR) -> dict:
    """Read a dataset's manifest, with a helpful error if it is missing."""
    manifest_path = Path(root) / name / "manifest.json"
    if not manifest_path.exists():
        available = list_datasets(root)
        hint = (
            f" Available: {', '.join(available)}." if available
            else " No datasets generated yet -- run: python -m datasets.generate"
        )
        raise FileNotFoundError(f"no manifest for dataset {name!r}.{hint}")
    return json.loads(manifest_path.read_text())


def dataset_shards(name: str, root: Path = DATASET_DIR) -> Iterator[Path]:
    """Yield each shard file of a dataset, in order."""
    manifest = load_manifest(name, root)
    base = Path(root) / name
    for shard in manifest["shards"]:
        yield base / shard["path"]
