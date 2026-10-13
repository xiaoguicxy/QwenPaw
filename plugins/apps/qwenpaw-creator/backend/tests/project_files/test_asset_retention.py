# -*- coding: utf-8 -*-
"""Retention pruning for superseded artifact versions."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from services.project_files.asset_retention import prune_artifact_versions
from services.project_files.assets import AssetFileStore
from services.project_files.models import (
    ArtifactVersionRenderSource,
    Project,
)
from services.project_files.store import ProjectConflict, ProjectStore

from .conftest import PROJECT_ID, r2v_element


pytestmark = pytest.mark.unit

SLOT_ID = "slot-final"
_DAY = timedelta(days=1)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _build_project(
    tmp_path,
    *,
    version_count=7,
    age_days=60,
    timeline_version=None,
    runtime_record_version=None,
    share_last_file=False,
    thumbnail_on_oldest=True,
):
    """A project whose ``final_video`` slot holds ``version_count`` payloads.

    Version ``i`` is older than version ``i+1``; the newest is selected.
    All versions are aged past the retention window unless ``age_days``
    says otherwise.
    """

    store = ProjectStore(tmp_path.resolve())
    base_snapshot = store.create(
        Project.new(project_id=PROJECT_ID, name="Retention"),
    )
    project_root = store.project_root(PROJECT_ID)
    asset_store = AssetFileStore(project_root)
    now = datetime.now(UTC)

    files: dict[str, dict] = {}
    versions: dict[str, dict] = {}
    version_ids: list[str] = []
    for index in range(version_count):
        version_id = f"version-{index}"
        file_id = f"file-{index}"
        content = f"payload-{index}".encode() * 64
        relative_uri = f"assets/artifacts/{SLOT_ID}/{version_id}/payload.bin"
        published = asset_store.publish(
            asset_store.stage_bytes(content, staging_id=version_id),
            relative_uri,
        )
        created = now - (age_days * _DAY) + timedelta(hours=index)
        files[file_id] = {
            "file_id": file_id,
            "kind": "artifact_payload",
            "relative_uri": published.relative_uri,
            "sha256": published.sha256,
            "size_bytes": published.size_bytes,
            "media_type": "video/mp4",
            "created_at": _iso(created),
        }
        thumbnail_file_id = None
        if thumbnail_on_oldest and index == 0:
            thumbnail_file_id = "thumb-0"
            thumb_uri = f"assets/artifacts/{SLOT_ID}/{version_id}/thumb.png"
            thumb = asset_store.publish(
                asset_store.stage_bytes(b"thumbnail", staging_id="thumb-0"),
                thumb_uri,
            )
            files["thumb-0"] = {
                "file_id": "thumb-0",
                "kind": "artifact_thumbnail",
                "relative_uri": thumb.relative_uri,
                "sha256": thumb.sha256,
                "size_bytes": thumb.size_bytes,
                "media_type": "image/png",
                "created_at": _iso(created),
            }
        versions[version_id] = {
            "version_id": version_id,
            "slot_id": SLOT_ID,
            "kind": "final_video",
            "owner_ref": "timeline:main",
            "name": f"final cut {index}",
            "file_id": file_id,
            "checksum": published.sha256,
            "based_on_generation": 0,
            "thumbnail_file_id": thumbnail_file_id,
            "created_at": _iso(created),
        }
        version_ids.append(version_id)
    if share_last_file:
        # The two newest versions deliberately share one payload inode.
        newest, previous = version_ids[-1], version_ids[-2]
        versions[previous]["file_id"] = versions[newest]["file_id"]
        versions[previous]["checksum"] = versions[newest]["checksum"]
        shared = versions[newest]["file_id"]
        files.pop(f"file-{version_count - 2}", None)
        assert shared in files

    project = base_snapshot.project
    if timeline_version is not None:
        element = r2v_element("el-1", start=0).model_copy(
            update={
                "render_source": ArtifactVersionRenderSource(
                    version_id=timeline_version,
                    source_in_tick=0,
                    source_out_tick=1000,
                ),
            },
        )
        timeline = project.timelines.items["timeline:main"].model_copy(
            update={"elements_by_id": {"el-1": element}},
        )
        project = project.model_copy(deep=True)
        project.timelines.items["timeline:main"] = timeline
    document = project.model_dump(mode="json")
    document["assets"]["files_by_id"] = files
    document["assets"]["artifact_versions_by_id"] = versions
    document["assets"]["artifact_slots_by_id"][SLOT_ID] = {
        "slot_id": SLOT_ID,
        "kind": "final_video",
        "owner_ref": "timeline:main",
        "version_ids": version_ids,
        "selected_version_id": version_ids[-1],
    }
    document["generation"] = base_snapshot.generation + 1
    document["updated_at"] = _iso(datetime.now(UTC))
    snapshot = store.replace(
        PROJECT_ID,
        Project.model_validate(document),
        base_snapshot.etag,
    )
    if runtime_record_version is not None:
        record_dir = project_root / "runtime" / "medias" / "task-1"
        record_dir.mkdir(parents=True)
        (record_dir / "task.json").write_text(
            json.dumps({"fileIds": [runtime_record_version]}),
            encoding="utf-8",
        )
    return store, snapshot, project_root


def test_prunes_aged_unreferenced_versions_and_keeps_guarded_ones(tmp_path):
    store, _, project_root = _build_project(
        tmp_path,
        timeline_version="version-1",
        runtime_record_version="version-3",
    )

    report = prune_artifact_versions(
        store,
        PROJECT_ID,
        keep_recent=2,
        max_age_seconds=30 * 86400.0,
    )

    # Newest two are windowed; version-4/-2/-0 die; -1 (timeline) and -3
    # (runtime record) are pinned by references.
    assert report.pruned_versions == 3
    assert set(report.dropped_version_ids) == {
        "version-4",
        "version-2",
        "version-0",
    }
    assert report.kept_referenced == 2
    assert report.pruned_files == 4  # three payloads plus one thumbnail
    for gone in ("version-4", "version-2", "version-0"):
        assert not (
            project_root / "assets" / "artifacts" / SLOT_ID / gone
        ).exists()
    for kept in ("version-6", "version-5", "version-3", "version-1"):
        assert (
            project_root
            / "assets"
            / "artifacts"
            / SLOT_ID
            / kept
            / "payload.bin"
        ).is_file()
    after = store.read(PROJECT_ID)
    assert after.project.generation == 2
    slot = after.project.assets.artifact_slots_by_id[SLOT_ID]
    assert set(slot.version_ids) == {
        "version-6",
        "version-5",
        "version-3",
        "version-1",
    }
    assert slot.selected_version_id == "version-6"
    assert "version-0" not in after.project.assets.artifact_versions_by_id
    assert "file-0" not in after.project.assets.files_by_id
    assert "thumb-0" not in after.project.assets.files_by_id
    assert report.reclaimed_bytes > 0


def test_selected_and_young_versions_survive(tmp_path):
    store, _, project_root = _build_project(tmp_path, age_days=1)

    report = prune_artifact_versions(
        store,
        PROJECT_ID,
        keep_recent=1,
        max_age_seconds=30 * 86400.0,
    )

    assert report.empty
    assert (
        project_root / "assets" / "artifacts" / SLOT_ID / "version-0"
    ).is_dir()
    assert store.read(PROJECT_ID).project.generation == 1


def test_shared_payload_survives_while_one_version_dies(tmp_path):
    store, _, project_root = _build_project(
        tmp_path,
        version_count=4,
        share_last_file=True,
    )

    report = prune_artifact_versions(
        store,
        PROJECT_ID,
        keep_recent=1,
        max_age_seconds=30 * 86400.0,
    )

    # version-2 shares its payload with the selected version-3, so the file
    # and index entry must survive even though the version record dies.
    assert "version-2" in report.dropped_version_ids
    after = store.read(PROJECT_ID)
    assert "file-3" in after.project.assets.files_by_id
    assert (
        project_root
        / "assets"
        / "artifacts"
        / SLOT_ID
        / "version-3"
        / "payload.bin"
    ).is_file()


def test_etag_conflict_skips_cycle_without_touching_disk(
    tmp_path,
    monkeypatch,
):
    store, _, project_root = _build_project(tmp_path, version_count=4)

    def _conflict(*_args, **_kwargs):
        raise ProjectConflict("simulated concurrent commit")

    monkeypatch.setattr(store, "replace", _conflict)
    report = prune_artifact_versions(
        store,
        PROJECT_ID,
        keep_recent=1,
        max_age_seconds=30 * 86400.0,
    )

    assert report.empty
    assert (
        project_root / "assets" / "artifacts" / SLOT_ID / "version-0"
    ).is_dir()
    assert store.read(PROJECT_ID).project.generation == 1


def test_unreadable_runtime_record_fails_towards_retention(tmp_path):
    store, _, project_root = _build_project(tmp_path, version_count=4)
    blocker = project_root / "runtime" / "medias" / "evil.json"
    blocker.mkdir(parents=True)

    report = prune_artifact_versions(
        store,
        PROJECT_ID,
        keep_recent=1,
        max_age_seconds=30 * 86400.0,
    )

    assert report.empty
    assert (
        project_root / "assets" / "artifacts" / SLOT_ID / "version-0"
    ).is_dir()


def test_large_runtime_record_is_streamed_across_chunk_boundaries(
    tmp_path,
    monkeypatch,
):
    from services.project_files import asset_retention

    store, _, project_root = _build_project(
        tmp_path,
        version_count=4,
        runtime_record_version="version-0",
    )
    # Tiny chunks force every needle to span multiple windows; the overlap
    # must still find the reference so the version is kept.
    monkeypatch.setattr(asset_retention, "_SCAN_CHUNK_BYTES", 8)

    report = prune_artifact_versions(
        store,
        PROJECT_ID,
        keep_recent=1,
        max_age_seconds=30 * 86400.0,
    )

    assert report.kept_referenced == 1
    assert set(report.dropped_version_ids) == {"version-1", "version-2"}
    assert (
        project_root / "assets" / "artifacts" / SLOT_ID / "version-0"
    ).is_dir()


def test_invalid_utf8_record_fails_towards_retention(tmp_path):
    store, _, project_root = _build_project(tmp_path, version_count=4)
    record_dir = project_root / "runtime" / "medias" / "task-bad"
    record_dir.mkdir(parents=True)
    (record_dir / "task.json").write_bytes(b'{"note": "\xff\xfe broken"}')

    report = prune_artifact_versions(
        store,
        PROJECT_ID,
        keep_recent=1,
        max_age_seconds=30 * 86400.0,
    )

    # An undecodable record's identifier set is unknown, so nothing may
    # be pruned this cycle.
    assert report.empty
    assert (
        project_root / "assets" / "artifacts" / SLOT_ID / "version-0"
    ).is_dir()


def test_pruned_versions_leave_audit_records(tmp_path):
    store, _, project_root = _build_project(tmp_path, version_count=4)

    report = prune_artifact_versions(
        store,
        PROJECT_ID,
        keep_recent=1,
        max_age_seconds=30 * 86400.0,
    )

    assert report.pruned_versions == 3
    archive = project_root / "runtime" / "retention-archive.jsonl"
    assert archive.is_file()
    records = [
        json.loads(line)
        for line in archive.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [record["kind"] for record in records] == [
        "asset-version-pruned",
    ] * 3
    assert {record["versionId"] for record in records} == {
        "version-0",
        "version-1",
        "version-2",
    }
    for record in records:
        assert record["projectId"] == PROJECT_ID
        assert record["slotId"] == SLOT_ID
        assert record["fileId"]
        assert record["checksum"]
        assert record["createdAt"]
        assert record["prunedAt"]


def test_unwritable_audit_archive_does_not_block_asset_pruning(
    tmp_path,
    monkeypatch,
):
    store, _, project_root = _build_project(tmp_path, version_count=4)

    def _refuse(*args, **kwargs):
        raise OSError("audit archive unwritable")

    monkeypatch.setattr(
        "services.project_files.asset_retention.append_retention_records",
        _refuse,
    )

    report = prune_artifact_versions(
        store,
        PROJECT_ID,
        keep_recent=1,
        max_age_seconds=30 * 86400.0,
    )

    # Unlike transaction pruning: the authority swap already de-indexed the
    # versions, so keeping their payloads would leak orphan files forever.
    assert report.pruned_versions == 3
    assert not (
        project_root / "assets" / "artifacts" / SLOT_ID / "version-0"
    ).exists()


def test_slot_is_never_emptied(tmp_path):
    store, _, _project_root = _build_project(tmp_path, version_count=2)

    report = prune_artifact_versions(
        store,
        PROJECT_ID,
        keep_recent=1,
        max_age_seconds=30 * 86400.0,
    )

    after = store.read(PROJECT_ID)
    slot = after.project.assets.artifact_slots_by_id[SLOT_ID]
    assert report.pruned_versions == 1
    assert slot.version_ids == ["version-1"]
    assert slot.selected_version_id == "version-1"
