# -*- coding: utf-8 -*-
"""Retention sweeps for converged transactions and the shared blob store."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.project_files.blobs import (
    ContentBlobStore,
    read_document_ref,
    write_document_ref,
)
from services.project_files.commit import (
    CommitJournalState,
    ProjectCommitJournal,
)
from services.project_files.transaction_gc import (
    migrate_inline_transaction_snapshots,
    prune_terminal_transactions,
    reclaim_converged_transaction_storage,
    sweep_orphan_blobs,
    unmigrate_blob_transaction_snapshots,
)
from services.runtime_files.atomic_store import AtomicJsonRecordStore

from .conftest import PROJECT_ID, make_store, runtime_root, transaction_root


pytestmark = pytest.mark.unit

_DAY = 24 * 3600.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_transaction(
    store,
    transaction_id,
    state,
    *,
    age_days,
    with_pointers=True,
    inline=False,
):
    """A synthetic transaction directory with a hand-written journal."""

    root = transaction_root(store, transaction_id)
    root.mkdir(parents=True)
    journal = ProjectCommitJournal(
        transaction_id=transaction_id,
        project_id=PROJECT_ID,
        round_id=f"round-{transaction_id}",
        state=state,
        base_etag="etag-base",
        candidate_etag="etag-candidate",
        temp_path=f"runtime/tmp/{transaction_id}",
        touched_pointers=[],
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    AtomicJsonRecordStore(
        root / "journal.json",
        ProjectCommitJournal,
    ).write(journal)
    if with_pointers:
        blob_store = ContentBlobStore(runtime_root(store))
        for name in ("base.json", "candidate.json"):
            document = {"txn": transaction_id, "part": name}
            if inline:
                # Legacy pre-blob layout: the full document lives inline.
                (root / name).write_text(
                    json.dumps(document),
                    encoding="utf-8",
                )
            else:
                write_document_ref(blob_store, root / name, document)
    mtime = time.time() - age_days * _DAY
    os.utime(root, (mtime, mtime))
    return root


def _backdate_blobs(store, age_days):
    blob_root = runtime_root(store) / "blobs"
    mtime = time.time() - age_days * _DAY
    for blob in blob_root.glob("*/*.json.gz"):
        os.utime(blob, (mtime, mtime))


def _pin_compensation(store, transaction_id):
    decision_root = (
        runtime_root(store)
        / "reviews"
        / "review-1"
        / "decision-transactions"
        / "decision-1"
    )
    decision_root.mkdir(parents=True)
    (decision_root / "journal.json").write_text(
        json.dumps(
            {
                "state": "PROJECT_APPLIED",
                "compensation_transaction_id": transaction_id,
            },
        ),
        encoding="utf-8",
    )


def test_prunes_aged_terminals_but_keeps_prepared_and_recent(tmp_path):
    store, _ = make_store(tmp_path)
    finalized_old = _make_transaction(
        store,
        "txn-old-1",
        CommitJournalState.RUNTIME_FINALIZED,
        age_days=60,
    )
    aborted_old = _make_transaction(
        store,
        "txn-old-2",
        CommitJournalState.ABORTED,
        age_days=59,
    )
    prepared_old = _make_transaction(
        store,
        "txn-old-3",
        CommitJournalState.PREPARED,
        age_days=58,
    )
    finalized_new = _make_transaction(
        store,
        "txn-new",
        CommitJournalState.RUNTIME_FINALIZED,
        age_days=1,
    )

    pruned, reclaimed = prune_terminal_transactions(
        store.project_root(PROJECT_ID),
        keep_recent=1,
        max_age_seconds=30 * _DAY,
    )

    assert pruned == 2
    assert reclaimed > 0
    assert not finalized_old.exists()
    assert not aborted_old.exists()
    assert prepared_old.is_dir()
    assert finalized_new.is_dir()


def test_recent_window_protects_newest_transactions(tmp_path):
    store, _ = make_store(tmp_path)
    roots = [
        _make_transaction(
            store,
            f"txn-{index}",
            CommitJournalState.RUNTIME_FINALIZED,
            age_days=60 - index,
        )
        for index in range(5)
    ]

    pruned, _ = prune_terminal_transactions(
        store.project_root(PROJECT_ID),
        keep_recent=3,
        max_age_seconds=30 * _DAY,
    )

    # The two oldest fall out of the recent window and past the age gate.
    assert pruned == 2
    assert not roots[0].exists()
    assert not roots[1].exists()
    assert all(root.is_dir() for root in roots[2:])


def test_review_compensation_reference_pins_transaction(tmp_path):
    store, _ = make_store(tmp_path)
    pinned = _make_transaction(
        store,
        "txn-pinned",
        CommitJournalState.RUNTIME_FINALIZED,
        age_days=60,
    )
    other = _make_transaction(
        store,
        "txn-other",
        CommitJournalState.RUNTIME_FINALIZED,
        age_days=59,
    )
    _pin_compensation(store, "txn-pinned")

    pruned, _ = prune_terminal_transactions(
        store.project_root(PROJECT_ID),
        keep_recent=0,
        max_age_seconds=30 * _DAY,
    )

    assert pruned == 1
    assert pinned.is_dir()
    assert not other.exists()


def test_sweep_removes_orphan_blobs_only(tmp_path):
    store, _ = make_store(tmp_path)
    kept = _make_transaction(
        store,
        "txn-kept",
        CommitJournalState.RUNTIME_FINALIZED,
        age_days=60,
    )
    blob_store = ContentBlobStore(runtime_root(store))
    orphan_ref = blob_store.put({"txn": "crashed", "part": "base.json"})
    fresh_orphan_ref = blob_store.put({"txn": "in-flight", "part": "base"})
    _backdate_blobs(store, 2)
    # The in-flight blob was just published; restore a fresh mtime for it.
    digest = fresh_orphan_ref.removeprefix("sha256:")
    fresh_path = blob_store.root / digest[:2] / f"{digest}.json.gz"
    os.utime(fresh_path, None)

    removed, reclaimed = sweep_orphan_blobs(
        store.project_root(PROJECT_ID),
        grace_seconds=600.0,
    )

    assert removed == 1
    assert reclaimed > 0
    orphan_path = blob_store._path(orphan_ref.removeprefix("sha256:"))
    assert not orphan_path.exists()
    assert fresh_path.exists()
    assert kept.is_dir()
    # The referenced blob behind the surviving pointers is untouched.
    pointer = json.loads((kept / "base.json").read_text(encoding="utf-8"))
    assert blob_store.get(pointer["blobRef"])["txn"] == "txn-kept"


def test_combined_pass_sweeps_blobs_of_pruned_transactions(tmp_path):
    store, _ = make_store(tmp_path)
    _make_transaction(
        store,
        "txn-aged",
        CommitJournalState.RUNTIME_FINALIZED,
        age_days=60,
    )
    survivor = _make_transaction(
        store,
        "txn-survivor",
        CommitJournalState.RUNTIME_FINALIZED,
        age_days=1,
    )
    _backdate_blobs(store, 2)
    blob_store = ContentBlobStore(runtime_root(store))

    report = reclaim_converged_transaction_storage(
        store.project_root(PROJECT_ID),
        keep_recent=1,
        max_age_seconds=30 * _DAY,
        blob_grace_seconds=600.0,
    )

    assert report.pruned_transactions == 1
    # txn-aged pointers died with the directory, so their exclusive blobs are
    # swept in the same pass; the survivor keeps its own.
    assert report.removed_blobs == 2
    assert survivor.is_dir()
    pointer = json.loads(
        (survivor / "base.json").read_text(encoding="utf-8"),
    )
    assert blob_store.get(pointer["blobRef"])["txn"] == "txn-survivor"


def test_unparsable_pointer_aborts_blob_sweep(tmp_path):
    store, _ = make_store(tmp_path)
    root = _make_transaction(
        store,
        "txn-corrupt",
        CommitJournalState.RUNTIME_FINALIZED,
        age_days=1,
    )
    blob_store = ContentBlobStore(runtime_root(store))
    orphan_ref = blob_store.put({"txn": "orphan"})
    (root / "latest.json").write_text("{broken", encoding="utf-8")
    _backdate_blobs(store, 2)

    removed, reclaimed = sweep_orphan_blobs(
        store.project_root(PROJECT_ID),
        grace_seconds=600.0,
    )

    assert (removed, reclaimed) == (0, 0)
    assert blob_store._path(orphan_ref.removeprefix("sha256:")).exists()


def test_missing_roots_are_noops(tmp_path):
    store, _ = make_store(tmp_path)
    project_root = store.project_root(PROJECT_ID)

    assert prune_terminal_transactions(project_root) == (0, 0)
    assert sweep_orphan_blobs(project_root) == (0, 0)
    report = reclaim_converged_transaction_storage(project_root)
    assert report.empty


def test_symlinked_transaction_root_is_ignored(tmp_path):
    store, _ = make_store(tmp_path)
    project_root = store.project_root(PROJECT_ID)
    runtime = project_root / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "journal.json").write_text("{}", encoding="utf-8")
    os.symlink(outside, runtime / "transactions", target_is_directory=True)

    assert prune_terminal_transactions(project_root) == (0, 0)
    assert (outside / "journal.json").exists()
    assert Path(runtime / "transactions").is_symlink()


def test_migration_rewrites_inline_snapshots_in_place(tmp_path):
    store, _ = make_store(tmp_path)
    # Even a non-terminal transaction is safe to upgrade: every reader
    # accepts both layouts, and the journal itself is never touched.
    root = _make_transaction(
        store,
        "txn-legacy",
        CommitJournalState.PREPARED,
        age_days=1,
        inline=True,
    )
    journal_before = (root / "journal.json").read_bytes()

    migrated, inline_bytes, pointer_bytes = (
        migrate_inline_transaction_snapshots(store.project_root(PROJECT_ID))
    )

    assert migrated == 2
    assert inline_bytes > 0
    assert pointer_bytes > 0
    blob_store = ContentBlobStore(runtime_root(store))
    for name in ("base.json", "candidate.json"):
        raw = json.loads((root / name).read_text(encoding="utf-8"))
        assert set(raw) == {"blobRef"}
        assert read_document_ref(blob_store, root / name) == {
            "txn": "txn-legacy",
            "part": name,
        }
    assert (root / "journal.json").read_bytes() == journal_before
    # Idempotent: pointers are recognized and never rewritten.
    assert migrate_inline_transaction_snapshots(
        store.project_root(PROJECT_ID),
    ) == (0, 0, 0)


def test_migration_skips_unreadable_inline_snapshot(tmp_path):
    store, _ = make_store(tmp_path)
    root = _make_transaction(
        store,
        "txn-legacy",
        CommitJournalState.RUNTIME_FINALIZED,
        age_days=1,
        inline=True,
    )
    (root / "latest.json").write_text("{broken", encoding="utf-8")

    migrated, _, _ = migrate_inline_transaction_snapshots(
        store.project_root(PROJECT_ID),
    )

    # The corrupt file stays exactly as it is for the recovery audit.
    assert migrated == 2
    assert (root / "latest.json").read_text(encoding="utf-8") == "{broken"


def test_combined_pass_prunes_then_migrates_then_sweeps(tmp_path):
    store, _ = make_store(tmp_path)
    _make_transaction(
        store,
        "txn-aged",
        CommitJournalState.RUNTIME_FINALIZED,
        age_days=60,
        inline=True,
    )
    survivor = _make_transaction(
        store,
        "txn-survivor",
        CommitJournalState.RUNTIME_FINALIZED,
        age_days=1,
        inline=True,
    )

    report = reclaim_converged_transaction_storage(
        store.project_root(PROJECT_ID),
        keep_recent=1,
        max_age_seconds=30 * _DAY,
        blob_grace_seconds=600.0,
    )

    assert report.pruned_transactions == 1
    # Only the survivor's two inline snapshots were worth migrating.
    assert report.migrated_snapshots == 2
    # Migration blobs are fresh and pointer-referenced: nothing is swept.
    assert report.removed_blobs == 0
    blob_store = ContentBlobStore(runtime_root(store))
    raw = json.loads((survivor / "base.json").read_text(encoding="utf-8"))
    assert blob_store.get(raw["blobRef"])["txn"] == "txn-survivor"


def test_migration_can_be_disabled(tmp_path):
    store, _ = make_store(tmp_path)
    root = _make_transaction(
        store,
        "txn-legacy",
        CommitJournalState.RUNTIME_FINALIZED,
        age_days=1,
        inline=True,
    )

    report = reclaim_converged_transaction_storage(
        store.project_root(PROJECT_ID),
        migrate_snapshots=False,
    )

    assert report.migrated_snapshots == 0
    assert report.empty
    raw = json.loads((root / "base.json").read_text(encoding="utf-8"))
    assert set(raw) != {"blobRef"}


def test_prune_archives_journal_before_deleting(tmp_path):
    store, _ = make_store(tmp_path)
    _make_transaction(
        store,
        "txn-aged",
        CommitJournalState.RUNTIME_FINALIZED,
        age_days=60,
    )

    pruned, _ = prune_terminal_transactions(
        store.project_root(PROJECT_ID),
        keep_recent=0,
        max_age_seconds=30 * _DAY,
    )

    assert pruned == 1
    archive = runtime_root(store) / "retention-archive.jsonl"
    assert archive.is_file()
    lines = [
        json.loads(line)
        for line in archive.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 1
    record = lines[0]
    assert record["kind"] == "transaction-pruned"
    assert record["transactionId"] == "txn-aged"
    assert record["directoryBytes"] > 0
    assert record["prunedAt"]
    dumped = json.dumps(record["journal"])
    assert "txn-aged" in dumped
    assert CommitJournalState.RUNTIME_FINALIZED.value in dumped


def test_unwritable_audit_archive_keeps_transaction(
    tmp_path,
    monkeypatch,
):
    store, _ = make_store(tmp_path)
    root = _make_transaction(
        store,
        "txn-aged",
        CommitJournalState.RUNTIME_FINALIZED,
        age_days=60,
    )

    def _refuse(*args, **kwargs):
        raise OSError("audit archive unwritable")

    monkeypatch.setattr(
        "services.project_files.transaction_gc.append_retention_records",
        _refuse,
    )

    pruned, reclaimed = prune_terminal_transactions(
        store.project_root(PROJECT_ID),
        keep_recent=0,
        max_age_seconds=30 * _DAY,
    )

    # Fail towards retention: no audit record, no deletion.
    assert (pruned, reclaimed) == (0, 0)
    assert root.is_dir()
    assert (root / "journal.json").is_file()


def test_migration_budget_pauses_and_resumes(tmp_path, caplog):
    store, _ = make_store(tmp_path)
    first = _make_transaction(
        store,
        "txn-a",
        CommitJournalState.RUNTIME_FINALIZED,
        age_days=1,
        inline=True,
    )
    _make_transaction(
        store,
        "txn-b",
        CommitJournalState.RUNTIME_FINALIZED,
        age_days=1,
        inline=True,
    )

    # A zero budget is exhausted before the first entry is touched.
    with caplog.at_level("INFO"):
        assert migrate_inline_transaction_snapshots(
            store.project_root(PROJECT_ID),
            time_budget_seconds=0.0,
        ) == (0, 0, 0)
    assert "paused" in caplog.text
    raw = json.loads((first / "base.json").read_text(encoding="utf-8"))
    assert set(raw) != {"blobRef"}

    # The next pass without a budget resumes and finishes the backlog.
    caplog.clear()
    with caplog.at_level("INFO"):
        migrated, _, _ = migrate_inline_transaction_snapshots(
            store.project_root(PROJECT_ID),
        )
    assert migrated == 4

    # Once everything is in blob format the settlement marker is logged,
    # which is the signal that legacy-inline readers can eventually go.
    caplog.clear()
    with caplog.at_level("INFO"):
        assert migrate_inline_transaction_snapshots(
            store.project_root(PROJECT_ID),
        ) == (0, 0, 0)
    assert "snapshot migration complete" in caplog.text


def test_unmigrate_expands_pointers_back_to_inline(tmp_path):
    store, _ = make_store(tmp_path)
    root = _make_transaction(
        store,
        "txn-legacy",
        CommitJournalState.RUNTIME_FINALIZED,
        age_days=1,
        inline=True,
    )
    journal_before = (root / "journal.json").read_bytes()
    migrate_inline_transaction_snapshots(store.project_root(PROJECT_ID))

    expanded, failed = unmigrate_blob_transaction_snapshots(
        store.project_root(PROJECT_ID),
    )

    assert (expanded, failed) == (2, 0)
    for name in ("base.json", "candidate.json"):
        raw = json.loads((root / name).read_text(encoding="utf-8"))
        assert raw == {"txn": "txn-legacy", "part": name}
    assert (root / "journal.json").read_bytes() == journal_before
    # Idempotent: inline documents are left exactly as they are.
    assert unmigrate_blob_transaction_snapshots(
        store.project_root(PROJECT_ID),
    ) == (0, 0)


def test_unmigrate_keeps_pointer_when_blob_is_missing(tmp_path):
    store, _ = make_store(tmp_path)
    root = _make_transaction(
        store,
        "txn-legacy",
        CommitJournalState.RUNTIME_FINALIZED,
        age_days=1,
    )
    blob_store = ContentBlobStore(runtime_root(store))
    pointer_before = (root / "base.json").read_bytes()
    digest = json.loads(pointer_before)["blobRef"].removeprefix("sha256:")
    blob_store._path(digest).unlink()

    expanded, failed = unmigrate_blob_transaction_snapshots(
        store.project_root(PROJECT_ID),
    )

    # The candidate blob still exists; the base pointer survives untouched
    # rather than being replaced by a guess.
    assert expanded == 1
    assert failed == 1
    assert (root / "base.json").read_bytes() == pointer_before
    raw = json.loads((root / "candidate.json").read_text(encoding="utf-8"))
    assert raw == {"txn": "txn-legacy", "part": "candidate.json"}
