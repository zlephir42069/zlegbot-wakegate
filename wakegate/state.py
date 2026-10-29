"""Atomic JSON state and an exclusive file lock for overlapping cron runs."""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path

from wakegate.errors import StateError


class StateStore:
    """JSON state protected by ``fcntl.flock``.

    The lock is on a sibling ``*.lock`` file so a reader never sees a
    half-written document. Writes go to a temp file in the same directory
    and replace the destination with ``os.replace``.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_name(path.name + ".lock")
        self._fh = None

    def acquire(self, timeout: float) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.lock_path, "a+", encoding="utf-8")
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise TimeoutError(
                        f"could not lock {self.lock_path} within {timeout:g}s"
                    )
                time.sleep(0.05)
                continue
            self._fh = handle
            return

    def release(self) -> None:
        handle = self._fh
        self._fh = None
        if handle is None:
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def load(self) -> dict:
        if not self.path.exists():
            return {"checks": {}}
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"could not read state {self.path}: {exc}") from exc
        if not isinstance(data, dict):
            raise StateError(f"state file {self.path} must contain a JSON object")
        checks = data.get("checks", {})
        if not isinstance(checks, dict):
            raise StateError(f"state file {self.path} field 'checks' must be an object")
        data["checks"] = checks
        return data

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(self.path.name + ".tmp")
        payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.path)
        except OSError as exc:
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise StateError(f"could not write state {self.path}: {exc}") from exc


def append_jsonl(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise StateError(f"could not append run log {path}: {exc}") from exc
