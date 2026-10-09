# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""User-skill config writes are serialized so concurrent edits never lose."""

from __future__ import annotations

import threading
import time

import pytest

from models import config
from services import external_skills
from services.media_files import user_skills

pytestmark = pytest.mark.unit


def _skill_md(name: str) -> str:
    return f"---\nname: {name}\ndescription: demo\n---\n\n# {name}\n"


@pytest.fixture(autouse=True)
def _reset_caches():
    yield
    config._clear_skills_config_cache()
    external_skills._clear_load_cache()


def _prepare(tmp_path, monkeypatch):
    data_root = tmp_path / "creator-data"
    (data_root / "config").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CREATOR_DATA_ROOT", str(data_root))
    monkeypatch.delenv("CREATOR_SKILLS_CONFIG_PATH", raising=False)
    config._clear_skills_config_cache()
    external_skills._clear_load_cache()
    return data_root


def test_save_toggle_delete_round_trip(tmp_path, monkeypatch) -> None:
    """The locked read-modify-write paths keep their return contracts."""

    data_root = _prepare(tmp_path, monkeypatch)
    entry = user_skills.save_user_skill("demo", _skill_md("demo"))
    assert entry.name == "demo" and entry.enabled is True
    assert (data_root / "skills" / "demo" / "SKILL.md").is_file()

    assert user_skills.set_user_skill_enabled("demo", False) is True
    states = {item.name: item.enabled for item in config.load_skills_config()}
    assert states == {"demo": False}

    assert user_skills.delete_user_skill("demo") is True
    assert not config.load_skills_config()
    assert not (data_root / "skills" / "demo").exists()
    # Idempotent refusals once the entry is gone.
    assert user_skills.delete_user_skill("demo") is False
    assert user_skills.set_user_skill_enabled("demo", True) is False


def test_concurrent_saves_do_not_lose_config_entries(
    tmp_path,
    monkeypatch,
) -> None:
    """A read-modify-write race must not drop concurrently saved skills."""

    data_root = _prepare(tmp_path, monkeypatch)

    # Widen the race window: without the module lock every worker reads the
    # same pre-save snapshot and the last write clobbers all the others.
    real_load = user_skills.load_skills_config

    def slow_load():
        entries = real_load()
        time.sleep(0.005)
        return entries

    monkeypatch.setattr(user_skills, "load_skills_config", slow_load)

    names = [f"skill-{index:02d}" for index in range(24)]
    barrier = threading.Barrier(len(names))

    def worker(name: str) -> None:
        barrier.wait()
        user_skills.save_user_skill(name, _skill_md(name))

    threads = [threading.Thread(target=worker, args=(n,)) for n in names]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    saved = {entry.name for entry in config.load_skills_config()}
    assert saved == set(names)
    for name in names:
        assert (data_root / "skills" / name / "SKILL.md").is_file()


def test_same_name_save_and_delete_never_register_a_fileless_skill(
    tmp_path,
    monkeypatch,
) -> None:
    """A delete racing a same-name save must not resurrect a file-less entry.

    The SKILL.md write and the config registration share one critical
    section, so a concurrent delete runs either wholly before or wholly
    after the save -- never between the write and the registration (which
    would leave a registered skill whose directory was just removed).
    """

    data_root = _prepare(tmp_path, monkeypatch)
    user_skills.save_user_skill("demo", _skill_md("demo"))

    # Pause the save right after it publishes SKILL.md but before it returns,
    # so the delete below is guaranteed to run inside that window.
    written = threading.Event()
    real_replace = user_skills.atomic_replace_bytes

    def slow_replace(path, data):
        real_replace(path, data)
        written.set()
        time.sleep(0.05)

    monkeypatch.setattr(user_skills, "atomic_replace_bytes", slow_replace)

    saver = threading.Thread(
        target=user_skills.save_user_skill,
        args=("demo", _skill_md("demo-v2")),
    )
    saver.start()
    assert written.wait(timeout=5.0), "save never reached the SKILL.md write"
    user_skills.delete_user_skill("demo")
    saver.join(timeout=5.0)

    registered = {item.name for item in config.load_skills_config()}
    if "demo" in registered:
        assert (data_root / "skills" / "demo" / "SKILL.md").is_file(), (
            "delete interleaved between the SKILL.md write and the config "
            "registration, resurrecting 'demo' without its file"
        )
