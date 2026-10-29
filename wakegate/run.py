"""Evaluate every check once, wake on a real signal, and append the run log."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from wakegate.checks import evaluate
from wakegate.duration import as_utc, parse_iso
from wakegate.errors import CheckError, StateError, WakeError
from wakegate.state import StateStore, append_jsonl
from wakegate.wake import send_wake


def execute(config, *, dry_run: bool = False, now: datetime | None = None) -> int:
    """Run every check. Returns 0, or 1 if a wake or check failed.

    ``dry_run`` prints each result and does not wake, write state, append the
    run log, or create the state directory. A failed wake leaves that check's
    state unchanged so the same signal is still pending on the next run.
    """
    moment = as_utc(now or datetime.now(timezone.utc))
    if dry_run:
        try:
            state = _load_if_present(config.state_path)
        except StateError as exc:
            _error(config, f"state error: {exc}")
            return 1
        exit_code, _dirty, _rows = _evaluate(config, state, moment, dry_run=True)
        return exit_code

    store = StateStore(config.state_path)
    try:
        store.acquire(config.lock_timeout)
    except TimeoutError as exc:
        _error(config, f"state error: {exc}")
        return 1

    exit_code = 0
    try:
        try:
            state = store.load()
        except StateError as exc:
            _error(config, f"state error: {exc}")
            return 1
        exit_code, dirty, rows = _evaluate(config, state, moment, dry_run=False)
        try:
            # State is saved before the log so a crash cannot record a wake
            # that was not actually committed. The reverse crash (state saved,
            # log missing) will not wake the same signal twice.
            if dirty:
                store.save(state)
            append_jsonl(config.log_path, rows)
        except StateError as exc:
            _error(config, f"state error: {exc}")
            return 1
        return exit_code
    finally:
        store.release()


def _load_if_present(path) -> dict:
    if not path.exists():
        return {"checks": {}}
    return StateStore(path).load()


def _evaluate(config, state, moment, dry_run: bool):
    checks_state = state["checks"]
    rows: list[dict] = []
    dirty = False
    exit_code = 0
    for spec in config.checks:
        failed, changed = _run_check(config, spec, checks_state, rows, moment, dry_run)
        dirty = dirty or changed
        if failed:
            exit_code = 1
    return exit_code, dirty, rows


def _run_check(config, spec, checks_state, rows, moment, dry_run: bool):
    entry = checks_state.get(spec.name)
    if not isinstance(entry, dict):
        entry = {}
    data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
    last_wake = entry.get("last_wake") if isinstance(entry.get("last_wake"), str) else None

    try:
        result = evaluate(spec.type, spec.options, data, moment)
    except CheckError as exc:
        summary = config.redactor.text(str(exc))
        _record(rows, dry_run, moment, spec, False, "error", "check_error", summary)
        _error(config, f"error {spec.name}: {summary}")
        return True, False
    except Exception as exc:
        summary = config.redactor.text(f"{exc.__class__.__name__}: {exc}")
        _record(rows, dry_run, moment, spec, False, "error", "check_error", summary)
        _error(config, f"error {spec.name}: {summary}")
        return True, False

    summary = config.redactor.text(result.summary)
    if not result.fired:
        changed = _commit(checks_state, spec.name, result.state, last_wake, dry_run)
        _record(rows, dry_run, moment, spec, False, "avoided", "not_fired", summary)
        if dry_run:
            print(f"{spec.name} ok: {summary}", flush=True)
        return False, changed

    if _in_cooldown(last_wake, spec.cooldown, moment):
        _record(rows, dry_run, moment, spec, True, "avoided", "cooldown", summary)
        if dry_run:
            print(f"{spec.name} cooldown: {summary}", flush=True)
        return False, False

    if dry_run:
        print(f"{spec.name} fired: {summary}", flush=True)
        return False, False

    try:
        send_wake(config.wakes[spec.wake], spec.name, summary, moment, config.redactor)
    except WakeError as exc:
        message = config.redactor.text(str(exc))
        _record(rows, dry_run, moment, spec, True, "failed", "wake_error", summary)
        _error(config, f"wake failed {spec.name}: {message}")
        return True, False

    changed = _commit(checks_state, spec.name, result.state, moment.isoformat(), dry_run=False)
    _record(rows, dry_run, moment, spec, True, "sent", "sent", summary)
    print(f"woke {spec.name}: {summary}", flush=True)
    return False, changed


def _commit(checks_state, name, data, last_wake, dry_run: bool) -> bool:
    if dry_run:
        return False
    new_entry = {"data": data, "last_wake": last_wake}
    if checks_state.get(name) != new_entry:
        checks_state[name] = new_entry
        return True
    return False


def _in_cooldown(last_wake, cooldown: timedelta, now: datetime) -> bool:
    if cooldown <= timedelta(0) or not last_wake:
        return False
    try:
        then = parse_iso(last_wake)
    except ValueError:
        return False
    return as_utc(now) < then + cooldown


def _record(rows, dry_run, moment, spec, fired, outcome, reason, summary) -> None:
    if dry_run:
        return
    rows.append(
        {
            "check": spec.name,
            "fired": bool(fired),
            "outcome": outcome,
            "reason": reason,
            "summary": summary,
            "ts": moment.isoformat(),
            "type": spec.type,
        }
    )


def _error(config, message: str) -> None:
    print(config.redactor.text(message), file=sys.stderr, flush=True)
