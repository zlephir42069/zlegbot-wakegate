"""Check registry. Each evaluator returns fired, a short summary, and new state."""

from __future__ import annotations

from datetime import datetime

from wakegate.checks.base import CheckResult
from wakegate.checks import command, feed_new_item, file_changed, queue_nonempty, schedule_due, url_changed

EVALUATORS = {
    "file_changed": file_changed.evaluate,
    "feed_new_item": feed_new_item.evaluate,
    "url_changed": url_changed.evaluate,
    "schedule_due": schedule_due.evaluate,
    "queue_nonempty": queue_nonempty.evaluate,
    "command": command.evaluate,
}


def evaluate(check_type: str, options: dict, state: dict, now: datetime) -> CheckResult:
    try:
        evaluator = EVALUATORS[check_type]
    except KeyError as exc:
        raise KeyError(f"unknown check type {check_type}") from exc
    return evaluator(options, state or {}, now)
