"""Summarize the run log. Every number is a count of log lines."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from wakegate.duration import as_utc, parse_iso
from wakegate.errors import StateError

# Log outcome -> report bucket. evaluated is the sum of these four buckets.
_BUCKETS = {
    "sent": "sent",
    "avoided": "avoided",
    "failed": "failed",
    "error": "errors",
}


def render_report(log_path: Path, since: datetime) -> str:
    since = as_utc(since)
    totals = Counter()
    per: dict[str, Counter] = {}
    skipped = 0
    if log_path.exists():
        try:
            text = log_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StateError(f"could not read run log: {exc.__class__.__name__}") from exc
        for line in text.splitlines():
            if not line.strip():
                continue
            parsed = _classify(line, since)
            if parsed == "old":
                continue
            if parsed == "bad":
                skipped += 1
                continue
            name, bucket = parsed
            totals["evaluated"] += 1
            totals[bucket] += 1
            row = per.setdefault(name, Counter())
            row["evaluated"] += 1
            row[bucket] += 1
    return _format(totals, per, skipped, since)


def _classify(line: str, since: datetime):
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return "bad"
    if not isinstance(obj, dict):
        return "bad"
    outcome = obj.get("outcome")
    name = obj.get("check")
    stamp = obj.get("ts")
    if outcome not in _BUCKETS or not isinstance(name, str) or not name:
        return "bad"
    if not isinstance(stamp, str):
        return "bad"
    try:
        when = parse_iso(stamp)
    except ValueError:
        return "bad"
    if when < since:
        return "old"
    return name, _BUCKETS[outcome]


def _format(totals: Counter, per: dict[str, Counter], skipped: int, since: datetime) -> str:
    lines = [
        f"since: {since.isoformat()}",
        f"checks evaluated: {totals['evaluated']}",
        f"wakes sent: {totals['sent']}",
        f"wakes avoided: {totals['avoided']}",
        f"wake failures: {totals['failed']}",
        f"check errors: {totals['errors']}",
        f"malformed log lines: {skipped}",
        "",
        "by check:",
    ]
    if not per:
        lines.append("  (none)")
    for name in sorted(per):
        row = per[name]
        lines.append(
            f"  {name}: evaluated {row['evaluated']}, "
            f"wakes sent {row['sent']}, "
            f"wakes avoided {row['avoided']}, "
            f"wake failures {row['failed']}, "
            f"check errors {row['errors']}"
        )
    lines.append("")
    return "\n".join(lines)
