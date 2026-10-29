"""List files for directory checks and match a small glob dialect.

Patterns:
- ``*.md`` matches the file name only.
- ``sub/*.md`` matches that relative path; ``*`` does not cross ``/``.
- ``**`` matches across directories, so ``**/*.md`` matches at any depth.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from wakegate.errors import CheckError


def list_files(root: Path, pattern: str | None, recursive: bool) -> list[Path]:
    if root.is_symlink() and root.is_dir():
        raise CheckError(f"refusing to walk symlink directory {root}")
    if root.is_file():
        return [root]
    if not root.is_dir():
        raise CheckError(f"path is not a file or directory: {root}")

    found: list[Path] = []
    if recursive:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            current = Path(dirpath)
            dirnames[:] = sorted(
                name for name in dirnames if not (current / name).is_symlink()
            )
            for name in sorted(filenames):
                path = current / name
                if path.is_symlink() or not path.is_file():
                    continue
                found.append(path)
    else:
        for path in sorted(root.iterdir(), key=lambda item: item.name):
            if path.is_symlink() or not path.is_file():
                continue
            found.append(path)

    if pattern:
        found = [
            path
            for path in found
            if glob_match(path.relative_to(root).as_posix(), pattern)
        ]
    return found


def glob_match(relative: str, pattern: str) -> bool:
    target = relative
    if "/" not in pattern and "**" not in pattern:
        target = relative.rsplit("/", 1)[-1]
    return _compile(pattern).fullmatch(target) is not None


def _compile(pattern: str) -> re.Pattern[str]:
    parts: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            parts.append("(?:.*/)?")
            index += 3
            continue
        if pattern.startswith("**", index):
            parts.append(".*")
            index += 2
            continue
        char = pattern[index]
        if char == "*":
            parts.append("[^/]*")
        elif char == "?":
            parts.append("[^/]")
        else:
            parts.append(re.escape(char))
        index += 1
    return re.compile("^" + "".join(parts) + "$")
