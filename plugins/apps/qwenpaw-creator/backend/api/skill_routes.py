# -*- coding: utf-8 -*-
"""Skill listing, content view, save, toggle and delete endpoints.

Backs the "Skills" pane of the model-configuration modal. User skills are
content-based (name + SKILL.md body) and persisted under the data root;
builtin skills ship with the code tree and are read-only here.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile

from services.external_skills import (
    _BUILTIN_SKILLS_ROOT,
    LoadedSkill,
    SkillExecutionError,
    load_skills,
    parse_skill_md,
    read_skill_content,
)
from services.media_files.user_skills import (
    UserSkillError,
    delete_user_skill,
    import_skills_from_zip_bytes,
    save_user_skill,
    set_user_skill_enabled,
)
from services.storage_root import CreatorDataRootError

router = APIRouter(prefix="/skills", tags=["skills"])


def _is_builtin(skill: LoadedSkill) -> bool:
    try:
        skill.root.resolve().relative_to(_BUILTIN_SKILLS_ROOT.resolve())
        return True
    except (ValueError, OSError):
        return False


def _resolve_description(skill: LoadedSkill) -> str | None:
    """Config description first, else the SKILL.md front matter.

    Builtin entries are constructed without a description, so their
    front matter is the source of truth. Mirrors _skill_context_block
    so the UI shows the same text the agent sees in its prompt.
    """

    configured = (skill.entry.description or "").strip()
    if configured:
        return configured
    try:
        parsed = parse_skill_md(skill.skill_md).get("description", "")
    except Exception:
        parsed = ""
    return parsed.strip() or None


def _item(skill: LoadedSkill) -> dict[str, Any]:
    return {
        "name": skill.entry.name,
        "description": _resolve_description(skill),
        "enabled": skill.entry.enabled,
        "status": skill.status,
        "reason": skill.reason,
        "builtin": _is_builtin(skill),
    }


def _data_root_guard(action):
    try:
        return action()
    except CreatorDataRootError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except UserSkillError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
async def list_skills() -> dict[str, Any]:
    items = await asyncio.to_thread(
        lambda: [_item(skill) for skill in load_skills()],
    )
    return {"items": items}


@router.get("/{name}/content")
async def get_skill_content(name: str) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(read_skill_content, skill_name=name)
    except SkillExecutionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class SaveSkillRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1)


@router.post("")
async def save_skill(request: SaveSkillRequest) -> dict[str, Any]:
    entry = _data_root_guard(
        lambda: save_user_skill(request.name.strip(), request.content),
    )
    return {"ok": True, "name": entry.name}


@router.post("/upload")
async def upload_skill_zip(request: Request) -> dict[str, Any]:
    """Import one or more skills from an uploaded zip (SKILL.md bundle)."""

    form = await request.form()
    upload = next(
        (
            value
            for _, value in form.multi_items()
            if isinstance(value, UploadFile)
        ),
        None,
    )
    if upload is None:
        raise HTTPException(status_code=400, detail="未找到上传的文件")
    filename = Path(upload.filename or "").name
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="仅支持 .zip 文件")
    data = await upload.read()
    return await asyncio.to_thread(
        _data_root_guard,
        lambda: import_skills_from_zip_bytes(data),
    )


class ToggleSkillRequest(BaseModel):
    enabled: bool


@router.patch("/{name}")
async def toggle_skill(
    name: str,
    request: ToggleSkillRequest,
) -> dict[str, Any]:
    changed = _data_root_guard(
        lambda: set_user_skill_enabled(name, request.enabled),
    )
    if not changed:
        raise HTTPException(status_code=404, detail=f"技能不存在或为内置: {name}")
    return {"ok": True, "name": name, "enabled": request.enabled}


@router.delete("/{name}")
async def remove_skill(name: str) -> dict[str, Any]:
    deleted = _data_root_guard(lambda: delete_user_skill(name))
    if not deleted:
        raise HTTPException(status_code=404, detail=f"技能不存在或为内置: {name}")
    return {"deleted": name}


__all__ = ["router"]
