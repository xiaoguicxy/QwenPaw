#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rollback aid: expand blob-pointer transaction snapshots back to inline JSON.

Creator versions that predate the content-addressed blob store cannot read
``{"blobRef": ...}`` pointer snapshots.  Run this tool *before* downgrading
so the whole transaction history becomes plain inline JSON again::

    # one or more project roots (directories containing runtime/transactions)
    python scripts/unmigrate_transaction_snapshots.py <project_root> [...]

    # or every project under a Creator data root
    python scripts/unmigrate_transaction_snapshots.py \
        --data-root ~/.qwenpaw/creator-runtime

Use the same interpreter that runs the app (its venv provides pydantic and
the backend package layout).  The tool is read-safe: pointers whose blob is
missing or corrupt are kept untouched and reported, and the leftover blob
files are reclaimed by the regular startup sweep of the *new* version if you
upgrade again.  Exit codes: 0 = everything expanded, 2 = some pointers had
to be kept (downgrade would lose those transactions' snapshots).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from services.project_files.transaction_gc import (  # noqa: E402
    unmigrate_blob_transaction_snapshots,
)


def _project_roots(positional: list[Path], data_root: Path | None) -> list[Path]:
    roots: list[Path] = []
    for candidate in positional:
        if (candidate / "runtime" / "transactions").is_dir():
            roots.append(candidate)
        else:
            print(f"skip {candidate}: no runtime/transactions", file=sys.stderr)
    if data_root is not None:
        if not data_root.is_dir():
            print(f"data root not found: {data_root}", file=sys.stderr)
            sys.exit(2)
        for child in sorted(data_root.iterdir()):
            if (child / "runtime" / "transactions").is_dir():
                roots.append(child)
    # Deduplicate while preserving order.
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = root.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(root)
    return unique


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "project_roots",
        nargs="*",
        type=Path,
        help="project directories containing runtime/transactions",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="Creator data root; every project inside it is processed",
    )
    args = parser.parse_args()
    roots = _project_roots(args.project_roots, args.data_root)
    if not roots:
        parser.error("no project roots given (positional or --data-root)")
    total_expanded = 0
    total_failed = 0
    for root in roots:
        expanded, failed = unmigrate_blob_transaction_snapshots(root)
        total_expanded += expanded
        total_failed += failed
        print(f"{root}: expanded {expanded} pointer(s), kept {failed}")
    print(f"total: expanded {total_expanded}, kept {total_failed}")
    return 2 if total_failed else 0


if __name__ == "__main__":
    sys.exit(main())
