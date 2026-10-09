# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Skill routes: discovery stays off the loop; content reads the raw file."""

from __future__ import annotations

import json
import threading

from fastapi import FastAPI
import pytest

from api import skill_routes
from api.dependencies import creator_error_handler
from domain.errors import CreatorError
from models import config
from services import external_skills

pytestmark = pytest.mark.unit

_SKILL_MD = """---
name: demo-skill
description: demo
---

# Demo
"""


def _app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(CreatorError, creator_error_handler)
    app.include_router(skill_routes.router)
    return app


@pytest.fixture(autouse=True)
def _reset_caches():
    yield
    config._clear_skills_config_cache()
    external_skills._clear_load_cache()


def _configure(tmp_path, monkeypatch, entries):
    data_root = tmp_path / "creator-data"
    (data_root / "config").mkdir(parents=True, exist_ok=True)
    (data_root / "config" / "skills_config.json").write_text(
        json.dumps({"skills": entries}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("CREATOR_DATA_ROOT", str(data_root))
    monkeypatch.delenv("CREATOR_SKILLS_CONFIG_PATH", raising=False)
    config._clear_skills_config_cache()
    external_skills._clear_load_cache()
    return data_root


def _write_demo_skill(tmp_path):
    root = tmp_path / "demo-src"
    root.mkdir()
    (root / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    return root


def test_list_skills_runs_off_the_event_loop(
    tmp_path,
    monkeypatch,
    run_scenario,
) -> None:
    """Skill discovery scans disk (and may probe node); never on the loop."""

    root = _write_demo_skill(tmp_path)
    _configure(
        tmp_path,
        monkeypatch,
        [{"name": "demo-skill", "path": str(root), "enabled": True}],
    )
    load_threads: list[int] = []
    real_load = skill_routes.load_skills

    def recording_load():
        load_threads.append(threading.get_ident())
        return real_load()

    monkeypatch.setattr(skill_routes, "load_skills", recording_load)

    async def scenario(client) -> int:
        loop_thread = threading.get_ident()
        response = await client.get("/skills")
        assert response.status_code == 200, response.text
        names = [item["name"] for item in response.json()["items"]]
        assert "demo-skill" in names
        return loop_thread

    loop_thread = run_scenario(_app(), scenario)
    assert load_threads, "list_skills must discover skills"
    assert all(thread != loop_thread for thread in load_threads)


def test_get_skill_content_runs_off_the_event_loop(
    tmp_path,
    monkeypatch,
    run_scenario,
) -> None:
    """The content read (find_skill -> load_skills) is off the loop too."""

    root = _write_demo_skill(tmp_path)
    _configure(
        tmp_path,
        monkeypatch,
        [{"name": "demo-skill", "path": str(root), "enabled": True}],
    )
    read_threads: list[int] = []
    real_read = skill_routes.read_skill_content

    def recording_read(**kwargs):
        read_threads.append(threading.get_ident())
        return real_read(**kwargs)

    monkeypatch.setattr(skill_routes, "read_skill_content", recording_read)

    async def scenario(client) -> int:
        loop_thread = threading.get_ident()
        response = await client.get("/skills/demo-skill/content")
        assert response.status_code == 200, response.text
        assert response.json()["content"] == _SKILL_MD
        return loop_thread

    loop_thread = run_scenario(_app(), scenario)
    assert read_threads, "get_skill_content must read the skill"
    assert all(thread != loop_thread for thread in read_threads)


def test_content_route_reads_a_disabled_skill(
    tmp_path,
    monkeypatch,
    api_request,
) -> None:
    """The editor reads a disabled skill's raw SKILL.md (view_skill can't)."""

    root = _write_demo_skill(tmp_path)
    _configure(
        tmp_path,
        monkeypatch,
        [{"name": "demo-skill", "path": str(root), "enabled": False}],
    )
    response = api_request(_app(), "GET", "/skills/demo-skill/content")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["content"] == _SKILL_MD
    assert body["truncated"] is False


def test_content_route_404_for_unknown_skill(
    tmp_path,
    monkeypatch,
    api_request,
) -> None:
    """An unknown skill maps SkillExecutionError to a 404."""

    _configure(tmp_path, monkeypatch, [])
    response = api_request(_app(), "GET", "/skills/nope/content")
    assert response.status_code == 404
