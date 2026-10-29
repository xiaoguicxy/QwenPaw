# -*- coding: utf-8 -*-
"""Connectivity probes must stay zero-cost for non-chat models.

Submitting real tasks (ASR/video) as a "ping" is billable and rejected
by the DashScope gateway with 403; probes use free read-only APIs.
"""

# pylint: disable=unused-argument
# The stub transport below mirrors the httpx call signature so the route under
# test can pass `params=` / `json=` exactly as it would on the wire.

from __future__ import annotations

import asyncio
import types

import pytest

from api.model_routes import (
    _minimax_base_resp_error,
    _non_secret_probe_params,
    _probe_payload,
    _probe_target_refusal,
)
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


def test_dashscope_probe_uses_upload_policy() -> None:
    url, headers, payload = _probe_payload(_request())
    assert url == "https://dashscope.aliyuncs.com/api/v1/uploads"
    assert payload == {
        "_get_probe": True,
        "action": "getPolicy",
        "model": "fun-asr",
    }
    assert "X-DashScope-Async" not in headers


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


# ---------------------------------------------------------------------------
# CWE-522: the probe must not launder a saved credential toward a foreign host
# ---------------------------------------------------------------------------


_SECTIONS = (
    "llm",
    "vlm",
    "asr",
    "tts",
    "s2v",
    "image",
    "video",
    "embedding",
)


def _stub_saved_config(monkeypatch, **sections: dict) -> None:
    """Pretend the operator already saved these provider endpoints.

    ``_stub_saved_config(monkeypatch, llm={"base_url": ..., "api_key": ...})``;
    a section left out reads as unsaved.
    """
    from api import model_routes

    defaults = {
        "base_url": "",
        "api_key": "",
        "model_name": "glm-5",
        "protocol": "OpenAI Chat",
        "provider": None,
        "voice": "",
        "reuse_llm_key": False,
        "reuse_vlm_key": False,
    }
    saved = {
        name: types.SimpleNamespace(
            **{**defaults, **sections.get(name, {})},
        )
        for name in _SECTIONS
    }

    def _load_saved():
        return types.SimpleNamespace(**saved)

    monkeypatch.setattr(model_routes, "load_model_config", _load_saved)


def _no_dispatch_client(monkeypatch) -> None:
    """Make any outbound probe fail the test instead of reaching a socket."""
    from api import model_routes

    def _factory(*args, **kwargs):
        raise AssertionError("the probe must not be dispatched at all")

    monkeypatch.setattr(model_routes.httpx, "AsyncClient", _factory)


def _capture_dispatch(monkeypatch) -> list:
    """Record the URL and headers a probe would send, then report success."""
    from api import model_routes

    sent: list[tuple[str, dict[str, str]]] = []

    class _Response:
        is_success = True

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def get(self, url, headers=None, params=None):
            sent.append((str(url), dict(headers or {})))
            return _Response()

        async def post(self, url, headers=None, json=None):
            sent.append((str(url), dict(headers or {})))
            return _Response()

    monkeypatch.setattr(model_routes.httpx, "AsyncClient", _Client)
    return sent


def test_saved_key_is_never_sent_to_a_request_selected_host(
    monkeypatch,
) -> None:
    """Masked key + foreign Base URL would post the stored secret elsewhere."""
    from api import model_routes

    _stub_saved_config(
        monkeypatch,
        llm={
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "sk-saved",
        },
    )
    _no_dispatch_client(monkeypatch)
    request = ModelConnectionTestRequest(
        type="llm",
        base_url="https://collector.example.net/v1",
        api_key=model_routes.SECRET_MASK,
        model_name="glm-5",
        protocol="OpenAI Chat",
        provider=None,
        require_api_key=True,
    )

    response = asyncio.run(model_routes.test_model_connection(request))

    assert not response.ok
    assert "安全限制" in response.error
    assert "collector.example.net" in response.error


@pytest.mark.parametrize(
    ("supplied_key", "stored_key", "probed", "homes", "want_refusal"),
    [
        # The caller already holds this key, so testing a brand-new gateway
        # against it stays possible - that is the point of this endpoint.
        (
            "sk-typed",
            "",
            "https://gateway.example.com/v1",
            ("https://dashscope.aliyuncs.com/v1",),
            False,
        ),
        # Same origin, different path: the compatible-mode and api roots are
        # one endpoint family, and the key belongs to both.
        (
            "",
            "sk-saved",
            "https://dashscope.aliyuncs.com/api/v1",
            ("https://dashscope.aliyuncs.com/compatible-mode/v1",),
            False,
        ),
        # Host comparison is case-insensitive.
        (
            "",
            "sk-saved",
            "https://DASHSCOPE.aliyuncs.com/v1",
            ("https://dashscope.aliyuncs.com/v1",),
            False,
        ),
        # A saved key may not travel to a host chosen per-request.
        (
            "",
            "sk-saved",
            "http://127.0.0.1:9999/v1",
            ("https://dashscope.aliyuncs.com/v1",),
            True,
        ),
        # ... nor one scheme downgrade away, where it would leave in cleartext.
        (
            "",
            "sk-saved",
            "http://dashscope.aliyuncs.com/v1",
            ("https://dashscope.aliyuncs.com/v1",),
            True,
        ),
        # ... nor to another service listening on the same host.
        (
            "",
            "sk-saved",
            "http://127.0.0.1:8000/v1",
            ("http://127.0.0.1:11434/v1",),
            True,
        ),
        # ``casefold()`` reads these two as equal; HTTPX resolves them to
        # different domains, so the guard must not treat them as one home.
        (
            "",
            "sk-saved",
            "https://aßistant.example/v1",
            ("https://assistant.example/v1",),
            True,
        ),
        # An explicit port must match the saved one, defaults included.
        (
            "",
            "sk-saved",
            "https://dashscope.aliyuncs.com:443/v1",
            ("https://dashscope.aliyuncs.com/v1",),
            False,
        ),
        # Nothing is being sent, so there is nothing to bind: a keyless probe
        # is a plain URL check.
        (
            "",
            "",
            "http://127.0.0.1:11434/v1",
            ("http://localhost:11434/v1",),
            False,
        ),
        # A credential with no recorded home must not borrow the request's.
        (
            "",
            "sk-env",
            "https://other.example/v1",
            ("",),
            True,
        ),
    ],
)
def test_probe_target_guard_boundaries(
    supplied_key,
    stored_key,
    probed,
    homes,
    want_refusal,
) -> None:
    refusal = _probe_target_refusal(
        probed,
        "llm",
        supplied_key=supplied_key,
        stored_key=stored_key,
        trusted_base_urls=homes,
    )

    assert bool(refusal) is want_refusal


def test_probe_target_guard_rejects_non_http_targets() -> None:
    refusal = _probe_target_refusal(
        "file:///etc/passwd",
        "llm",
        supplied_key="sk-typed",
        stored_key="",
        trusted_base_urls=(),
    )

    assert "http" in refusal


def test_probe_target_guard_survives_an_unparseable_url() -> None:
    """A malformed address is a config error, never an escaping ValueError."""
    refusal = _probe_target_refusal(
        "https://[::1",
        "llm",
        supplied_key="sk-typed",
        stored_key="",
        trusted_base_urls=("https://[::1",),
    )

    assert "配置不完整" in refusal


def test_malformed_probe_url_returns_a_config_error_not_a_500(
    monkeypatch,
) -> None:
    """The guard parses before the route's try block, so it must not raise."""
    from api import model_routes

    _stub_saved_config(
        monkeypatch,
        llm={
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "sk-saved",
        },
    )
    _no_dispatch_client(monkeypatch)
    request = ModelConnectionTestRequest(
        type="asr",
        base_url="https://[::1",
        api_key="sk-test",
        model_name="fun-asr",
        protocol="DashScope Fun-ASR",
        provider="fun-asr",
    )

    response = asyncio.run(model_routes.test_model_connection(request))

    assert not response.ok
    assert "配置不完整" in response.error


def test_keyless_probe_of_another_host_still_reaches_the_provider(
    monkeypatch,
) -> None:
    """With no credential anywhere, testing a local server is not a leak."""
    from api import model_routes

    _stub_saved_config(
        monkeypatch,
        llm={"base_url": "http://localhost:11434/v1", "api_key": ""},
    )
    sent = _capture_dispatch(monkeypatch)
    request = ModelConnectionTestRequest(
        type="llm",
        base_url="http://127.0.0.1:11434/v1",
        api_key="",
        model_name="llama3",
        protocol="OpenAI Chat",
        provider=None,
        require_api_key=False,
    )

    response = asyncio.run(model_routes.test_model_connection(request))

    assert response.ok
    assert len(sent) == 1
    assert sent[0][0].startswith("http://127.0.0.1:11434")
    assert "Authorization" not in sent[0][1]


def test_environment_key_without_a_saved_home_is_refused(
    monkeypatch,
) -> None:
    """A key with no recorded origin must not adopt the request's target."""
    from api import model_routes

    _stub_saved_config(monkeypatch, llm={"base_url": "", "api_key": "sk-env"})
    _no_dispatch_client(monkeypatch)
    request = ModelConnectionTestRequest(
        type="llm",
        base_url="https://other.example/v1",
        api_key="",
        model_name="glm-5",
        protocol="OpenAI Chat",
        provider=None,
    )

    response = asyncio.run(model_routes.test_model_connection(request))

    assert not response.ok
    assert "没有记录归属地址" in response.error


@pytest.mark.parametrize(
    ("section", "owner", "owner_key", "model_name", "protocol"),
    [
        ("asr", "llm", "sk-llm", "whisper-1", "OpenAI Whisper"),
        (
            "embedding",
            "vlm",
            "sk-vlm",
            "qwen3-vl-embedding",
            "DashScope Multimodal",
        ),
    ],
)
def test_saved_cross_host_key_reuse_still_probes_its_own_host(
    monkeypatch,
    section,
    owner,
    owner_key,
    model_name,
    protocol,
) -> None:
    """An operator-saved reuse pair is authorised, per each half's own host.

    The route previously re-anchored a reused key to the owner endpoint only,
    which refused a config that was already saved and never edited by this
    request - and the hint to "save it first" could not help, because it was.
    """
    from api import model_routes

    saved_owner = {
        "base_url": f"https://{owner}.example/v1",
        "api_key": owner_key,
    }
    saved_section = {
        "base_url": "https://media.example/v1",
        "api_key": "",
        "model_name": model_name,
        "protocol": protocol,
        "reuse_llm_key": section == "asr",
        "reuse_vlm_key": section == "embedding",
    }
    _stub_saved_config(
        monkeypatch,
        **{owner: saved_owner, section: saved_section},
    )
    sent = _capture_dispatch(monkeypatch)
    request = ModelConnectionTestRequest(
        type=section,
        base_url="",
        api_key="",
        model_name=model_name,
        protocol=protocol,
        provider=None,
    )

    response = asyncio.run(model_routes.test_model_connection(request))

    assert response.ok, response.error
    assert len(sent) == 1
    assert sent[0][0].startswith("https://media.example")
    assert sent[0][1]["Authorization"] == f"Bearer {owner_key}"


def test_saved_key_is_not_tricked_by_an_idna_lookalike_host(
    monkeypatch,
) -> None:
    """A lookalike host that casefolds to the saved one is still foreign."""
    from api import model_routes

    _stub_saved_config(
        monkeypatch,
        llm={
            "base_url": "https://assistant.example/v1",
            "api_key": "sk-saved",
        },
    )
    _no_dispatch_client(monkeypatch)
    request = ModelConnectionTestRequest(
        type="llm",
        base_url="https://aßistant.example/v1",
        api_key=model_routes.SECRET_MASK,
        model_name="glm-5",
        protocol="OpenAI Chat",
        provider=None,
    )

    response = asyncio.run(model_routes.test_model_connection(request))

    assert not response.ok
    assert "安全限制" in response.error


def test_get_probe_params_never_carry_the_credential() -> None:
    params = _non_secret_probe_params(
        {"model": "fun-asr", "token": "sk-secret", "flag": "false"},
        "sk-secret",
    )

    assert params == {"model": "fun-asr", "flag": "false"}
    assert _non_secret_probe_params({"model": "fun-asr"}, "") == {
        "model": "fun-asr",
    }
