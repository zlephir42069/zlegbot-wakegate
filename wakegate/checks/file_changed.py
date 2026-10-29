"""Fire when a file or directory's SHA-256 content changes.

The first successful read records a baseline and does not fire. The same
hashes never fire again.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from wakegate.checks.base import CheckResult
from wakegate.errors import CheckError
from wakegate.fileset import list_files

_SHOW = 5


def evaluate(options: dict, state: dict, now: datetime) -> CheckResult:
    del now
    root = Path(options["path"])
    if not root.exists():
        raise CheckError(f"path does not exist: {root}")
    files = list_files(root, options.get("glob"), options.get("recursive", True))
    current = _hashes(root, files)
    if "files" not in state:
        noun = "file" if len(current) == 1 else "files"
        return CheckResult(False, f"baseline, {len(current)} {noun}", {"files": current})

    previous = state.get("files") or {}
    if not isinstance(previous, dict):
        previous = {}
    added = sorted(set(current) - set(previous))
    removed = sorted(set(previous) - set(current))
    changed = sorted(
        name for name in current if name in previous and current[name] != previous[name]
    )
    new_state = {"files": current}
    if not added and not removed and not changed:
        return CheckResult(False, "unchanged", new_state)
    return CheckResult(True, _summary(changed, added, removed), new_state)


def _hashes(root: Path, files: list[Path]) -> dict:
    mapping = {}
    if root.is_file():
        mapping[root.name] = _sha256(root)
        return mapping
    for path in files:
        mapping[path.relative_to(root).as_posix()] = _sha256(path)
    return mapping


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _summary(changed: list[str], added: list[str], removed: list[str]) -> str:
    parts = []
    if changed:
        parts.append("changed: " + _join(changed))
    if added:
        parts.append("added: " + _join(added))
    if removed:
        parts.append("removed: " + _join(removed))
    return "; ".join(parts)


def _join(names: list[str]) -> str:
    shown = ", ".join(names[:_SHOW])
    extra = len(names) - _SHOW
    if extra > 0:
        return f"{shown} (+{extra} more)"
    return shown
