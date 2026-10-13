# -*- coding: utf-8 -*-
"""Content-addressed compressed storage for frozen Project documents.

Transaction snapshots (base/candidate/latest/final) used to embed the full
pretty-printed Project JSON in every transaction, so a commit chain stored
the same document over and over.  Documents now live once, gzip-compressed,
under ``runtime/blobs/<xx>/<sha256>.json.gz``; transaction directories keep
tiny ``{"blobRef": ...}`` pointers instead.  Readers accept both layouts so
pre-migration transactions stay recoverable and auditable.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from domain.errors import StorageIntegrityError
from services.runtime_files.atomic_store import (
    AtomicJsonRecordStore,
    canonical_json_bytes,
    fsync_directory,
    strict_json_loads,
)
from services.runtime_files.errors import RecordNotFoundError

_BLOB_REF = re.compile(r"sha256:[0-9a-f]{64}")


class ContentBlobStore:
    """Immutable, deduplicated, compressed JSON objects below one Runtime."""

    def __init__(self, runtime_root: Path) -> None:
        self.root = runtime_root / "blobs"

    def put(self, document: Mapping[str, Any]) -> str:
        """Store one document; identical content is stored exactly once."""

        payload = canonical_json_bytes(document)
        digest = hashlib.sha256(payload).hexdigest()
        path = self._path(digest)
        if path.exists():
            return f"sha256:{digest}"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(gzip.compress(payload, 6))
                handle.flush()
                os.fsync(handle.fileno())
            # Concurrent writers produce byte-identical payloads, so losing
            # the rename race still leaves the correct blob in place.
            os.replace(temporary, path)
            fsync_directory(path.parent)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return f"sha256:{digest}"

    def get(self, ref: str) -> dict[str, Any]:
        """Read one document back, failing closed on any content drift."""

        if _BLOB_REF.fullmatch(ref) is None:
            raise StorageIntegrityError(
                f"Project blob ref is malformed: {ref!r}",
            )
        digest = ref.removeprefix("sha256:")
        payload = gzip.decompress(self._path(digest).read_bytes())
        actual = hashlib.sha256(payload).hexdigest()
        if actual != digest:
            raise StorageIntegrityError(
                f"Project blob content mismatch: {ref}",
            )
        value = strict_json_loads(payload)
        if not isinstance(value, dict):
            raise StorageIntegrityError(
                f"Project blob must be one JSON object: {ref}",
            )
        return value

    def _path(self, digest: str) -> Path:
        return self.root / digest[:2] / f"{digest}.json.gz"


def write_document_ref(
    store: ContentBlobStore,
    path: Path,
    document: Mapping[str, Any],
) -> str:
    """Persist one frozen document as a blob plus a tiny pointer record."""

    ref = store.put(document)
    AtomicJsonRecordStore(path).write({"blobRef": ref})
    return ref


def read_document_ref(store: ContentBlobStore, path: Path) -> Any:
    """Read a transaction document in either the blob or legacy layout."""

    return _resolve(store, AtomicJsonRecordStore(path).read())


def read_document_ref_or_none(store: ContentBlobStore, path: Path) -> Any:
    try:
        raw = AtomicJsonRecordStore(path).read()
    except RecordNotFoundError:
        return None
    return _resolve(store, raw)


def _resolve(store: ContentBlobStore, raw: Any) -> Any:
    # A real Project document always carries schema_version and friends, so
    # a single-key blobRef record can only be one of our own pointers.
    if (
        isinstance(raw, Mapping)
        and set(raw) == {"blobRef"}
        and isinstance(raw.get("blobRef"), str)
    ):
        return store.get(raw["blobRef"])
    return raw
