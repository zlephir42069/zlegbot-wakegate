"""Wake targets. A failed wake raises WakeError and does not update state."""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime

from wakegate.errors import WakeError
from wakegate.http import post_json
from wakegate.redact import Redactor

DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 0.5
DEFAULT_TIMEOUT = 30.0
_DISCORD_LIMIT = 1900


def send_wake(
    spec,
    check: str,
    summary: str,
    now: datetime,
    redactor: Redactor,
    sleeper=time.sleep,
) -> None:
    """Call ``spec`` with retries. Sleep grows as ``backoff * 2**attempt``."""
    delay = spec.backoff
    last = "wake failed"
    attempts = spec.retries
    for attempt in range(attempts):
        try:
            _send_once(spec, check, summary, now)
            return
        except Exception as exc:
            last = redactor.text(str(exc))
            if attempt + 1 >= attempts:
                break
            sleeper(delay)
            delay *= 2
    raise WakeError(last)


def _send_once(spec, check: str, summary: str, now: datetime) -> None:
    stamp = now.isoformat()
    if spec.type == "webhook":
        post_json(
            spec.url,
            {"check": check, "summary": summary, "timestamp": stamp},
            spec.headers,
            spec.timeout,
        )
        return
    if spec.type == "discord":
        content = f"{check}: {summary}"
        if len(content) > _DISCORD_LIMIT:
            content = content[: _DISCORD_LIMIT - 3] + "..."
        # parse: [] disables @everyone, @here, and role pings.
        post_json(
            spec.url,
            {"content": content, "allowed_mentions": {"parse": []}},
            None,
            spec.timeout,
        )
        return
    if spec.type == "slack":
        post_json(spec.url, {"text": f"{check}: {summary}"}, None, spec.timeout)
        return
    if spec.type == "command":
        _run_command(spec, check, summary, stamp)
        return
    raise WakeError(f"unknown wake type {spec.type}")


def _run_command(spec, check: str, summary: str, stamp: str) -> None:
    env = os.environ.copy()
    env["WAKEGATE_CHECK"] = check
    env["WAKEGATE_SUMMARY"] = summary
    env["WAKEGATE_TIMESTAMP"] = stamp
    try:
        completed = subprocess.run(
            spec.run,
            shell=True,
            timeout=spec.timeout,
            env=env,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise WakeError(f"command timed out after {spec.timeout:g}s") from exc
    if completed.returncode != 0:
        raise WakeError(f"command exited {completed.returncode}")
