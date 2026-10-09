# -*- coding: utf-8 -*-
"""User-created Creator skills persisted to the data root.

Content-based (name + SKILL.md body), mirroring the QwenPaw skill-add
UX. The body is written to ``$CREATOR_DATA_ROOT/skills/<name>/SKILL.md``
and the directory is registered in ``skills_config.json`` so the existing
loader (``services.external_skills.load_skills``) picks it up unchanged.

Skills are domain knowledge only: this module never executes skill code
and never grants a skill any capability beyond the SKILL.md text.
"""
from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from models.config import load_skills_config, write_skills_config
from schemas.skills import SkillEntry
from services.external_skills import (
    _BUILTIN_SKILLS_ROOT,
    _clear_load_cache,
    parse_skill_md,
)
from services.runtime_files.atomic_store import atomic_replace_bytes
from services.storage_root import require_creator_data_root

_USER_SKILL_DIR_NAME = "skills"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
# Skills are plain-text domain knowledge; 32 MiB is far beyond any real
# SKILL.md bundle and stops accidental huge uploads before extraction.
_MAX_ZIP_BYTES = 32 * 1024 * 1024


class UserSkillError(ValueError):
    """A user skill save/delete request that must be refused."""


def _skills_root() -> Path:
    directory = require_creator_data_root() / _USER_SKILL_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _validated_dir(name: str) -> Path:
    # The regex rejects separators and "..", so the resolved directory can
    # never escape the managed skills root.
    if not _NAME_RE.match(name or ""):
        raise UserSkillError(
            "技能名只能包含小写字母、数字、点、下划线、连字符，且以字母或数字开头",
        )
    return _skills_root() / name


def save_user_skill(name: str, content: str) -> SkillEntry:
    """Write SKILL.md content and register/refresh its config entry."""

    directory = _validated_dir(name)
    try:
        parse_skill_md(content)
    except Exception as exc:
        raise UserSkillError(
            f"SKILL.md 格式无效（需要 --- front matter --- 头部）：{exc}",
        ) from exc
    directory.mkdir(parents=True, exist_ok=True)
    atomic_replace_bytes(directory / "SKILL.md", content.encode("utf-8"))

    entries = list(load_skills_config())
    existing = next((item for item in entries if item.name == name), None)
    if existing is not None:
        entry = existing.model_copy(update={"path": str(directory)})
        entries = [entry if item.name == name else item for item in entries]
    else:
        entry = SkillEntry(name=name, path=str(directory), enabled=True)
        entries.append(entry)
    write_skills_config(entries)
    _clear_load_cache()
    return entry


def _locate_skill_dirs(root: Path) -> list[Path]:
    """One skill (SKILL.md at the root) or many (one per subdir)."""

    if (root / "SKILL.md").is_file():
        return [root]
    found = []
    for child in sorted(root.iterdir()):
        if (
            child.is_dir()
            and not child.is_symlink()
            and (child / "SKILL.md").is_file()
        ):
            found.append(child)
    return found


def _builtin_skill_names() -> set[str]:
    try:
        return {
            path.name
            for path in _BUILTIN_SKILLS_ROOT.iterdir()
            if path.is_dir()
        }
    except OSError:
        return set()


def import_skills_from_zip_bytes(data: bytes) -> dict:
    """Extract an uploaded zip and install each SKILL.md as a user skill.

    Reuses the Project archive extractor, so zip-slip, symlink and
    expansion-bomb members are rejected before anything touches disk. A
    zip may carry a single skill (SKILL.md at the root) or several (one
    directory each). Builtin-name collisions and invalid skills are
    skipped and reported rather than aborting the whole import.
    """

    if len(data) > _MAX_ZIP_BYTES:
        raise UserSkillError(
            f"ZIP 超过 {_MAX_ZIP_BYTES // (1024 * 1024)}MB 上限",
        )
    from domain.errors import BadRequestError
    from services.project_files.archive import extract_archive

    imported: list[str] = []
    skipped: list[dict] = []
    builtin = _builtin_skill_names()
    with tempfile.TemporaryDirectory(prefix="creator-skill-") as tmp:
        extract_dir = Path(tmp) / "extract"
        extract_dir.mkdir(mode=0o700)
        zip_path = Path(tmp) / "upload.zip"
        zip_path.write_bytes(data)
        try:
            extract_archive(zip_path, extract_dir)
        except BadRequestError as exc:
            raise UserSkillError(f"ZIP 无效：{exc}") from exc
        skill_dirs = _locate_skill_dirs(extract_dir)
        if not skill_dirs:
            raise UserSkillError(
                "ZIP 中未找到 SKILL.md（技能需包含 SKILL.md 文件）",
            )
        for directory in skill_dirs:
            label = directory.name
            try:
                content = (directory / "SKILL.md").read_text(
                    encoding="utf-8",
                )
                parsed = parse_skill_md(content)
                fm_name = (parsed.get("name") or "").strip()
                if _NAME_RE.match(fm_name):
                    name = fm_name
                elif directory != extract_dir:
                    name = directory.name
                else:
                    skipped.append(
                        {
                            "name": label,
                            "reason": "SKILL.md 缺少合规的 name 字段",
                        },
                    )
                    continue
                if name in builtin:
                    skipped.append(
                        {"name": name, "reason": "与内置技能同名"},
                    )
                    continue
                save_user_skill(name, content)
                imported.append(name)
            except (UserSkillError, ValueError, OSError) as exc:
                skipped.append({"name": label, "reason": str(exc)})
    return {"imported": imported, "skipped": skipped, "count": len(imported)}


def set_user_skill_enabled(name: str, enabled: bool) -> bool:
    """Toggle a configured (non-builtin) skill; report whether it changed."""

    entries = list(load_skills_config())
    changed = False
    for index, item in enumerate(entries):
        if item.name == name:
            entries[index] = item.model_copy(update={"enabled": enabled})
            changed = True
    if changed:
        write_skills_config(entries)
        _clear_load_cache()
    return changed


def delete_user_skill(name: str) -> bool:
    """Remove a configured skill entry and its managed directory."""

    entries = list(load_skills_config())
    remaining = [item for item in entries if item.name != name]
    if len(remaining) == len(entries):
        return False
    write_skills_config(remaining)
    # name is regex-validated (no separators / ".."), so this stays inside
    # the managed skills root.
    shutil.rmtree(_skills_root() / name, ignore_errors=True)
    _clear_load_cache()
    return True


__all__ = [
    "UserSkillError",
    "delete_user_skill",
    "import_skills_from_zip_bytes",
    "save_user_skill",
    "set_user_skill_enabled",
]
