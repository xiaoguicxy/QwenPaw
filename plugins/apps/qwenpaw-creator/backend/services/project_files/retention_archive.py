# -*- coding: utf-8 -*-
"""Append-only audit trail for retention deletions.

Bounded retention makes storage finite by *deleting* converged history, and
the deleted bytes are gone for good.  This module keeps the part that must
not be lost: a compact JSONL record of every deletion, so "what existed,
when it converged and when retention removed it" survives indefinitely at
kilobyte cost (real journals average ~1.2 KiB).

The archive is deliberately dumb: one JSON object per line, appended with
``O_NOFOLLOW`` plus fsync, gzip-rotated past a size threshold.  Failure
policies differ per caller by design:

* transaction pruning treats an unwritable archive as a reason to keep the
  transaction (fail towards retention — the journal is the only copy of
  that history);
* asset-version pruning logs and proceeds, because its payloads are already
  de-indexed by the CAS authority swap and keeping them would leak orphan
  files forever.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ARCHIVE_NAME = "retention-archive.jsonl"
ROTATE_THRESHOLD_BYTES = 4 * 1024 * 1024
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_retention_records(
    runtime_root: Path,
    records: Iterable[Mapping[str, Any]],
) -> int:
    """Append audit records to the Runtime archive; returns lines written.

    Raises ``OSError`` when the archive cannot be appended (including when
    the path is a symlink) so transaction retention can fail towards
    retention.
    """

    lines = [
        json.dumps(
            dict(record),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for record in records
    ]
    if not lines:
        return 0
    archive = runtime_root / ARCHIVE_NAME
    descriptor = os.open(
        archive,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | _NOFOLLOW,
        0o600,
    )
    try:
        handle = os.fdopen(descriptor, "a", encoding="utf-8")
    except BaseException:
        os.close(descriptor)
        raise
    with handle:
        handle.write("".join(f"{line}\n" for line in lines))
        handle.flush()
        os.fsync(handle.fileno())
    _rotate_if_needed(archive)
    return len(lines)


def _rotate_if_needed(archive: Path) -> None:
    """Gzip the live archive once it grows past the threshold.

    Best-effort: the records are already durable in the live file, so a
    failed rotation only means it keeps growing.  The compressed copy is
    complete before the live file is truncated; a crash in between
    duplicates records, which an audit trail tolerates.
    """

    try:
        size = os.path.getsize(archive)
    except OSError:
        return
    if size <= ROTATE_THRESHOLD_BYTES:
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    rotated = archive.with_name(f"retention-archive-{stamp}.jsonl.gz")
    try:
        source_descriptor = os.open(archive, os.O_RDONLY | _NOFOLLOW)
        try:
            source = os.fdopen(source_descriptor, "rb")
        except BaseException:
            os.close(source_descriptor)
            raise
        with source, gzip.open(rotated, "wb", compresslevel=6) as target:
            shutil.copyfileobj(source, target, 1024 * 1024)
        truncate_descriptor = os.open(archive, os.O_WRONLY | _NOFOLLOW)
        try:
            os.ftruncate(truncate_descriptor, 0)
            os.fsync(truncate_descriptor)
        finally:
            os.close(truncate_descriptor)
    except OSError:
        rotated.unlink(missing_ok=True)
        logger.warning(
            "retention archive rotation failed; keeping %s",
            archive.name,
        )
        return
    logger.info(
        "rotated retention archive to %s (%.1f MiB)",
        rotated.name,
        size / (1024 * 1024),
    )
