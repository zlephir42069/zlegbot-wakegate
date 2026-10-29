"""Fire when a queue holds more than ``threshold`` items and those items changed.

A directory queue counts matching files. A ``.json`` file must contain an
array. A ``.jsonl`` file counts non-empty lines. The same fingerprint does
not fire twice; draining the queue clears it so a later refill can fire.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from wakegate.checks.base import CheckResult
from wakegate.errors import CheckError
from wakegate.fileset import list_files


def evaluate(options: dict, state: dict, now: datetime) -> CheckResult:
    del now
    path = Path(options["path"])
    if not path.exists():
        raise CheckError(f"path does not exist: {path}")
    count, digest = _snapshot(path, options)
    threshold = options.get("threshold", 0)
    new_state = {"count": count, "fingerprint": digest}
    label = _items(count)
    if count <= threshold:
        return CheckResult(False, label, new_state)
    if state.get("fingerprint") == digest:
        return CheckResult(False, f"unchanged, {label}", new_state)
    return CheckResult(True, f"{label} waiting", new_state)


def _items(count: int) -> str:
    return "1 item" if count == 1 else f"{count} items"


def _snapshot(path: Path, options: dict) -> tuple[int, str]:
    if path.is_dir():
        files = list_files(path, options.get("glob"), options.get("recursive", False))
        chunks = []
        for item in sorted(files, key=lambda entry: entry.relative_to(path).as_posix()):
            rel = item.relative_to(path).as_posix()
            chunks.append(rel.encode("utf-8") + b"\0" + _sha256(item).encode("ascii"))
        digest = hashlib.sha256(b"\n".join(chunks)).hexdigest()
        return len(files), digest
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return _jsonl(path)
    if suffix == ".json":
        return _json_array(path)
    raise CheckError("queue path must be a directory, a JSON array, or a JSONL file")


def _json_array(path: Path) -> tuple[int, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckError(f"could not read JSON queue: {exc.__class__.__name__}") from exc
    if not isinstance(data, list):
        raise CheckError("JSON queue file must contain an array")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return len(data), hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _jsonl(path: Path) -> tuple[int, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CheckError(f"could not read JSONL queue: {exc.__class__.__name__}") from exc
    values = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            values.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise CheckError(f"invalid JSONL at line {number}") from exc
    payload = json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return len(values), hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
