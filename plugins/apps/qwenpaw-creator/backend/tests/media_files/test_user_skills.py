# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""User-skill config writes are serialized so concurrent edits never lose."""

from __future__ import annotations

import io
import json
import threading
import time
import zipfile

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

    # Park the save inside its critical section (right after it publishes
    # SKILL.md) until this test releases it, so the interleaving order is
    # constructed rather than left to the scheduler honoring a fixed sleep.
    in_write = threading.Event()
    resume_write = threading.Event()
    real_replace = user_skills.atomic_replace_bytes

    def slow_replace(path, data):
        real_replace(path, data)
        in_write.set()
        assert resume_write.wait(timeout=5.0), "save was never resumed"

    monkeypatch.setattr(user_skills, "atomic_replace_bytes", slow_replace)

    saver = threading.Thread(
        target=user_skills.save_user_skill,
        args=("demo", _skill_md("demo-v2")),
    )
    deleted: list[bool] = []
    deleter = threading.Thread(
        target=lambda: deleted.append(user_skills.delete_user_skill("demo")),
    )
    saver.start()
    assert in_write.wait(timeout=5.0), "save never reached the SKILL.md write"
    deleter.start()
    deleter.join(timeout=0.2)
    assert not deleted, "delete completed while the save held the lock"
    resume_write.set()
    deleter.join(timeout=5.0)
    saver.join(timeout=5.0)

    registered = {item.name for item in config.load_skills_config()}
    if "demo" in registered:
        assert (data_root / "skills" / "demo" / "SKILL.md").is_file(), (
            "delete interleaved between the SKILL.md write and the config "
            "registration, resurrecting 'demo' without its file"
        )


def test_writes_refuse_to_drop_a_rejected_config_entry(
    tmp_path,
    monkeypatch,
) -> None:
    """An unrelated invalid entry must survive another skill's toggle.

    ``load_skills_config`` reports rejected entries as diagnostics instead of
    returning them, so a read-modify-write that ignores those diagnostics
    would erase hand-authored configuration nobody asked to change.
    """

    data_root = _prepare(tmp_path, monkeypatch)
    user_skills.save_user_skill("demo", _skill_md("demo"))
    path = data_root / "config" / "skills_config.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    # ``extra="forbid"`` makes this entry invalid: it stays visible as an
    # unavailable row but never reaches the validated subset.
    document["skills"].append(
        {
            "name": "legacy",
            "path": str(data_root / "skills" / "legacy"),
            "enabled": True,
            "timeout_seconds": 30,
        },
    )
    path.write_text(json.dumps(document), encoding="utf-8")
    config._clear_skills_config_cache()
    external_skills._clear_load_cache()

    with pytest.raises(user_skills.UserSkillError):
        user_skills.set_user_skill_enabled("demo", False)
    # The refused toggle left the valid entry untouched too.
    assert config.load_skills_config()[0].enabled is True
    document = json.loads(path.read_text(encoding="utf-8"))
    saved = {item["name"] for item in document["skills"]}
    assert saved == {"demo", "legacy"}, "the invalid entry was erased"

    with pytest.raises(user_skills.UserSkillError):
        user_skills.delete_user_skill("demo")
    assert "legacy" in path.read_text(encoding="utf-8")


def test_zip_import_reports_a_member_with_invalid_yaml_front_matter(
    tmp_path,
    monkeypatch,
) -> None:
    """One unparsable SKILL.md skips itself, not the whole upload.

    ``parse_skill_md`` raises PyYAML's ParserError for a broken flow scalar,
    which is not a ValueError, so it used to escape the per-member handler and
    fail the request after part of the batch was already installed.
    """

    _prepare(tmp_path, monkeypatch)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("a-good/SKILL.md", _skill_md("a-good"))
        archive.writestr(
            "b-bad/SKILL.md",
            "---\nname: [unclosed\ndescription: demo\n---\n\n# b\n",
        )
        archive.writestr("c-good/SKILL.md", _skill_md("c-good"))

    result = user_skills.import_skills_from_zip_bytes(payload.getvalue())

    assert sorted(result["imported"]) == ["a-good", "c-good"]
    assert [item["name"] for item in result["skipped"]] == ["b-bad"]
    assert result["count"] == 2
    registered = {item.name for item in config.load_skills_config()}
    assert registered == {"a-good", "c-good"}


def test_create_refuses_to_shadow_a_builtin_skill(
    tmp_path,
    monkeypatch,
) -> None:
    """An ordinary create cannot silently replace builtin domain knowledge.

    A skills_config.json entry with a builtin name shadows it by design, but
    that override stays a deployment decision; the ZIP importer already
    refuses builtin names, so the save entry point must not accept one either.
    """

    _prepare(tmp_path, monkeypatch)
    builtin_root = tmp_path / "builtin-skills"
    (builtin_root / "visual-asset-design").mkdir(parents=True)
    monkeypatch.setattr(
        user_skills,
        "_BUILTIN_SKILLS_ROOT",
        builtin_root,
    )

    with pytest.raises(user_skills.UserSkillError, match="内置"):
        user_skills.save_user_skill(
            "visual-asset-design",
            _skill_md("mine"),
        )
    # Refused before anything touched the managed skills root.
    shadow = tmp_path / "creator-data" / "skills" / "visual-asset-design"
    assert not shadow.exists()

    # A pre-existing same-name config entry keeps its documented override.
    config_path = tmp_path / "creator-data" / "config" / "skills_config.json"
    config_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "visual-asset-design",
                        "path": str(tmp_path / "creator-data" / "custom"),
                        "enabled": True,
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    config._clear_skills_config_cache()
    external_skills._clear_load_cache()
    entry = user_skills.save_user_skill(
        "visual-asset-design",
        _skill_md("mine"),
    )
    assert entry.name == "visual-asset-design"
