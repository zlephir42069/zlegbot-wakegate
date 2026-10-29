"""Fire when a configured local time slot is inside its window and not yet fired.

``times`` are daily ``HH:MM`` values. ``at`` entries are one-shot ISO datetimes.
Naive ``at`` values use the check's timezone. Each slot fires at most once;
a successful wake is what records the slot.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from wakegate.checks.base import CheckResult
from wakegate.duration import as_utc, parse_iso


def evaluate(options: dict, state: dict, now: datetime) -> CheckResult:
    tz = ZoneInfo(options["timezone"])
    now_utc = as_utc(now)
    window: timedelta = options["window"]
    fired = [item for item in (state.get("fired") or []) if isinstance(item, str)]
    fired_set = set(fired)

    due: list[tuple[str, str]] = []
    seen: set[str] = set()
    for hour, minute in options.get("times") or []:
        _collect_daily(due, seen, now_utc, tz, hour, minute, window, fired_set)
    for raw in options.get("at") or []:
        _consider(due, seen, _parse_at(raw, tz), tz, now_utc, window, fired_set)

    kept = _prune(fired, now_utc, window)
    if not due:
        return CheckResult(False, "nothing due", {"fired": kept})
    keys = [key for key, _label in due]
    labels = ", ".join(label for _key, label in due)
    return CheckResult(True, f"due: {labels}", {"fired": kept + keys})


def _collect_daily(due, seen, now_utc, tz, hour, minute, window, fired_set) -> None:
    local_now = now_utc.astimezone(tz)
    for offset in (0, -1):
        day = local_now.date() + timedelta(days=offset)
        slot = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
        _consider(due, seen, slot, tz, now_utc, window, fired_set)


def _consider(due, seen, slot: datetime, tz, now_utc, window, fired_set) -> None:
    slot_utc = as_utc(slot)
    if not (slot_utc <= now_utc < slot_utc + window):
        return
    key = slot_utc.isoformat()
    if key in fired_set or key in seen:
        return
    seen.add(key)
    local = slot_utc.astimezone(tz)
    due.append((key, local.strftime("%Y-%m-%d %H:%M ") + str(tz)))


def _parse_at(raw: str, tz) -> datetime:
    parsed = parse_iso(raw, naive_ok=True)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _prune(fired: list[str], now_utc: datetime, window: timedelta) -> list[str]:
    # A slot stops being due once `now >= slot + window`.
    cutoff = now_utc - window
    kept = []
    for key in fired:
        try:
            when = parse_iso(key)
        except ValueError:
            continue
        if when > cutoff:
            kept.append(key)
    return kept
