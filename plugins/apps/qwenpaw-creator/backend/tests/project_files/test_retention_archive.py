# -*- coding: utf-8 -*-
"""Append-only retention audit archive: durability, rotation, symlink safety."""

from __future__ import annotations

import gzip
import json
import os

import pytest

from services.project_files import retention_archive
from services.project_files.retention_archive import (
    ARCHIVE_NAME,
    append_retention_records,
)


pytestmark = pytest.mark.unit


def _record(index: int) -> dict:
    return {
        "kind": "transaction-pruned",
        "transactionId": f"txn-{index}",
        "pad": "x" * 48,
    }


def _lines(path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_append_creates_private_durable_file(tmp_path):
    written = append_retention_records(tmp_path, [_record(1)])

    assert written == 1
    archive = tmp_path / ARCHIVE_NAME
    assert _lines(archive) == [_record(1)]
    assert archive.stat().st_mode & 0o777 == 0o600
    # Append is cumulative and order-preserving.
    append_retention_records(tmp_path, [_record(2), _record(3)])
    assert [line["transactionId"] for line in _lines(archive)] == [
        "txn-1",
        "txn-2",
        "txn-3",
    ]


def test_empty_batch_is_a_noop(tmp_path):
    assert append_retention_records(tmp_path, []) == 0
    assert not (tmp_path / ARCHIVE_NAME).exists()


def test_rotation_gzips_and_truncates(tmp_path, monkeypatch):
    monkeypatch.setattr(retention_archive, "ROTATE_THRESHOLD_BYTES", 128)
    first = [_record(1), _record(2)]
    append_retention_records(tmp_path, first)
    rotated = sorted(tmp_path.glob("retention-archive-*.jsonl.gz"))
    assert len(rotated) == 1
    # The compressed copy holds every pre-rotation record ...
    with gzip.open(rotated[0], "rb") as handle:
        archived = [
            json.loads(line)
            for line in handle.read().decode("utf-8").splitlines()
            if line.strip()
        ]
    assert archived == first
    # ... and the live file was truncated, staying usable for new records.
    assert (tmp_path / ARCHIVE_NAME).stat().st_size == 0
    append_retention_records(tmp_path, [_record(3)])
    assert _lines(tmp_path / ARCHIVE_NAME) == [_record(3)]


def test_rotation_failure_keeps_live_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(retention_archive, "ROTATE_THRESHOLD_BYTES", 16)

    def _refuse_gzip(*args, **kwargs):
        raise OSError("rotation unwritable")

    monkeypatch.setattr(retention_archive.gzip, "open", _refuse_gzip)
    append_retention_records(tmp_path, [_record(1)])

    # Records stay durable in the live file; only rotation is lost.
    assert _lines(tmp_path / ARCHIVE_NAME) == [_record(1)]
    assert not list(tmp_path.glob("retention-archive-*.jsonl.gz"))


def test_symlinked_archive_is_refused(tmp_path):
    outside = tmp_path / "outside.jsonl"
    outside.write_text("", encoding="utf-8")
    os.symlink(outside, tmp_path / ARCHIVE_NAME)

    if hasattr(os, "O_NOFOLLOW"):
        with pytest.raises(OSError):
            append_retention_records(tmp_path, [_record(1)])
        assert _lines(outside) == []
    else:  # pragma: no cover - Windows has no O_NOFOLLOW
        pytest.skip("O_NOFOLLOW unavailable")
