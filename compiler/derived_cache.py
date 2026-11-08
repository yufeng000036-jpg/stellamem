"""Hybrid Write-Markdown / Read-Derived-Index Cache for Stellamem.

Solves the I/O and Token linear inflation bottleneck of pure-file architectures:
- Authoritative storage remains 100% human-readable Markdown and JSONL files.
- Reads check `(mtime_ns, size)` fingerprints against an in-memory & disk-backed
  lightweight derived index (`.derived_index_cache.json`).
- Unchanged Markdown files are never re-parsed or re-tokenized during recall.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from compiler.file_lock import atomic_write_text


@dataclass(frozen=True)
class FileFingerprint:
    rel_path: str
    mtime_ns: int
    size: int
    sha256: str


class DerivedMarkdownCache:
    """Incremental cache over Markdown memory files and version-chain ledgers."""

    def __init__(
        self, root_dir: Path | str, cache_filename: str = ".derived_index_cache.json"
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.cache_path = self.root_dir / cache_filename
        self._entries: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self._stats = {"hits": 0, "misses": 0, "evictions": 0}
        self._load_cache()

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    def _load_cache(self) -> None:
        if not self.cache_path.exists():
            return
        try:
            raw = self.cache_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("version") == 1:
                entries = data.get("entries", {})
                if isinstance(entries, dict):
                    self._entries = entries
        except (OSError, json.JSONDecodeError):
            self._entries = {}

    def flush(self) -> None:
        """Persist updated derived index entries to disk atomically."""
        if not self._dirty:
            return
        payload = json.dumps(
            {"version": 1, "entries": self._entries},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        atomic_write_text(self.cache_path, payload + "\n")
        self._dirty = False

    def get_or_parse(
        self,
        md_path: Path | str,
        parser_fn: Callable[[str, Path], dict[str, Any]],
    ) -> dict[str, Any]:
        """Return parsed metadata/summary for a Markdown file without re-reading if unchanged."""
        path = Path(md_path).resolve()
        try:
            rel_key = path.relative_to(self.root_dir).as_posix()
        except ValueError:
            rel_key = path.as_posix()

        st = path.stat()
        mtime_ns = st.st_mtime_ns
        size = st.st_size

        cached = self._entries.get(rel_key)
        text: str | None = None
        if (
            cached is not None
            and cached.get("mtime_ns") == mtime_ns
            and cached.get("size") == size
            and isinstance(cached.get("parsed"), dict)
        ):
            # Fast-path hit, but verify the content hash before trusting it.
            # mtime_ns + size alone can lie on coarse-resolution filesystems
            # or when timestamps are cloned; a sha256 read is cheap compared
            # to re-parsing (which may cost LLM tokens).
            text = path.read_text(encoding="utf-8")
            if hashlib.sha256(text.encode("utf-8")).hexdigest() == cached.get("sha256"):
                self._stats["hits"] += 1
                return cached["parsed"]
            # Hash mismatch: fall through to re-parse below (text already read).

        self._stats["misses"] += 1
        if text is None:
            text = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

        if (
            cached is not None
            and cached.get("sha256") == digest
            and isinstance(cached.get("parsed"), dict)
        ):
            cached["mtime_ns"] = mtime_ns
            cached["size"] = size
            self._dirty = True
            return cached["parsed"]

        parsed = parser_fn(text, path)
        self._entries[rel_key] = {
            "mtime_ns": mtime_ns,
            "size": size,
            "sha256": digest,
            "parsed": parsed,
        }
        self._dirty = True
        return parsed

    def invalidate(self, paths) -> int:
        """Drop cache entries for the given (root-relative) paths.

        Used by the deletion chain: after a file is modified, its
        cached parse must go now, not on the next read (the cache
        would self-heal via its content hash then, but a deletion
        must not wait for a read).
        """
        removed = 0
        for p in paths:
            key = Path(p).as_posix()
            if key in self._entries:
                del self._entries[key]
                removed += 1
        if removed:
            self._stats["evictions"] += removed
            self._dirty = True
            self.flush()
        return removed

    def prune_deleted(self, existing_paths: set[Path]) -> int:
        """Remove cache entries whose authoritative Markdown files were deleted."""
        valid_keys: set[str] = set()
        for p in existing_paths:
            resolved = p.resolve()
            try:
                valid_keys.add(resolved.relative_to(self.root_dir).as_posix())
            except ValueError:
                valid_keys.add(resolved.as_posix())

        removed = 0
        for key in list(self._entries.keys()):
            if key not in valid_keys:
                del self._entries[key]
                removed += 1
        if removed:
            self._stats["evictions"] += removed
            self._dirty = True
        return removed
