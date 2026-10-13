# -*- coding: utf-8 -*-
# flake8: noqa: E501
# pylint: disable=protected-access,unused-argument
"""TTS HTTP-family zero-copy ingest (P3).

The HTTP family downloads narration straight to a temp file and hands over its
path, so the audio never round-trips through RAM (``read_bytes``) nor gets
rewritten on disk: duration is probed off the file and the store ingests it by
hard link (``stage_path(consume_source=True)``).  The WebSocket family keeps
streaming into memory as bytes.  These tests lock the dual-carrier contract,
the streaming hash, the path-based duration and the consume-on-success /
cleanup-on-failure lifecycle.
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import wave
from pathlib import Path

import pytest

from models import tts_model
from services.media_files import audio_execution
from services.media_files.audio_execution import execute_file_tts_command
from services.project_files.assets import AssetFileStore

from .conftest import r2v_project_services

pytestmark = pytest.mark.unit

PROJECT_ID = "tts-ingest-project"


def _write_wav(path: Path, *, frames: int, rate: int = 44100) -> bytes:
    """Write a mono 16-bit WAV so header parsing yields a true duration."""

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * frames)
    return path.read_bytes()


# --- dual-carrier contract --------------------------------------------------


def test_synthesis_requires_exactly_one_carrier(tmp_path) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        tts_model.TTSSynthesis(
            media_type="audio/wav",
            model="m",
            voice="v",
            characters=1,
        )
    with pytest.raises(ValueError, match="exactly one"):
        tts_model.TTSSynthesis(
            media_type="audio/wav",
            model="m",
            voice="v",
            characters=1,
            audio_bytes=b"x",
            audio_path=tmp_path / "a.wav",
        )
    carried = tts_model.TTSSynthesis(
        media_type="audio/wav",
        model="m",
        voice="v",
        characters=1,
        audio_bytes=b"x",
    )
    assert carried.audio_path is None


# --- download hands over a temp file, never bytes ---------------------------


def test_download_audio_returns_persistent_temp_file(monkeypatch) -> None:
    payload = b"RIFF\x00\x00\x00\x00WAVEdata-payload"

    def fake_download(url, destination):
        Path(destination).write_bytes(payload)

    monkeypatch.setattr(tts_model, "download_remote_file", fake_download)
    path, media_type = tts_model._download_audio("https://example.com/a.wav")
    try:
        # mimetypes maps .wav to audio/wav or audio/x-wav by platform; both
        # are handled downstream.
        assert media_type in {"audio/wav", "audio/x-wav"}
        assert path.is_file()
        assert path.read_bytes() == payload
    finally:
        path.unlink(missing_ok=True)


def test_download_audio_empty_payload_leaves_no_temp_file(monkeypatch) -> None:
    def fake_download(url, destination):
        Path(destination).write_bytes(b"")

    monkeypatch.setattr(tts_model, "download_remote_file", fake_download)
    before = set(Path(tempfile.gettempdir()).glob("creator-tts-*"))
    with pytest.raises(tts_model.ModelError, match="empty audio payload"):
        tts_model._download_audio("https://example.com/empty.wav")
    after = set(Path(tempfile.gettempdir()).glob("creator-tts-*"))
    # The failed download cleans up after itself: no orphan temp file.
    assert after == before


# --- streaming hash + path-based duration -----------------------------------


def test_sha256_file_matches_hashlib(tmp_path) -> None:
    blob = b"narration-payload" * 4096
    path = tmp_path / "a.wav"
    path.write_bytes(blob)
    assert (
        audio_execution._sha256_file(path) == hashlib.sha256(blob).hexdigest()
    )


def test_synthesis_duration_reads_wav_path_without_ffprobe(
    tmp_path,
    monkeypatch,
) -> None:
    wav = tmp_path / "n.wav"
    _write_wav(wav, frames=22050, rate=44100)  # exactly 0.5s

    def _refuse_subprocess(*_args, **_kwargs):  # pragma: no cover - guard
        raise AssertionError("duration must not shell out for a WAV path")

    monkeypatch.setattr(audio_execution.subprocess, "run", _refuse_subprocess)
    synthesis = tts_model.TTSSynthesis(
        media_type="audio/wav",
        model="m",
        voice="v",
        characters=3,
        audio_path=wav,
    )
    assert audio_execution._synthesis_duration_seconds(synthesis) == (
        pytest.approx(0.5, abs=0.01)
    )


# --- end-to-end path ingest through execute_file_tts_command ----------------


def test_execute_file_tts_path_ingest_consumes_source(
    tmp_path,
    monkeypatch,
) -> None:
    services = r2v_project_services(
        tmp_path,
        monkeypatch,
        project_id=PROJECT_ID,
        name="TTS Ingest",
    )
    wav = tmp_path / "downloaded.wav"
    payload = _write_wav(wav, frames=44100, rate=44100)  # 1.0s
    expected_checksum = hashlib.sha256(payload).hexdigest()

    async def fake_synthesize(text, **_kwargs):
        return tts_model.TTSSynthesis(
            audio_path=wav,
            media_type="audio/wav",
            model="qwen3-tts-flash",
            voice="Cherry",
            characters=len(text),
        )

    monkeypatch.setattr(tts_model, "synthesize", fake_synthesize)
    result = asyncio.run(
        execute_file_tts_command(
            services,
            project_id=PROJECT_ID,
            target_ref="element:narration-1",
            arguments={"text": "旁白内容"},
            idempotency_key="tts-path-ingest",
        ),
    )

    # The zero-copy handoff consumed the downloaded temp file.
    assert not wav.exists()
    assert result.duration_seconds == pytest.approx(1.0, abs=0.01)
    # The stored asset is byte-identical to what was downloaded.
    snapshot = services.projects.read(PROJECT_ID)
    indexed = snapshot.project.assets.files_by_id[result.file_id]
    assert indexed.sha256 == expected_checksum
    assert indexed.size_bytes == len(payload)
    store = AssetFileStore(services.projects.project_root(PROJECT_ID))
    assert store.read_verified(indexed) == payload


def test_execute_file_tts_cleans_temp_file_on_register_failure(
    tmp_path,
    monkeypatch,
) -> None:
    services = r2v_project_services(
        tmp_path,
        monkeypatch,
        project_id=PROJECT_ID,
        name="TTS Ingest Fail",
    )
    wav = tmp_path / "downloaded.wav"
    _write_wav(wav, frames=44100, rate=44100)

    async def fake_synthesize(text, **_kwargs):
        return tts_model.TTSSynthesis(
            audio_path=wav,
            media_type="audio/wav",
            model="qwen3-tts-flash",
            voice="Cherry",
            characters=len(text),
        )

    def boom(*_args, **_kwargs):
        raise RuntimeError("register failed")

    monkeypatch.setattr(tts_model, "synthesize", fake_synthesize)
    monkeypatch.setattr(audio_execution, "_register_audio_asset", boom)

    with pytest.raises(RuntimeError, match="register failed"):
        asyncio.run(
            execute_file_tts_command(
                services,
                project_id=PROJECT_ID,
                target_ref="element:narration-1",
                arguments={"text": "旁白内容"},
                idempotency_key="tts-path-fail",
            ),
        )

    # The finally block removes the downloaded temp file even on failure.
    assert not wav.exists()
