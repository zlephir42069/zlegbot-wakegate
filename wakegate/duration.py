"""Duration and timestamp parsing shared by config, schedule, and report."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_DURATION = re.compile(r"^(\d+)([smhdw])$")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(value: str) -> timedelta:
    """Parse `30s`, `15m`, `12h`, `7d`, or `1w` into a timedelta."""
    text = value.strip().lower()
    match = _DURATION.fullmatch(text)
    if not match:
        raise ValueError(
            f"invalid duration {value!r} (use a number plus s, m, h, d, or w, such as 30m)"
        )
    amount = int(match.group(1))
    return timedelta(seconds=amount * _UNITS[match.group(2)])


def parse_iso(value: str, *, naive_ok: bool = False) -> datetime:
    """Parse an ISO-8601 timestamp. A trailing Z is treated as UTC.

    Naive datetimes are rejected unless ``naive_ok`` is set. Callers that
    accept naive values interpret them in a configured timezone.
    """
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid timestamp {value!r}") from exc
    if parsed.tzinfo is None:
        if not naive_ok:
            raise ValueError(f"timestamp {value!r} needs a timezone offset")
        return parsed
    return parsed.astimezone(timezone.utc)


def parse_since(value: str, now: datetime) -> datetime:
    """Resolve a report window: a duration before ``now``, or an absolute time."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    text = value.strip()
    try:
        return now - parse_duration(text)
    except ValueError:
        pass
    try:
        return parse_iso(text, naive_ok=False)
    except ValueError as exc:
        raise ValueError(
            f"invalid --since {value!r} (use a duration like 7d or an ISO timestamp)"
        ) from exc


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
