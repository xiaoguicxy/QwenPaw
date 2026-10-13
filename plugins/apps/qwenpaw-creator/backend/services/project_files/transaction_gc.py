# -*- coding: utf-8 -*-
"""Bounded retention for converged commit transactions and their blobs.

Terminal transactions used to accumulate forever: every directory keeps its
journal, round record and four document pointers, and the content-addressed
blob store keeps each frozen document they reference.  Once a transaction has
converged (``RUNTIME_FINALIZED``/``ABORTED``), is older than the retention
window and falls outside the recent window it is pure audit history:

* startup recovery skips terminal journals without touching their files;
* the Patch idempotency path treats a missing journal as "not published yet,
  safe to re-execute" (``_recover_patch_response`` returns ``None``), so
  pruning never fabricates a replay result.

Review compensation transactions are pinned for good: decision replay
re-reads their journals and final snapshots long after they converge.

Blob sweeping runs after pruning in the same pass.  A blob is only removed
when no surviving transaction pointer references it *and* it is older than a
short grace window, so a crash between ``ContentBlobStore.put`` and the
pointer write can never delete a document an in-flight commit still needs.

Between the two, surviving legacy transactions get a format-only upgrade:
inline full-document snapshots are rewritten as blob pointers, so pre-blob
history enjoys the same deduplication and compression as new commits without
any of it being deleted.  Every reader already accepts both layouts.  The
upgrade honours a wall-clock budget and simply resumes on the next startup
when the budget runs out, so a large backlog can never stall boot.

Pruned transactions leave a compact audit record behind: the full journal
(who, when, which state, etag chain) is appended to
``runtime/retention-archive.jsonl`` before any file is deleted, and an
unwritable archive keeps the transaction on disk instead.

:func:`unmigrate_blob_transaction_snapshots` is the rollback aid: it
expands blob pointers back into inline documents for Creator versions that
predate the blob store.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from domain.errors import StorageIntegrityError
from services.runtime_files.atomic_store import (
    AtomicJsonRecordStore,
    fsync_directory,
)
from services.runtime_files.path_safety import is_link_path

from .blobs import ContentBlobStore, write_document_ref
from .commit import CommitJournalState, ProjectCommitJournal
from .retention_archive import append_retention_records, utc_now_iso

logger = logging.getLogger(__name__)

DEFAULT_KEEP_RECENT_TRANSACTIONS = 100
DEFAULT_TRANSACTION_MAX_AGE_SECONDS = 30 * 24 * 3600.0
DEFAULT_BLOB_GRACE_SECONDS = 10 * 60.0

# Only fully converged states may be pruned.  PREPARED still owes recovery a
# decision and PROJECT_REPLACED still owes Runtime pointer finalization.
_PRUNABLE_STATES = frozenset(
    {
        CommitJournalState.RUNTIME_FINALIZED,
        CommitJournalState.ABORTED,
    },
)
_POINTER_NAMES = ("base.json", "candidate.json", "latest.json", "final.json")
_BLOB_SUFFIX = ".json.gz"
_BLOB_REF_PREFIX = "sha256:"


@dataclass(frozen=True, slots=True)
class TransactionGcReport:
    pruned_transactions: int = 0
    pruned_transaction_bytes: int = 0
    removed_blobs: int = 0
    removed_blob_bytes: int = 0
    migrated_snapshots: int = 0
    migrated_inline_bytes: int = 0
    migrated_pointer_bytes: int = 0

    @property
    def empty(self) -> bool:
        return not (
            self.pruned_transactions
            or self.pruned_transaction_bytes
            or self.removed_blobs
            or self.removed_blob_bytes
            or self.migrated_snapshots
        )


def reclaim_converged_transaction_storage(
    project_root: Path,
    *,
    keep_recent: int = DEFAULT_KEEP_RECENT_TRANSACTIONS,
    max_age_seconds: float = DEFAULT_TRANSACTION_MAX_AGE_SECONDS,
    blob_grace_seconds: float = DEFAULT_BLOB_GRACE_SECONDS,
    migrate_snapshots: bool = True,
    migration_budget_seconds: float | None = None,
    now: float | None = None,
) -> TransactionGcReport:
    """Prune aged terminal transactions, upgrade survivors, then sweep blobs.

    Pruning first keeps the migration off deleted history; migrating before
    the sweep means freshly created blobs are already pointer-referenced
    (and inside the grace window anyway).
    """

    pruned, pruned_bytes = prune_terminal_transactions(
        project_root,
        keep_recent=keep_recent,
        max_age_seconds=max_age_seconds,
        now=now,
    )
    migrated, inline_bytes, pointer_bytes = (0, 0, 0)
    if migrate_snapshots:
        migrated, inline_bytes, pointer_bytes = (
            migrate_inline_transaction_snapshots(
                project_root,
                time_budget_seconds=migration_budget_seconds,
            )
        )
    removed, removed_bytes = sweep_orphan_blobs(
        project_root,
        grace_seconds=blob_grace_seconds,
        now=now,
    )
    return TransactionGcReport(
        pruned_transactions=pruned,
        pruned_transaction_bytes=pruned_bytes,
        removed_blobs=removed,
        removed_blob_bytes=removed_bytes,
        migrated_snapshots=migrated,
        migrated_inline_bytes=inline_bytes,
        migrated_pointer_bytes=pointer_bytes,
    )


def migrate_inline_transaction_snapshots(
    project_root: Path,
    *,
    time_budget_seconds: float | None = None,
) -> tuple[int, int, int]:
    """Rewrite legacy inline snapshots as blob pointers, in place.

    A format-only upgrade for transactions that predate the blob store: the
    same document is re-published through ``ContentBlobStore`` (deduplicated
    and gzip-compressed) and the snapshot file is atomically replaced by its
    ``{"blobRef": ...}`` pointer.  Journals are never touched, content is
    preserved, and the operation is idempotent because pointers are
    recognized and skipped.  Unreadable snapshots are left exactly as they
    are for the recovery audit.

    ``time_budget_seconds`` caps one pass so a large legacy backlog cannot
    stall startup; the migration is resumable because already-migrated
    files are skipped, so the next startup simply continues.  ``None``
    means migrate everything in one pass.

    Returns ``(migrated_files, inline_bytes_before, pointer_bytes_after)``.
    """

    runtime_root = project_root / "runtime"
    transactions_root = runtime_root / "transactions"
    if is_link_path(transactions_root) or not transactions_root.is_dir():
        return (0, 0, 0)
    deadline = (
        None
        if time_budget_seconds is None
        else time.monotonic() + time_budget_seconds
    )
    blobs = ContentBlobStore(runtime_root)
    migrated = 0
    inline_bytes = 0
    pointer_bytes = 0
    already_blob = 0
    paused = False
    for entry in sorted(transactions_root.iterdir()):
        if deadline is not None and time.monotonic() >= deadline:
            paused = True
            break
        if entry.name.startswith(".") or is_link_path(entry):
            continue
        try:
            entry_stat = entry.stat()
        except OSError:
            continue
        if not stat.S_ISDIR(entry_stat.st_mode):
            continue
        for name in _POINTER_NAMES:
            path = entry / name
            try:
                path_stat = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(path_stat.st_mode):
                continue
            try:
                raw = json.loads(path.read_bytes())
            except (OSError, ValueError):
                # Same policy as pruning: corrupt history stays for audit.
                logger.warning(
                    "snapshot migration skipped unreadable file: %s",
                    path,
                )
                continue
            if not isinstance(raw, dict):
                continue
            if (
                set(raw) == {"blobRef"}
                and isinstance(raw.get("blobRef"), str)
                and raw["blobRef"].startswith(_BLOB_REF_PREFIX)
            ):
                already_blob += 1
                continue  # already in blob format
            write_document_ref(blobs, path, raw)
            migrated += 1
            inline_bytes += path_stat.st_size
            try:
                pointer_bytes += path.lstat().st_size
            except OSError:
                pass
    if migrated:
        logger.info(
            "snapshot migration rewrote %d inline snapshot(s): "
            "%.1f MiB inline -> %.1f MiB pointers (plus shared blobs)",
            migrated,
            inline_bytes / (1024 * 1024),
            pointer_bytes / (1024 * 1024),
        )
    if paused:
        logger.info(
            "snapshot migration paused after its %.1fs time budget; "
            "remaining inline snapshots resume on the next startup",
            time_budget_seconds or 0.0,
        )
    elif not migrated and already_blob:
        # Settlement marker: once every snapshot is in blob format the
        # legacy-inline compatibility readers can eventually be retired.
        logger.info(
            "snapshot migration complete: all %d snapshot file(s) already "
            "in blob format",
            already_blob,
        )
    return (migrated, inline_bytes, pointer_bytes)


def unmigrate_blob_transaction_snapshots(project_root: Path) -> tuple[int, int]:
    """Expand blob-pointer snapshots back to inline documents, in place.

    Rollback aid and the exact inverse of
    :func:`migrate_inline_transaction_snapshots`: every ``{"blobRef": ...}``
    pointer is atomically replaced by the full document read back from the
    content-addressed store, so Creator versions that predate the blob store
    can read the history again.  Blob files themselves are left behind; the
    regular startup sweep removes them once no pointer references them.
    A pointer whose blob is missing or corrupt is kept untouched and
    counted as a failure — expanding never destroys anything.

    Returns ``(expanded_files, unreadable_pointers)``.
    """

    runtime_root = project_root / "runtime"
    transactions_root = runtime_root / "transactions"
    if is_link_path(transactions_root) or not transactions_root.is_dir():
        return (0, 0)
    blobs = ContentBlobStore(runtime_root)
    expanded = 0
    failed = 0
    for entry in sorted(transactions_root.iterdir()):
        if entry.name.startswith(".") or is_link_path(entry):
            continue
        try:
            entry_stat = entry.stat()
        except OSError:
            continue
        if not stat.S_ISDIR(entry_stat.st_mode):
            continue
        for name in _POINTER_NAMES:
            path = entry / name
            try:
                path_stat = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(path_stat.st_mode):
                continue
            try:
                raw = json.loads(path.read_bytes())
            except (OSError, ValueError):
                logger.warning(
                    "snapshot unmigration skipped unreadable file: %s",
                    path,
                )
                continue
            if (
                not isinstance(raw, dict)
                or set(raw) != {"blobRef"}
                or not isinstance(raw.get("blobRef"), str)
                or not raw["blobRef"].startswith(_BLOB_REF_PREFIX)
            ):
                continue  # already inline; left exactly as it is
            try:
                document = blobs.get(raw["blobRef"])
            except (OSError, ValueError, StorageIntegrityError):
                logger.warning(
                    "snapshot unmigration kept pointer, blob unreadable: %s",
                    path,
                )
                failed += 1
                continue
            AtomicJsonRecordStore(path).write(document)
            expanded += 1
    if expanded or failed:
        logger.info(
            "snapshot unmigration expanded %d pointer(s) back to inline "
            "documents (%d unreadable blob(s) kept as pointers)",
            expanded,
            failed,
        )
    return (expanded, failed)


def prune_terminal_transactions(
    project_root: Path,
    *,
    keep_recent: int = DEFAULT_KEEP_RECENT_TRANSACTIONS,
    max_age_seconds: float = DEFAULT_TRANSACTION_MAX_AGE_SECONDS,
    now: float | None = None,
) -> tuple[int, int]:
    """Delete converged transaction directories past both retention gates.

    Keeps the ``keep_recent`` newest transactions unconditionally and only
    deletes directories whose mtime is older than ``max_age_seconds``.  Any
    transaction referenced by a Review decision journal is pinned regardless
    of age.  Unreadable or non-terminal journals are left for the recovery
    audit instead of being guessed at.
    """

    runtime_root = project_root / "runtime"
    transactions_root = runtime_root / "transactions"
    if is_link_path(transactions_root) or not transactions_root.is_dir():
        return (0, 0)
    current = time.time() if now is None else now
    cutoff = current - max_age_seconds
    entries: list[tuple[float, Path]] = []
    for entry in transactions_root.iterdir():
        if entry.name.startswith(".") or is_link_path(entry):
            continue
        try:
            entry_stat = entry.stat()
        except OSError:
            continue
        if not stat.S_ISDIR(entry_stat.st_mode):
            continue
        entries.append((entry_stat.st_mtime, entry))
    if len(entries) <= keep_recent:
        return (0, 0)
    entries.sort(key=lambda item: item[0], reverse=True)
    pinned = _pinned_transaction_ids(runtime_root)
    pruned = 0
    reclaimed = 0
    for mtime, entry in entries[keep_recent:]:
        if mtime >= cutoff:
            continue
        if entry.name in pinned:
            continue
        journal = AtomicJsonRecordStore(
            entry / "journal.json",
            ProjectCommitJournal,
        ).read_or_none()
        if journal is None or journal.state not in _PRUNABLE_STATES:
            continue
        size_bytes = _tree_bytes(entry)
        try:
            append_retention_records(
                runtime_root,
                (
                    {
                        "kind": "transaction-pruned",
                        "transactionId": entry.name,
                        "prunedAt": utc_now_iso(),
                        "directoryBytes": size_bytes,
                        "journal": journal.model_dump(mode="json"),
                    },
                ),
            )
        except OSError:
            # No audit record, no deletion: the journal is the only copy of
            # this transaction's history, so keep it until the archive
            # becomes writable again.
            logger.warning(
                "transaction retention kept %s: audit archive unwritable",
                entry.name,
            )
            continue
        # entry was verified to be a real directory directly inside the
        # transactions root; rmtree never follows symlinked children.
        shutil.rmtree(entry, ignore_errors=False)
        pruned += 1
        reclaimed += size_bytes
        logger.info(
            "transaction retention pruned converged transaction: %s",
            entry.name,
        )
    if pruned:
        fsync_directory(transactions_root)
    return (pruned, reclaimed)


def sweep_orphan_blobs(
    project_root: Path,
    *,
    grace_seconds: float = DEFAULT_BLOB_GRACE_SECONDS,
    now: float | None = None,
) -> tuple[int, int]:
    """Remove blob files no transaction pointer references anymore.

    Fails towards retention: if any pointer file cannot be parsed the whole
    sweep is skipped for this pass, because the unreadable pointer may still
    reference blobs that must survive.
    """

    runtime_root = project_root / "runtime"
    blobs_root = runtime_root / "blobs"
    if is_link_path(blobs_root) or not blobs_root.is_dir():
        return (0, 0)
    live = _live_blob_digests(runtime_root)
    if live is None:
        logger.warning(
            "blob sweep skipped: a transaction pointer could not be parsed",
        )
        return (0, 0)
    current = time.time() if now is None else now
    cutoff = current - grace_seconds
    removed = 0
    reclaimed = 0
    for bucket in blobs_root.iterdir():
        if is_link_path(bucket) or not bucket.is_dir():
            continue
        for blob in bucket.iterdir():
            if is_link_path(blob) or not blob.name.endswith(_BLOB_SUFFIX):
                continue
            digest = blob.name[: -len(_BLOB_SUFFIX)]
            if digest in live:
                continue
            try:
                blob_stat = blob.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(blob_stat.st_mode):
                continue
            if blob_stat.st_mtime >= cutoff:
                # A very recent blob may belong to a commit whose pointer
                # file has not been written yet.
                continue
            blob.unlink(missing_ok=True)
            removed += 1
            reclaimed += blob_stat.st_size
        try:
            bucket.rmdir()
        except OSError:
            pass
    if removed:
        fsync_directory(blobs_root)
        logger.info(
            "blob sweep removed %d orphan blob(s), %.1f MiB",
            removed,
            reclaimed / (1024 * 1024),
        )
    return (removed, reclaimed)


def _pinned_transaction_ids(runtime_root: Path) -> set[str]:
    """Transaction IDs any Review decision journal may still consult."""

    pinned: set[str] = set()
    reviews_root = runtime_root / "reviews"
    if is_link_path(reviews_root) or not reviews_root.is_dir():
        return pinned
    pattern = os.path.join("*", "decision-transactions", "*", "journal.json")
    for decision_journal in reviews_root.glob(pattern):
        try:
            raw = json.loads(decision_journal.read_bytes())
        except (OSError, ValueError):
            # A corrupt decision journal already fails decision replay on
            # its own; it cannot widen what retention is allowed to delete.
            continue
        if not isinstance(raw, dict):
            continue
        transaction_id = raw.get("compensation_transaction_id")
        if isinstance(transaction_id, str) and transaction_id:
            pinned.add(transaction_id)
    return pinned


def _live_blob_digests(runtime_root: Path) -> set[str] | None:
    """Digests referenced by transaction pointers; ``None`` aborts the sweep."""

    live: set[str] = set()
    transactions_root = runtime_root / "transactions"
    if is_link_path(transactions_root) or not transactions_root.is_dir():
        return live
    for entry in transactions_root.iterdir():
        if entry.name.startswith(".") or is_link_path(entry):
            continue
        for name in _POINTER_NAMES:
            pointer = entry / name
            try:
                pointer_stat = pointer.lstat()
            except (FileNotFoundError, OSError):
                continue
            if not stat.S_ISREG(pointer_stat.st_mode):
                continue
            try:
                raw = json.loads(pointer.read_bytes())
            except (OSError, ValueError):
                return None
            if (
                isinstance(raw, dict)
                and set(raw) == {"blobRef"}
                and isinstance(raw.get("blobRef"), str)
                and raw["blobRef"].startswith(_BLOB_REF_PREFIX)
            ):
                live.add(raw["blobRef"][len(_BLOB_REF_PREFIX) :])
    return live


def _tree_bytes(root: Path) -> int:
    total = 0
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name
            for name in dirnames
            if not is_link_path(Path(directory) / name)
        ]
        for name in filenames:
            candidate = Path(directory) / name
            try:
                candidate_stat = candidate.lstat()
            except OSError:
                continue
            if stat.S_ISREG(candidate_stat.st_mode):
                total += candidate_stat.st_size
    return total
