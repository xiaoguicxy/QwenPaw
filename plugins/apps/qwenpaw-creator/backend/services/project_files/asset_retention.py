# -*- coding: utf-8 -*-
"""Bounded retention for superseded artifact versions.

Every media re-render appends a new ``ArtifactVersion`` to its slot and the
payload of each superseded version stays on disk forever, so a long-lived
project accumulates gigabytes of ``final_video`` history nobody can reach
from the UI anymore.  This module prunes versions that are provably dead:

* never the slot's ``selected_version_id``;
* outside the most recent ``keep_recent`` versions of their slot;
* older than the retention window;
* and not mentioned by any identifier anywhere outside the assets index —
  the project document itself, plus every Runtime JSON record (media
  tasks, reviews, change rounds), streamed in bounded-size chunks so a
  record of any size is still scanned.  Identifier matching is deliberately
  a whole-JSON-string search so an unknown reference shape can only cause
  over-retention, never deletion of something still in use.

Slots are never emptied (the index validator forbids it), payloads are only
unlinked after the CAS authority swap succeeded, a lost CAS race simply
skips the cycle, and every pruned version leaves a compact audit record in
``runtime/retention-archive.jsonl``.
"""

from __future__ import annotations

import codecs
import json
import logging
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.runtime_files.atomic_store import fsync_directory
from services.runtime_files.path_safety import is_link_path

from .models import ArtifactVersion, AssetIndex, Project
from .retention_archive import append_retention_records, utc_now_iso
from .store import ProjectConflict, ProjectStore

logger = logging.getLogger(__name__)

DEFAULT_KEEP_RECENT_VERSIONS = 5
DEFAULT_VERSION_MAX_AGE_SECONDS = 30 * 24 * 3600.0

# Runtime trees that are either huge (sessions), frozen history being pruned
# by transaction_gc (transactions/blobs) or concurrency artifacts (locks).
_SCAN_EXCLUDED_DIRS = frozenset({"transactions", "blobs", "sessions", "locks"})
# Records are streamed through the reference search in chunks of this size;
# consecutive windows overlap by the longest needle so an identifier split
# across a chunk boundary cannot be missed.
_SCAN_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class AssetRetentionReport:
    pruned_versions: int = 0
    pruned_files: int = 0
    reclaimed_bytes: int = 0
    kept_referenced: int = 0
    dropped_version_ids: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.pruned_versions


@dataclass(frozen=True, slots=True)
class _RetentionPlan:
    drop_versions: dict[str, ArtifactVersion]
    drop_file_ids: frozenset[str]
    kept_referenced: int


def prune_artifact_versions(
    store: ProjectStore,
    project_id: str,
    *,
    keep_recent: int = DEFAULT_KEEP_RECENT_VERSIONS,
    max_age_seconds: float = DEFAULT_VERSION_MAX_AGE_SECONDS,
    now: datetime | None = None,
) -> AssetRetentionReport:
    """Drop dead artifact versions from one project and unlink their payloads."""

    keep_recent = max(1, keep_recent)
    current = (
        datetime.now(timezone.utc) if now is None else now
    )
    cutoff = current - timedelta(seconds=max_age_seconds)
    snapshot = store.read(project_id)
    project = snapshot.project
    assets = project.assets
    if not assets.artifact_versions_by_id:
        return AssetRetentionReport()
    candidates = _candidate_versions(assets, keep_recent, cutoff)
    if not candidates:
        return AssetRetentionReport()
    referenced = _scan_references(
        project,
        store.project_root(project_id),
        _candidate_identifiers(candidates),
    )
    if referenced is None:
        logger.warning(
            "asset retention skipped for %s: a Runtime record is unreadable",
            project_id,
        )
        return AssetRetentionReport()
    plan = _plan(assets, candidates, referenced)
    if not plan.drop_versions:
        return AssetRetentionReport(kept_referenced=plan.kept_referenced)
    candidate = project.model_copy(
        update={
            "assets": _pruned_index(assets, plan),
            "generation": project.generation + 1,
            "updated_at": current,
        },
    )
    try:
        store.replace(project_id, candidate, snapshot.etag)
    except ProjectConflict:
        # A concurrent commit won; the next startup pass re-plans from the
        # fresh authority instead of fighting over the ETag.
        logger.info(
            "asset retention skipped for %s: authority moved during pruning",
            project_id,
        )
        return AssetRetentionReport(kept_referenced=plan.kept_referenced)
    project_root = store.project_root(project_id)
    _archive_pruned_versions(project_root, project_id, plan)
    reclaimed = 0
    removed_files = 0
    for file_id in sorted(plan.drop_file_ids):
        indexed = assets.files_by_id[file_id]
        reclaimed += _delete_payload(project_root, indexed.relative_uri)
        removed_files += 1
    logger.info(
        "asset retention pruned %d artifact version(s), %d payload(s), "
        "%.1f MiB for project %s",
        len(plan.drop_versions),
        removed_files,
        reclaimed / (1024 * 1024),
        project_id,
    )
    return AssetRetentionReport(
        pruned_versions=len(plan.drop_versions),
        pruned_files=removed_files,
        reclaimed_bytes=reclaimed,
        kept_referenced=plan.kept_referenced,
        dropped_version_ids=tuple(sorted(plan.drop_versions)),
    )


def _candidate_versions(
    assets: AssetIndex,
    keep_recent: int,
    cutoff: datetime,
) -> dict[str, ArtifactVersion]:
    """Versions past the slot/selected/age gates, before reference checks."""

    candidates: dict[str, ArtifactVersion] = {}
    for slot in assets.artifact_slots_by_id.values():
        versions = [
            assets.artifact_versions_by_id[version_id]
            for version_id in slot.version_ids
            if version_id in assets.artifact_versions_by_id
        ]
        versions.sort(key=lambda version: version.created_at, reverse=True)
        for version in versions[keep_recent:]:
            if version.version_id == slot.selected_version_id:
                continue
            if version.created_at >= cutoff:
                continue
            candidates[version.version_id] = version
    return candidates


def _candidate_identifiers(
    candidates: dict[str, ArtifactVersion],
) -> frozenset[str]:
    """Every identifier a candidate version could be referenced by.

    A superset of what :func:`_plan` and :func:`_droppable_file_ids` later
    look up, so one streaming pass over the records answers every question.
    """

    identifiers: set[str] = set()
    for version in candidates.values():
        identifiers.add(version.version_id)
        identifiers.add(version.file_id)
        if version.thumbnail_file_id is not None:
            identifiers.add(version.thumbnail_file_id)
    return frozenset(identifiers)


def _plan(
    assets: AssetIndex,
    candidates: dict[str, ArtifactVersion],
    referenced: frozenset[str] | set[str],
) -> _RetentionPlan:
    drop_versions: dict[str, ArtifactVersion] = {}
    kept_referenced = 0
    for version in candidates.values():
        identifiers = [version.version_id, version.file_id]
        if version.thumbnail_file_id is not None:
            identifiers.append(version.thumbnail_file_id)
        if any(identifier in referenced for identifier in identifiers):
            kept_referenced += 1
            continue
        drop_versions[version.version_id] = version
    drop_file_ids = _droppable_file_ids(
        assets,
        drop_versions,
        referenced,
    )
    return _RetentionPlan(
        drop_versions=drop_versions,
        drop_file_ids=drop_file_ids,
        kept_referenced=kept_referenced,
    )


def _droppable_file_ids(
    assets: AssetIndex,
    drop_versions: dict[str, ArtifactVersion],
    referenced: frozenset[str] | set[str],
) -> frozenset[str]:
    """Files owned by dropped versions that no surviving record still uses."""

    candidates: set[str] = set()
    for version in drop_versions.values():
        candidates.add(version.file_id)
        if version.thumbnail_file_id is not None:
            candidates.add(version.thumbnail_file_id)
    survivors: set[str] = set()
    for version in assets.artifact_versions_by_id.values():
        if version.version_id in drop_versions:
            continue
        survivors.add(version.file_id)
        if version.thumbnail_file_id is not None:
            survivors.add(version.thumbnail_file_id)
    for version in assets.source_versions_by_id.values():
        for file_id in (
            version.file_id,
            version.thumbnail_file_id,
            version.native_model_file_id,
        ):
            if file_id is not None:
                survivors.add(file_id)
    for intelligence in assets.intelligence_versions_by_id.values():
        survivors.add(intelligence.file_id)
    return frozenset(
        file_id
        for file_id in candidates
        if file_id in assets.files_by_id
        and file_id not in survivors
        and file_id not in referenced
    )


def _pruned_index(assets: AssetIndex, plan: _RetentionPlan) -> AssetIndex:
    dumped = assets.model_dump(mode="json")
    for slot in dumped["artifact_slots_by_id"].values():
        slot["version_ids"] = [
            version_id
            for version_id in slot["version_ids"]
            if version_id not in plan.drop_versions
        ]
    for version_id in plan.drop_versions:
        dumped["artifact_versions_by_id"].pop(version_id, None)
    for file_id in plan.drop_file_ids:
        dumped["files_by_id"].pop(file_id, None)
    return AssetIndex.model_validate(dumped)


def _scan_references(
    project: Project,
    project_root: Path,
    identifiers: frozenset[str],
) -> set[str] | None:
    """Which identifiers appear quoted anywhere outside the assets index.

    The assets section is excluded because it is exactly what retention is
    allowed to shrink; everything else in the project document plus every
    Runtime JSON record must agree that an identifier is unused.  Records
    are streamed in bounded-size chunks with an overlap window, so size
    never forces an unscanned (and therefore unsafe) skip.  ``None`` means
    an unreadable record was hit and the caller must fail towards retention
    by skipping the cycle.
    """

    if not identifiers:
        return set()
    needles = {
        identifier: f'"{identifier}"' for identifier in identifiers
    }
    longest = max(len(needle) for needle in needles.values())
    found: set[str] = set()

    def _search(text: str) -> None:
        for identifier, needle in needles.items():
            if identifier not in found and needle in text:
                found.add(identifier)

    document = project.model_dump(mode="json")
    document.pop("assets", None)
    _search(json.dumps(document, ensure_ascii=False))
    runtime_root = project_root / "runtime"
    if runtime_root.is_dir() and not is_link_path(runtime_root):
        for record in runtime_root.rglob("*.json"):
            if len(found) == len(identifiers):
                break
            if any(
                part in _SCAN_EXCLUDED_DIRS
                for part in record.relative_to(runtime_root).parts[:-1]
            ):
                continue
            try:
                for chunk in _stream_text_chunks(record, longest - 1):
                    _search(chunk)
                    if len(found) == len(identifiers):
                        break
            except (OSError, UnicodeDecodeError):
                # An unreadable record must fail towards retention: its
                # identifier set is unknown, so nothing may be pruned.
                return None
    return found


def _stream_text_chunks(record: Path, overlap_chars: int) -> Iterator[str]:
    """Decode one file as overlapping text windows of bounded size.

    Each window repeats the last ``overlap_chars`` characters of the
    previous one, so a needle spanning a chunk boundary still matches.  A
    truncated multi-byte character at EOF raises ``UnicodeDecodeError``.
    """

    decoder = codecs.getincrementaldecoder("utf-8")()
    tail = ""
    with record.open("rb") as handle:
        while True:
            block = handle.read(_SCAN_CHUNK_BYTES)
            if not block:
                final = tail + decoder.decode(b"", True)
                if final:
                    yield final
                return
            text = tail + decoder.decode(block)
            yield text
            tail = text[len(text) - overlap_chars :] if overlap_chars else ""


def _archive_pruned_versions(
    project_root: Path,
    project_id: str,
    plan: _RetentionPlan,
) -> None:
    """Write the audit record for versions the authority just de-indexed.

    Best-effort by design: the CAS swap already removed these versions from
    the index, so refusing to unlink their payloads would leak orphan files
    forever — unlike transaction pruning, which keeps the directory when
    its audit record cannot be written.
    """

    if not plan.drop_versions:
        return
    try:
        append_retention_records(
            project_root / "runtime",
            [
                {
                    "kind": "asset-version-pruned",
                    "projectId": project_id,
                    "versionId": version.version_id,
                    "slotId": version.slot_id,
                    "artifactKind": version.kind,
                    "name": version.name,
                    "fileId": version.file_id,
                    "thumbnailFileId": version.thumbnail_file_id,
                    "checksum": version.checksum,
                    "createdAt": version.created_at.isoformat(),
                    "prunedAt": utc_now_iso(),
                }
                for version in sorted(
                    plan.drop_versions.values(),
                    key=lambda item: item.version_id,
                )
            ],
        )
    except OSError:
        logger.warning(
            "asset retention archive unwritable for %s; continuing",
            project_id,
        )


def _delete_payload(project_root: Path, relative_uri: str) -> int:
    """Unlink one de-indexed asset payload; returns bytes reclaimed."""

    path = project_root / relative_uri
    if not path.is_relative_to(project_root) or is_link_path(path):
        return 0
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return 0
    except OSError:
        return 0
    if not stat.S_ISREG(path_stat.st_mode):
        return 0
    path.unlink(missing_ok=True)
    size_bytes = path_stat.st_size
    assets_root = project_root / "assets"
    parent = path.parent
    fsync_directory(parent)
    while parent != assets_root and parent.is_relative_to(assets_root):
        try:
            parent.rmdir()
        except OSError:
            break
        grandparent = parent.parent
        try:
            fsync_directory(grandparent)
        except OSError:
            pass
        parent = grandparent
    return size_bytes
