# -*- coding: utf-8 -*-
"""Connectivity probes must measure the route production actually calls.

Image and video probes stay on free read-only APIs: submitting a real
generation task is billable, and the DashScope gateway rejects an async
submission that omits ``X-DashScope-Async`` with 403. ASR is the exception —
one second of generated silence costs a fraction of a cent, and the free
probes it used to rely on (``GET /models/{id}``, the upload-policy call) said
nothing about transcription, which is how three dead ASR routes stayed green.
"""

from __future__ import annotations

import asyncio
import io
import wave

import pytest

from api import model_routes
from api.model_routes import (
    ProbeDestinationError,
    _asr_provider,
    _assert_probe_destination,
    _assert_probe_peer,
    _minimax_base_resp_error,
    _prepare_probe_payload,
    _probe_payload,
    _send_probe,
    _silent_wav_bytes,
    _validate_section_connectivity,
)
from domain.errors import ValidationError
from models import media_transport
from schemas.models import ModelConnectionTestRequest


def _request(**overrides) -> ModelConnectionTestRequest:
    payload = {
        "type": "asr",
        "base_url": "https://dashscope.aliyuncs.com/api/v1",
        "api_key": "sk-test",
        "model_name": "fun-asr",
        "protocol": "DashScope Fun-ASR",
        "provider": "fun-asr",
    }
    payload.update(overrides)
    return ModelConnectionTestRequest(**payload)


@pytest.fixture(name="uploaded_samples")
def _uploaded_samples(monkeypatch: pytest.MonkeyPatch) -> list:
    """Stub the DashScope temporary-OSS upload, recording what it was given."""

    calls: list = []

    async def upload(path, **kwargs):
        calls.append((path.name, path.read_bytes(), kwargs))
        return "oss://dashscope/creator-probe.wav"

    monkeypatch.setattr(
        media_transport,
        "upload_local_file_to_dashscope_temp",
        upload,
    )
    return calls


def _probe(request):
    return asyncio.run(_prepare_probe_payload(request))


def test_asr_fun_probe_submits_a_real_transcription_task(
    uploaded_samples,
) -> None:
    """Fun-ASR is probed on the route production transcribes with.

    Regression guard: the probe used to call the DashScope upload-policy API,
    which says nothing about transcription, so a platform answering 404 on
    ``asr/transcription`` still showed a green connection test.
    """

    url, headers, payload = _probe(_request())

    assert url == (
        "https://dashscope.aliyuncs.com/api/v1"
        "/services/audio/asr/transcription"
    )
    # Without this header the gateway answers 403 "does not support
    # asynchronous calls" — the failure that pushed the probe onto the
    # zero-cost policy call in the first place.
    assert headers["X-DashScope-Async"] == "enable"
    assert headers["X-DashScope-OssResourceResolve"] == "enable"
    assert payload == {
        "model": "fun-asr",
        "input": {"file_urls": ["oss://dashscope/creator-probe.wav"]},
        "parameters": {},
    }
    # The sample really was uploaded, and it really is audio.
    name, sample, kwargs = uploaded_samples[0]
    assert name == "creator-probe.wav"
    assert sample == _silent_wav_bytes()
    assert kwargs["media_type"] == "audio/wav"
    assert kwargs["api_key"] == "sk-test"


def test_asr_fun_probe_does_not_double_a_token_portal_path(
    uploaded_samples,
) -> None:
    """A base URL already carrying the transcription path must not repeat it.

    ``_fun_asr_base`` strips the suffix for production; the probe has to join
    back to the same URL or the proxy rejects the doubled path with 403.
    """

    del uploaded_samples
    url, _headers, _payload = _probe(
        _request(
            base_url=(
                "https://dashscope.aliyuncs.com/api/v1"
                "/services/audio/asr/transcription"
            ),
        ),
    )

    assert url == (
        "https://dashscope.aliyuncs.com/api/v1"
        "/services/audio/asr/transcription"
    )


def test_asr_qwen3_probe_uses_the_multimodal_generation_route(
    uploaded_samples,
) -> None:
    """qwen3-asr must not be probed as if it were Fun-ASR.

    The previous dispatch never produced ``qwen3-asr``, so that config was
    validated against the Fun-ASR upload-policy call while production posted
    to ``multimodal-generation`` — the route that answered 502 in the field.
    """

    del uploaded_samples
    url, headers, payload = _probe(
        _request(
            model_name="qwen3-asr-flash",
            provider="fun-asr",
            protocol="DashScope Qwen3-ASR",
        ),
    )

    assert url == (
        "https://dashscope.aliyuncs.com"
        "/api/v1/services/aigc/multimodal-generation/generation"
    )
    assert headers["X-DashScope-OssResourceResolve"] == "enable"
    assert "X-DashScope-Async" not in headers
    assert payload == {
        "model": "qwen3-asr-flash",
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"audio": "oss://dashscope/creator-probe.wav"},
                    ],
                },
            ],
        },
        "parameters": {"result_format": "message"},
    }


def test_asr_whisper_probe_posts_a_real_multipart_transcription(
    uploaded_samples,
) -> None:
    """Whisper is probed by transcribing, not by looking the model up.

    ``GET /models/whisper-1`` only proves the key and the registry entry; the
    field failure was a 500 on ``audio/transcriptions`` itself.
    """

    url, headers, payload = _probe(
        _request(
            base_url="https://api.openai.com/v1",
            model_name="whisper-1",
            protocol="OpenAI Whisper",
            provider="whisper",
        ),
    )

    assert url == "https://api.openai.com/v1/audio/transcriptions"
    multipart = payload["_multipart_probe"]
    assert multipart["data"] == {
        "model": "whisper-1",
        "response_format": "verbose_json",
    }
    name, sample, mime = multipart["files"]["file"]
    assert (name, mime) == ("creator-probe.wav", "audio/wav")
    assert sample == _silent_wav_bytes()
    assert headers["Authorization"] == "Bearer sk-test"
    # OpenAI-compatible whisper takes the bytes inline: no DashScope upload.
    assert uploaded_samples == []


def test_asr_protocol_label_cannot_redirect_the_probe(
    uploaded_samples,
) -> None:
    """A Whisper protocol label must not move a Fun-ASR config's probe.

    ``get_asr_provider()`` reads only the stored provider, so this config
    transcribes through Fun-ASR. Probing ``audio/transcriptions`` instead
    would succeed against an OpenAI-compatible base URL and report green
    for a route production never calls.
    """

    del uploaded_samples
    url, headers, _payload = _probe(
        _request(
            base_url="https://api.openai.com/v1",
            model_name="whisper-1",
            protocol="OpenAI Whisper",
            provider="fun-asr",
        ),
    )

    assert url == (
        "https://api.openai.com/v1/services/audio/asr/transcription"
    )
    assert headers["X-DashScope-Async"] == "enable"


@pytest.mark.parametrize(
    ("provider", "protocol", "model_name", "expected"),
    [
        ("whisper", "", "whisper-1", "whisper"),
        # get_asr_provider() reads only the stored provider and defaults to
        # fun-asr, so the protocol label never decides the route.
        (None, "OpenAI Whisper", "whisper-1", "fun-asr"),
        # A protocol/provider mismatch is a broken config, not a licence to
        # probe the route the label implies.
        ("fun-asr", "OpenAI Whisper", "whisper-1", "fun-asr"),
        # get_asr_provider() only ever returns whisper or fun-asr, so a
        # qwen3-asr model arrives labelled fun-asr and is routed by name.
        ("fun-asr", "", "qwen3-asr-flash", "qwen3-asr"),
        (None, "", "qwen3-asr-flash", "qwen3-asr"),
        ("fun-asr", "DashScope Fun-ASR", "fun-asr", "fun-asr"),
        (None, "", "paraformer-v2", "fun-asr"),
    ],
)
def test_asr_provider_mirrors_the_production_dispatch(
    provider,
    protocol,
    model_name,
    expected,
) -> None:
    """The probe must run the route ``asr_model.transcribe`` would run."""

    assert (
        _asr_provider(
            _request(
                provider=provider,
                protocol=protocol,
                model_name=model_name,
            ),
        )
        == expected
    )


def test_silent_probe_sample_is_one_second_of_decodable_silence() -> None:
    """The sample must be real audio, or the probe proves nothing."""

    with wave.open(io.BytesIO(_silent_wav_bytes()), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 16_000
        assert handle.getnframes() == 16_000
        assert handle.readframes(16_000) == b"\x00" * 32_000


def test_probe_payload_refuses_to_guess_an_asr_route() -> None:
    """ASR dispatch is async; the sync path must not silently fall through."""

    with pytest.raises(ValueError, match="_prepare_probe_payload"):
        _probe_payload(_request())


class _EmptyAsrConfig:
    """A stored config with nothing filled in, as a first-run install has."""

    class _Item:
        api_key = ""
        base_url = ""
        model_name = ""
        protocol = ""
        provider = None
        voice = ""
        reuse_llm_key = False

    asr = _Item()
    llm = _Item()


def test_connection_test_reports_an_unstageable_sample_as_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A probe that cannot stage its own sample must still report cleanly.

    The Fun-ASR and qwen3-asr probes upload the silent sample through the
    real DashScope temporary OSS, which refuses an absent credential with a
    ``RuntimeError``. Uncaught, that turned the endpoint whose whole job is
    explaining a broken configuration into a 500.
    """

    monkeypatch.setattr(model_routes, "load_model_config", _EmptyAsrConfig)

    response = asyncio.run(
        model_routes.test_model_connection(
            # require_api_key=False is the caller's choice; the probe still
            # has to cope with the empty credential instead of exploding.
            _request(api_key="", require_api_key=False),
        ),
    )

    assert not response.ok
    assert "RuntimeError" in response.error
    assert "API key" in response.error


def test_connection_test_reports_a_metadata_target_as_a_failure(
    uploaded_samples,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused destination is a diagnostic answer, not a server error.

    End to end, because the exception being a ``ValueError`` only matters if
    the route's handler still lists ``ValueError``: drop it from that tuple
    and the endpoint whose job is explaining a broken configuration answers
    with a 500 instead.
    """

    del uploaded_samples
    monkeypatch.setattr(model_routes, "load_model_config", _EmptyAsrConfig)

    response = asyncio.run(
        model_routes.test_model_connection(
            _request(
                base_url="http://169.254.169.254/latest/meta-data",
                require_api_key=False,
            ),
        ),
    )

    assert not response.ok
    assert "ProbeDestinationError" in response.error
    assert "元数据" in response.error


def test_section_validator_reports_a_refused_probe_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused sample upload belongs in the 422 channel, not a 500.

    ``_validate_section_connectivity`` has no caller today, so this pins the
    contract for whenever it is wired into config save: the ASR probe is the
    only one that can fail before it puts anything on the wire, and that
    failure is a finding about the section like any other.
    """

    async def refused_upload(*_args, **_kwargs):
        raise RuntimeError("DashScope upload policy missing max_file_size_mb")

    monkeypatch.setattr(
        media_transport,
        "upload_local_file_to_dashscope_temp",
        refused_upload,
    )

    with pytest.raises(ValidationError, match="max_file_size_mb"):
        asyncio.run(
            _validate_section_connectivity(
                "asr",
                {
                    "asr": {
                        "enabled": True,
                        "base_url": "https://dashscope.aliyuncs.com/api/v1",
                        "api_key": "sk-test",
                        "model_name": "fun-asr",
                        "protocol": "DashScope Fun-ASR",
                        "provider": "fun-asr",
                    },
                },
            ),
        )


class _FakeClient:
    """Records the request ``_send_probe`` would put on the wire."""

    def __init__(self) -> None:
        self.calls: list = []

    async def get(self, url, *, headers, params):
        self.calls.append(("GET", url, headers, params, None, None))
        return "response"

    async def post(self, url, *, headers, json=None, data=None, files=None):
        self.calls.append(("POST", url, headers, json, data, files))
        return "response"


def test_send_probe_shapes_the_request_per_probe_marker() -> None:
    """One sender for all three probe shapes, so none can drift apart."""

    client = _FakeClient()
    probe_url = "https://example.test/v1"
    auth = {"Content-Type": "application/json", "Authorization": "Bearer k"}
    for payload in (
        {"_get_probe": True, "action": "getPolicy"},
        {
            "_multipart_probe": {
                "data": {"model": "whisper-1"},
                "files": {"file": ("a.wav", b"\x00", "audio/wav")},
            },
        },
        {"model": "qwen-plus"},
    ):
        asyncio.run(
            _send_probe(client, probe_url, dict(auth), payload),
        )

    verb, _url, headers, params, _json, _files = client.calls[0]
    assert (verb, headers, params) == (
        "GET",
        {"Authorization": "Bearer k"},
        {"action": "getPolicy"},
    )

    verb, _url, headers, sent_json, data, files = client.calls[1]
    # httpx has to write the multipart boundary itself.
    assert verb == "POST"
    assert "Content-Type" not in headers
    assert sent_json is None
    assert data == {"model": "whisper-1"}
    assert files == {"file": ("a.wav", b"\x00", "audio/wav")}

    verb, _url, headers, sent_json, data, files = client.calls[2]
    assert (verb, sent_json, data, files) == (
        "POST",
        {"model": "qwen-plus"},
        None,
        None,
    )
    assert headers["Content-Type"] == "application/json"


class _FakeStream:
    """Stands in for httpx's network stream, naming the connected peer."""

    def __init__(self, peer: str) -> None:
        self.peer = peer

    def get_extra_info(self, key: str):
        return (self.peer, 443) if key == "server_addr" else None


class _FakeResponse:
    """A response whose peer is known, or that has no network stream."""

    def __init__(self, peer: str | None) -> None:
        self.extensions = (
            {} if peer is None else {"network_stream": _FakeStream(peer)}
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/iam/",
        # AWS answers IPv6 from a ULA, Alibaba Cloud from the shared
        # address space: neither is link-local, so a range check alone
        # would let both through.
        "http://[fd00:ec2::254]/latest/meta-data/",
        "http://100.100.100.200/latest/meta-data/",
        "http://169.254.1.1:8080/v1/chat/completions",
        "http://[fe80::1]/v1",
    ],
)
def test_probe_destination_refuses_the_metadata_service(url: str) -> None:
    """Cloud metadata hands instance credentials to anything that asks.

    No model endpoint is ever served from those addresses, and a probe
    carries the configured API key, so reaching one would leak the key and
    read back the instance's own role credentials.
    """

    with pytest.raises(ProbeDestinationError, match="元数据"):
        _assert_probe_destination(url)


def test_probe_destination_error_is_a_value_error() -> None:
    """A refused destination must stay an ``ok=False`` answer, not a 500.

    ``test_model_connection`` catches ``(httpx.HTTPError, ValueError,
    RuntimeError)`` and renders the message as a diagnostic. Re-parenting
    this exception onto ``domain.errors.ValidationError`` would turn the
    endpoint whose job is explaining a broken configuration into a server
    error, which is the same trap the ASR upload refusal fell into.
    """

    assert issubclass(ProbeDestinationError, ValueError)


@pytest.mark.parametrize(
    "url",
    [
        # Self-hosted gateways are a first-class Creator setup.
        "http://localhost:11434/v1/chat/completions",
        "http://127.0.0.1:8000/v1",
        "http://[::1]:1234/v1",
        "http://192.168.1.20:30010/v1/videos",
        "http://10.0.0.8/v1",
        "https://dashscope.aliyuncs.com/api/v1",
        "https://api.openai.com/v1/audio/transcriptions",
    ],
)
def test_probe_destination_allows_operator_configured_endpoints(
    url: str,
) -> None:
    """Loopback and RFC1918 targets must keep probing successfully.

    ``safe_remote_download.require_public_ip`` rejects every non-global
    address, but it guards URLs a provider handed us. A probe target is the
    Base URL the operator typed into the model form, so applying that policy
    here would break every Ollama, vLLM and SGLang deployment the moment its
    connection is tested.
    """

    _assert_probe_destination(url)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://127.0.0.1:25/_",
        "ftp://example.test/v1",
        "/v1/chat/completions",
        "https://dashscope.aliyuncs.com@evil.example/v1",
        "https://user:secret@example.test/v1",
    ],
)
def test_probe_destination_refuses_other_schemes_and_credentials(
    url: str,
) -> None:
    """Only a plain http(s) host the operator named may be probed.

    ``https://dashscope.aliyuncs.com@evil.example`` reads as a trusted host
    in a log while the request actually goes to evil.example with the
    bearer key attached.
    """

    with pytest.raises(ProbeDestinationError):
        _assert_probe_destination(url)


def test_probe_peer_refuses_a_metadata_socket() -> None:
    """The connected peer is checked, not just the hostname.

    A pre-flight lookup and the connection are separate resolutions, so a
    hostname can answer with a public address and then land on the metadata
    service. The peer the socket reached cannot lie about that.
    """

    for peer in ("169.254.169.254", "fd00:ec2::254", "100.100.100.200"):
        with pytest.raises(ProbeDestinationError, match="元数据"):
            _assert_probe_peer(_FakeResponse(peer))

    # A public peer and a loopback gateway both pass, and a transport with
    # no network stream (a test double, in-process ASGI) has no peer to
    # verify, so it is skipped rather than failing every probe.
    _assert_probe_peer(_FakeResponse("93.184.216.34"))
    _assert_probe_peer(_FakeResponse("127.0.0.1"))
    _assert_probe_peer(_FakeResponse(None))
    _assert_probe_peer("response")


def test_send_probe_refuses_before_any_request_is_made() -> None:
    """A literal link-local target never reaches the wire.

    The credential is in the headers argument, so the point of checking
    first is that nothing carrying it is ever sent.
    """

    client = _FakeClient()

    with pytest.raises(ProbeDestinationError):
        asyncio.run(
            _send_probe(
                client,
                "http://169.254.169.254/latest/meta-data/",
                {"Authorization": "Bearer sk-live"},
                {"model": "qwen-plus"},
            ),
        )

    assert not client.calls


def test_send_probe_rejects_a_response_from_a_metadata_peer() -> None:
    """A hostname resolving onto the metadata service is caught too."""

    class _PeerClient(_FakeClient):
        async def post(
            self,
            url,
            *,
            headers,
            json=None,
            data=None,
            files=None,
        ):
            await super().post(
                url,
                headers=headers,
                json=json,
                data=data,
                files=files,
            )
            return _FakeResponse("100.100.100.200")

    client = _PeerClient()

    with pytest.raises(ProbeDestinationError, match="元数据"):
        asyncio.run(
            _send_probe(
                client,
                "https://gateway.example/v1",
                {},
                {"model": "qwen-plus"},
            ),
        )

    # The request did go out, which is exactly why the peer check exists.
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    ("base_url", "model_name"),
    [
        ("https://dashscope.aliyuncs.com/api/v1", "wan2.7-r2v"),
        (
            "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1",
            "wan3.0-video",
        ),
        (
            "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1",
            "kling/kling-v3-video-generation",
        ),
    ],
)
def test_bailian_video_probe_uses_configured_root_without_billing(
    base_url,
    model_name,
) -> None:
    url, headers, payload = _probe_payload(
        _request(
            type="video",
            base_url=base_url,
            model_name=model_name,
            protocol="DashScope（百炼）",
            provider=None,
        ),
    )

    assert url == f"{base_url}/tasks/11111111-1111-4111-8111-111111111111"
    assert payload == {"_get_probe": True}
    assert "X-DashScope-Async" not in headers


@pytest.mark.parametrize(
    ("type_", "model_name"),
    [("image", "wan2.7-image-pro"), ("video", "happyhorse-1.1")],
)
def test_token_plan_probe_uses_models_endpoint(type_, model_name) -> None:
    url, headers, payload = _probe_payload(
        _request(
            type=type_,
            model_name=model_name,
            protocol="Aliyun Token Plan",
            base_url="https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1",
            provider=None,
        ),
    )
    assert url == (
        "https://token-plan.cn-beijing.maas.aliyuncs.com"
        "/compatible-mode/v1/models"
    )
    assert payload == {"_get_probe": True}
    assert headers["Authorization"] == "Bearer sk-test"


@pytest.mark.parametrize(
    ("protocol", "base_url", "model_name", "expected_url", "expected_params"),
    [
        (
            "Google Gemini（Veo）",
            "https://generativelanguage.googleapis.com/v1beta",
            "veo-3.1-generate-preview",
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "veo-3.1-generate-preview",
            {},
        ),
        (
            "MiniMax（海螺）",
            "https://api.minimax.io",
            "MiniMax-Hailuo-2.3",
            "https://api.minimax.io/v1/query/video_generation",
            {"task_id": "creator-connection-probe"},
        ),
        (
            "Kling（可灵官方）",
            "https://api-singapore.klingai.com",
            "kling-2.6",
            "https://api-singapore.klingai.com/tasks",
            {"task_ids": "creator-connection-probe"},
        ),
        (
            "Vidu（官方）",
            "https://api.vidu.com",
            "viduq3-mix",
            "https://api.vidu.com/ent/v2/credits",
            {"show_detail": "false"},
        ),
    ],
)
def test_direct_video_probes_are_read_only_and_protocol_specific(
    protocol,
    base_url,
    model_name,
    expected_url,
    expected_params,
) -> None:
    url, headers, payload = _probe_payload(
        _request(
            type="video",
            protocol=protocol,
            base_url=base_url,
            model_name=model_name,
            provider=None,
        ),
    )

    assert url == expected_url
    assert payload.pop("_get_probe") is True
    assert payload == expected_params
    if protocol.startswith("Google"):
        assert headers["x-goog-api-key"] == "sk-test"
        assert "Authorization" not in headers
    elif protocol.startswith("Vidu"):
        assert headers["Authorization"] == "Token sk-test"
    else:
        assert headers["Authorization"] == "Bearer sk-test"


@pytest.mark.parametrize(
    ("model_name", "protocol"),
    [
        ("private-video-gateway", "DashScope（百炼）"),
        ("doubao-seedance-2.0-pro", "Volcano Engine（火山引擎）"),
        ("doubao-seedance-2.0-lite", "Volcano Engine（火山引擎）"),
    ],
)
def test_video_probe_rejects_unknown_model_before_network(
    model_name,
    protocol,
) -> None:
    with pytest.raises(ValueError, match="VIDEO_MODEL_CAPABILITY_UNKNOWN"):
        _probe_payload(
            _request(
                type="video",
                model_name=model_name,
                protocol=protocol,
                provider=None,
            ),
        )


class _Resp:
    def __init__(self, body, *, valid_json=True):
        self._body = body
        self._valid = valid_json

    def json(self):
        if not self._valid:
            raise ValueError("not json")
        return self._body


@pytest.mark.parametrize(
    ("body", "valid", "expect_fragment"),
    [
        # Config-level failures must surface even under HTTP 200.
        (
            {"base_resp": {"status_code": 1004, "status_msg": "login fail"}},
            True,
            "1004",
        ),
        ({"base_resp": {"status_code": 2049}}, True, "2049"),
        ({"base_resp": {"status_code": 1008}}, True, "1008"),
        # A throwaway task_id legitimately returns non-zero codes for a valid
        # key; those must NOT fail the connection probe.
        (
            {
                "base_resp": {
                    "status_code": 2013,
                    "status_msg": "task not found",
                },
            },
            True,
            None,
        ),
        ({"base_resp": {"status_code": 0}}, True, None),
        ({"status": "ok"}, True, None),
        ("", False, None),
    ],
)
def test_minimax_base_resp_error_surfaces_http200_failures(
    body,
    valid,
    expect_fragment,
) -> None:
    msg = _minimax_base_resp_error(_Resp(body, valid_json=valid))
    if expect_fragment is None:
        assert msg is None
    else:
        assert expect_fragment in msg


def test_minimax_image_probe_avoids_the_openai_models_path() -> None:
    # MiniMax has no GET /models/{id}; falling through to the OpenAI probe
    # surfaced an nginx HTML 404. It must hit the free task-query endpoint,
    # derived from whichever regional host the user configured.
    url, headers, payload = _probe_payload(
        _request(
            type="image",
            protocol="MiniMax（国内站）",
            base_url="https://api.minimax.cn",
            model_name="image-01",
            provider=None,
        ),
    )
    assert url == "https://api.minimax.cn/v1/query/video_generation"
    assert "/models/" not in url
    assert payload.pop("_get_probe") is True
    assert payload == {"task_id": "creator-connection-probe"}
    assert headers["Authorization"] == "Bearer sk-test"


def test_openai_image_probe_still_uses_model_retrieve() -> None:
    url, _headers, payload = _probe_payload(
        _request(
            type="image",
            protocol="OpenAI 协议",
            base_url="https://api.openai.com/v1",
            model_name="gpt-image-2",
            provider=None,
        ),
    )
    assert url == "https://api.openai.com/v1/models/gpt-image-2"
    assert payload == {"_get_probe": True}


def test_llm_probe_still_posts_a_chat_ping() -> None:
    url, _headers, payload = _probe_payload(
        _request(
            type="llm",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model_name="qwen-plus",
            protocol="OpenAI 协议",
            provider=None,
        ),
    )

    assert url == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    )
    assert "_get_probe" not in payload
    assert payload["model"] == "qwen-plus"


def test_anthropic_llm_probe_uses_messages_endpoint() -> None:
    url, headers, payload = _probe_payload(
        _request(
            type="llm",
            base_url="https://api.anthropic.com",
            model_name="claude-sonnet-4-20250514",
            protocol="Anthropic Claude",
            provider=None,
        ),
    )

    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "sk-test"
    assert headers["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in headers
    assert payload["model"] == "claude-sonnet-4-20250514"
    assert "max_tokens" not in payload
    assert payload["messages"] == [
        {"role": "user", "content": "Reply with pong only."},
    ]


def test_minimax_llm_probe_uses_anthropic_format() -> None:
    url, headers, payload = _probe_payload(
        _request(
            type="llm",
            base_url="https://api.minimaxi.com/anthropic",
            model_name="MiniMax-M3",
            protocol="MiniMax",
            provider=None,
        ),
    )

    assert url == "https://api.minimaxi.com/anthropic/v1/messages"
    assert headers["x-api-key"] == "sk-test"
    assert payload["model"] == "MiniMax-M3"


def test_gemini_llm_probe_uses_generate_content() -> None:
    url, headers, payload = _probe_payload(
        _request(
            type="llm",
            base_url="https://generativelanguage.googleapis.com",
            model_name="gemini-2.5-pro",
            protocol="Google Gemini",
            provider=None,
        ),
    )

    # Gemini authenticates through the ``key=`` query parameter; a probe
    # without it always fails with 400/401/403.
    assert url == (
        "https://generativelanguage.googleapis.com"
        "/v1beta/models/gemini-2.5-pro:generateContent?key=sk-test"
    )
    assert "Authorization" not in headers
    assert payload["contents"] == [
        {"parts": [{"text": "Reply with pong only."}]},
    ]
    assert "maxOutputTokens" not in payload.get("generationConfig", {})


def test_gemini_llm_probe_omits_key_when_keyless() -> None:
    url, headers, _payload = _probe_payload(
        _request(
            type="llm",
            base_url="https://generativelanguage.googleapis.com",
            api_key="",
            model_name="gemini-2.5-pro",
            protocol="Google Gemini",
            provider=None,
            require_api_key=False,
        ),
    )

    assert url == (
        "https://generativelanguage.googleapis.com"
        "/v1beta/models/gemini-2.5-pro:generateContent"
    )
    assert "Authorization" not in headers


def test_anthropic_llm_probe_omits_key_header_when_keyless() -> None:
    _url, headers, _payload = _probe_payload(
        _request(
            type="llm",
            base_url="https://api.anthropic.com",
            api_key="",
            model_name="claude-sonnet-4-20250514",
            protocol="Anthropic Claude",
            provider=None,
            require_api_key=False,
        ),
    )

    assert "x-api-key" not in headers
    assert "Authorization" not in headers
    assert headers["anthropic-version"] == "2023-06-01"


def test_opencode_keyless_llm_probe_posts_chat_ping_without_auth() -> None:
    # OpenCode Zen serves ``*-free`` models without an API key; the probe
    # must not send an (empty) Authorization header, which the gateway
    # would reject as an invalid key.
    url, headers, payload = _probe_payload(
        _request(
            type="llm",
            base_url="https://opencode.ai/zen/v1",
            api_key="",
            model_name="nemotron-3.5-lightning-free",
            protocol="OpenCode",
            provider=None,
            require_api_key=False,
        ),
    )

    assert url == "https://opencode.ai/zen/v1/chat/completions"
    assert "Authorization" not in headers
    assert payload["model"] == "nemotron-3.5-lightning-free"


def test_anthropic_vlm_probe_converts_image_to_anthropic_format() -> None:
    url, headers, payload = _probe_payload(
        _request(
            type="vlm",
            base_url="https://api.anthropic.com",
            model_name="claude-sonnet-4-20250514",
            protocol="Anthropic Claude",
            provider=None,
        ),
    )

    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "sk-test"
    messages = payload["messages"]
    assert len(messages) == 1
    content = messages[0]["content"]
    assert isinstance(content, list)
    text_block = content[0]
    assert text_block["type"] == "text"
    assert text_block["text"] == "Reply with red only."
    image_block = content[1]
    assert image_block["type"] == "image"
    assert image_block["source"]["type"] == "base64"
    assert image_block["source"]["media_type"] == "image/png"


def test_gemini_vlm_probe_converts_image_to_inline_data() -> None:
    url, _headers, payload = _probe_payload(
        _request(
            type="vlm",
            base_url="https://generativelanguage.googleapis.com",
            model_name="gemini-2.5-pro",
            protocol="Google Gemini",
            provider=None,
        ),
    )

    assert url.endswith(
        "/v1beta/models/gemini-2.5-pro:generateContent?key=sk-test",
    )
    contents = payload["contents"]
    assert len(contents) == 1
    parts = contents[0]["parts"]
    assert len(parts) == 2
    assert parts[0] == {"text": "Reply with red only."}
    assert parts[1]["inline_data"]["mime_type"] == "image/png"
