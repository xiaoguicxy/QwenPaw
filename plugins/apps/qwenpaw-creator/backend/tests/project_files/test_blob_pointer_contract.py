# -*- coding: utf-8 -*-
"""Contract guard: transaction snapshots must be read through the dual-format
helpers.

Since the blob store, a transaction snapshot file (base/candidate/latest/
final.json) may hold either a full inline document or a tiny
``{"blobRef": ...}`` pointer.  Every production read must therefore go
through ``read_document_ref*`` and every write through
``write_document_ref``; a raw ``AtomicJsonRecordStore`` read of a pointer
would resurrect the pre-blob parsing bug this guard exists to prevent.

The scan is a filename-literal heuristic: any services/ or api/ module that
mentions a snapshot filename must also reference one of the dual-format
helpers, unless it is whitelisted with a reason.  transaction_gc legitimately
reads raw bytes — it inspects the *format* (pointer vs inline) to migrate,
never to consume documents.
"""

from __future__ import annotations

from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]

_POINTER_FILENAMES = (
    '"base.json"',
    '"candidate.json"',
    '"latest.json"',
    '"final.json"',
)
_DUAL_FORMAT_HELPERS = ("read_document_ref", "write_document_ref")
_WHITELIST = {
    "transaction_gc.py": (
        "reads raw bytes to classify pointer vs inline format for migration, "
        "sweep and unmigration; never exposes documents to consumers"
    ),
}


def _production_modules() -> list[Path]:
    modules: list[Path] = []
    for root in ("services", "api"):
        modules.extend(sorted((_BACKEND / root).rglob("*.py")))
    assert modules, "production module scan found nothing; layout changed?"
    return modules


def test_snapshot_readers_use_dual_format_helpers() -> None:
    offenders = []
    for module in _production_modules():
        source = module.read_text(encoding="utf-8")
        if not any(name in source for name in _POINTER_FILENAMES):
            continue
        if module.name in _WHITELIST:
            continue
        if not any(helper in source for helper in _DUAL_FORMAT_HELPERS):
            offenders.append(str(module.relative_to(_BACKEND)))
    assert not offenders, (
        "these modules touch transaction snapshot files without the "
        f"dual-format helpers (or a whitelist reason): {offenders}"
    )


def test_whitelist_entries_still_exist_and_need_it() -> None:
    """A stale whitelist would silently disable the guard for a real file."""

    for name in _WHITELIST:
        matches = [
            module
            for module in _production_modules()
            if module.name == name
        ]
        assert matches, f"whitelisted module vanished: {name}"
        source = matches[0].read_text(encoding="utf-8")
        assert any(pointer in source for pointer in _POINTER_FILENAMES), (
            f"whitelisted module no longer mentions snapshot files: {name}"
        )
