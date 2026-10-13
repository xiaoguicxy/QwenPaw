# -*- coding: utf-8 -*-
# flake8: noqa: E501
# pylint: disable=protected-access,unused-argument
"""Scratch-peak trims over FfmpegLocalMediaRunner.

P1: the /tmp segment cache stores and restores by hard link, so a rendered
segment and its cache entry share one inode instead of two byte copies, with a
copy2 fallback when linking is refused (EXDEV).

P2: once concat merges this task's rendered segments they are deleted, dropping
the post-processing peak from ~3F to ~2F; a concat failure keeps every segment
for forensics, and caller-owned passthrough inputs are never touched.

Both are white-box unit tests: the ffmpeg-driven render/concat steps are faked,
so no real media is produced and no ffmpeg binary is required.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from domain.enums import CreatorCommandType
from services.media_files import local_execution
from services.media_files.local_execution import (
    FfmpegLocalMediaRunner,
    LocalMediaExecutionSpec,
    LocalMediaInput,
    _SegmentRender,
)

pytestmark = pytest.mark.unit


def _runner_with_cache(
    monkeypatch,
    tmp_path,
) -> tuple[FfmpegLocalMediaRunner, Path]:
    """Runner whose segment cache lives under tmp_path (isolated, same fs)."""

    cache_root = tmp_path / "segment-cache"
    cache_root.mkdir()
    runner = FfmpegLocalMediaRunner("ffmpeg")
    monkeypatch.setattr(runner, "_segment_cache_root", lambda: cache_root)
    return runner, cache_root


def _ino(path: Path) -> tuple[int, int]:
    info = path.stat()
    return (info.st_dev, info.st_ino)


# --- P1: segment cache store/restore share one inode ------------------------


def test_link_or_copy_hardlinks_by_default(tmp_path) -> None:
    src = tmp_path / "src.mp4"
    src.write_bytes(b"segment-payload")
    dst = tmp_path / "dst.mp4"

    local_execution._link_or_copy(src, dst)

    assert dst.read_bytes() == b"segment-payload"
    assert _ino(dst) == _ino(src)


def test_link_or_copy_falls_back_to_copy_on_exdev(
    tmp_path, monkeypatch
) -> None:
    src = tmp_path / "src.mp4"
    src.write_bytes(b"segment-payload")
    dst = tmp_path / "dst.mp4"

    def _exdev(*_args, **_kwargs):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(local_execution.os, "link", _exdev)
    local_execution._link_or_copy(src, dst)

    # Copy fallback: identical bytes, distinct inode.
    assert dst.read_bytes() == b"segment-payload"
    assert _ino(dst) != _ino(src)


def test_store_cached_segment_hardlinks_source(tmp_path, monkeypatch) -> None:
    runner, cache_root = _runner_with_cache(monkeypatch, tmp_path)
    segment = tmp_path / "seg.mp4"
    segment.write_bytes(b"payload")

    runner._store_cached_segment("key-1", segment, ())

    entry = cache_root / "key-1.mp4"
    # One inode shared by the scratch segment and the cache entry, so the
    # render no longer pays for a second full copy on disk.
    assert _ino(entry) == _ino(segment)
    assert entry.stat().st_nlink == 2


def test_store_cached_segment_exdev_falls_back_to_copy(
    tmp_path,
    monkeypatch,
) -> None:
    runner, cache_root = _runner_with_cache(monkeypatch, tmp_path)
    segment = tmp_path / "seg.mp4"
    segment.write_bytes(b"cross-device-payload")

    def _exdev(*_args, **_kwargs):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(local_execution.os, "link", _exdev)
    runner._store_cached_segment("key-exdev", segment, ())

    entry = cache_root / "key-exdev.mp4"
    assert entry.read_bytes() == b"cross-device-payload"
    assert _ino(entry) != _ino(segment)


def test_restore_cached_segment_hardlinks_entry(tmp_path, monkeypatch) -> None:
    runner, cache_root = _runner_with_cache(monkeypatch, tmp_path)
    segment = tmp_path / "seg.mp4"
    segment.write_bytes(b"payload")
    runner._store_cached_segment("key-2", segment, ("warn-a",))
    entry = cache_root / "key-2.mp4"

    restored = tmp_path / "restored" / "seg.mp4"
    restored.parent.mkdir()
    warnings = runner._restore_cached_segment("key-2", restored)

    assert warnings == ("warn-a",)
    assert restored.read_bytes() == b"payload"
    # A cache hit re-links the same inode: zero bytes written on restore.
    assert _ino(restored) == _ino(entry)


def test_prune_keeps_recent_entries_usable(tmp_path, monkeypatch) -> None:
    runner, cache_root = _runner_with_cache(monkeypatch, tmp_path)
    for index in range(3):
        segment = tmp_path / f"seg{index}.mp4"
        segment.write_bytes(b"payload-%d" % index)
        runner._store_cached_segment(f"key-{index}", segment, (f"w{index}",))
    # Deterministic LRU order: key-0 oldest .. key-2 newest.
    for index in range(3):
        os.utime(cache_root / f"key-{index}.mp4", (1_000 + index,) * 2)
    monkeypatch.setattr(local_execution, "_SEGMENT_CACHE_MAX_ITEMS", 2)

    runner._prune_segment_cache(cache_root)

    # Oldest entry (mp4 + sidecar) is pruned ...
    assert not (cache_root / "key-0.mp4").exists()
    assert not (cache_root / "key-0.json").exists()
    # ... and a surviving entry is still restorable via hard link.
    restored = tmp_path / "restored2.mp4"
    warnings = runner._restore_cached_segment("key-2", restored)
    assert warnings == ("w2",)
    assert restored.read_bytes() == b"payload-2"
    assert _ino(restored) == _ino(cache_root / "key-2.mp4")


# --- P2: concat deletes this task's rendered segments -----------------------


def _dummy_source(tmp_path: Path, index: int) -> Path:
    source = tmp_path / f"source-{index}.mp4"
    source.write_bytes(b"SOURCE-%d" % index)
    return source


def _input(
    index: int,
    path: Path,
    *,
    directives: bool = True,
) -> LocalMediaInput:
    extra = (
        {"start_seconds": 0.0, "end_seconds": 2.0, "duration_seconds": 2.0}
        if directives
        else {}
    )
    return LocalMediaInput(
        version_id=f"v{index}",
        file_id=f"file-{index}",
        checksum=f"sha256:seg{index}",
        media_type="video/mp4",
        path=path,
        source_ref=f"element:el{index}",
        **extra,
    )


def _p2_runner(
    monkeypatch,
    *,
    concat_error: Exception | None = None,
) -> FfmpegLocalMediaRunner:
    """Runner with the ffmpeg steps faked; segments and output are stubs."""

    runner = FfmpegLocalMediaRunner("ffmpeg")

    def fake_render_one_segment(
        spec,
        index,
        item,
        *,
        segment_dir,
        tail_trims,
    ) -> _SegmentRender:
        segment = segment_dir / f"{index:06d}.mp4"
        segment.write_bytes(b"SEGMENT-%d" % index)
        return _SegmentRender(segment, 2.0, ())

    def fake_concat(inputs, output, *, work_dir) -> None:
        if concat_error is not None:
            raise concat_error
        output.write_bytes(b"MERGED-OUTPUT")

    monkeypatch.setattr(runner, "_render_one_segment", fake_render_one_segment)
    monkeypatch.setattr(runner, "_concat", fake_concat)
    monkeypatch.setattr(runner, "_apply_color_grade", lambda spec: None)
    monkeypatch.setattr(
        runner,
        "_normalize_delivery_loudness",
        lambda spec: None,
    )
    return runner


def _compose_spec(work_dir, output, inputs) -> LocalMediaExecutionSpec:
    return LocalMediaExecutionSpec(
        command=CreatorCommandType.COMPOSE_FINAL_VIDEO,
        target_ref="timeline:main",
        task_id="task-p2",
        work_dir=work_dir,
        output_path=output,
        inputs=inputs,
        transitions=(),
        audio_plan="preserve",
        expected_duration_seconds=4.0,
        canvas_size=(640, 360),
    )


def test_compose_deletes_rendered_segments_after_concat(
    tmp_path,
    monkeypatch,
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    output = tmp_path / "output.mp4"
    inputs = tuple(_input(i, _dummy_source(tmp_path, i)) for i in range(3))

    _p2_runner(monkeypatch)._render_sync(
        _compose_spec(work_dir, output, inputs),
    )

    assert output.read_bytes() == b"MERGED-OUTPUT"
    # concat merged them, so every rendered segment is deleted (peak 3F->2F).
    assert not list((work_dir / "segments").glob("*.mp4"))


def test_concat_failure_preserves_segments_for_forensics(
    tmp_path,
    monkeypatch,
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    output = tmp_path / "output.mp4"
    inputs = tuple(_input(i, _dummy_source(tmp_path, i)) for i in range(2))
    runner = _p2_runner(monkeypatch, concat_error=RuntimeError("concat boom"))

    with pytest.raises(RuntimeError, match="concat boom"):
        runner._render_sync(_compose_spec(work_dir, output, inputs))

    # The failure raises before the delete point: all segments stay put.
    assert len(list((work_dir / "segments").glob("*.mp4"))) == 2
    assert not output.exists()


def test_direct_passthrough_never_deletes_caller_inputs(
    tmp_path,
    monkeypatch,
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    output = tmp_path / "output.mp4"
    caller_files = [_dummy_source(tmp_path, i) for i in range(2)]
    # A command outside {EXECUTE_EDIT, COMPOSE_FINAL_VIDEO} with directive-free
    # inputs selects the passthrough branch; _render_sync does not otherwise
    # interpret the command value.  Those inputs are caller-owned, never this
    # task's render products, so they must survive.
    inputs = tuple(
        _input(i, path, directives=False)
        for i, path in enumerate(caller_files)
    )
    spec = LocalMediaExecutionSpec(
        command=CreatorCommandType.ANALYZE_SOURCE_MEDIA,
        target_ref="timeline:main",
        task_id="task-passthrough",
        work_dir=work_dir,
        output_path=output,
        inputs=inputs,
        transitions=(),
        audio_plan="preserve",
        expected_duration_seconds=4.0,
        canvas_size=(640, 360),
    )

    _p2_runner(monkeypatch)._render_sync(spec)

    assert output.read_bytes() == b"MERGED-OUTPUT"
    for path in caller_files:
        assert path.exists()
    # Passthrough renders no segments, so no segments dir is even created.
    assert not (work_dir / "segments").exists()
