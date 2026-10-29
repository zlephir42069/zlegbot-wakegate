"""Load and validate wakegate YAML. Secrets stay in the environment."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from wakegate.duration import parse_duration, parse_iso
from wakegate.errors import ConfigError
from wakegate.redact import Redactor, scrub

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SENSITIVE_NAME = re.compile(
    r"(URL|URI|TOKEN|SECRET|PASSWORD|PASSWD|AUTH|WEBHOOK|HOOK)",
    re.IGNORECASE,
)
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_HHMM = re.compile(r"^(\d{2}):(\d{2})$")

_TOP = {"state", "log", "lock_timeout", "wakes", "checks"}
_WAKE_COMMON = {"type", "retries", "backoff", "timeout"}
_WAKE_FIELDS = {
    "webhook": _WAKE_COMMON | {"url", "headers"},
    "discord": _WAKE_COMMON | {"url"},
    "slack": _WAKE_COMMON | {"url"},
    "command": _WAKE_COMMON | {"run"},
}
_CHECK_COMMON = {"name", "type", "wake", "cooldown"}
_CHECK_FIELDS = {
    "file_changed": _CHECK_COMMON | {"path", "glob", "recursive"},
    "feed_new_item": _CHECK_COMMON | {"url", "timeout"},
    "url_changed": _CHECK_COMMON | {"url", "timeout", "extract"},
    "schedule_due": _CHECK_COMMON | {"timezone", "times", "at", "window"},
    "queue_nonempty": _CHECK_COMMON | {"path", "glob", "recursive", "threshold"},
    "command": _CHECK_COMMON | {"run", "fire_on", "timeout"},
}

_MAX_TIMEOUT = 86400.0


@dataclass
class WakeSpec:
    name: str
    type: str
    url: str = ""
    run: str = ""
    headers: dict = field(default_factory=dict)
    retries: int = 3
    backoff: float = 0.5
    timeout: float = 30.0


@dataclass
class CheckSpec:
    name: str
    type: str
    wake: str
    cooldown: timedelta
    options: dict


@dataclass
class AppConfig:
    path: Path
    state_path: Path
    log_path: Path
    lock_timeout: float
    wakes: dict
    checks: list
    redactor: Redactor


def load_config(path: Path) -> AppConfig:
    path = Path(path)
    data = _read_yaml(path)
    if not isinstance(data, dict):
        raise ConfigError("config must be a YAML mapping")

    errors: list[str] = []
    redactor = Redactor()
    base = path.parent.resolve()

    for key in sorted(set(data) - _TOP):
        errors.append(f"unknown setting {key}")

    state_path = _resolve(base, _optional_path(data, "state", ".wakegate/state.json", errors))
    log_path = _resolve(base, _optional_path(data, "log", ".wakegate/runs.jsonl", errors))
    lock_timeout = _seconds(
        data.get("lock_timeout", 60),
        "lock_timeout",
        errors,
        allow_zero=True,
    )
    wakes = _parse_wakes(data.get("wakes", None), errors, redactor)
    checks = _parse_checks(data.get("checks", None), errors, redactor, base, set(wakes))

    if errors:
        raise ConfigError(errors)
    if lock_timeout is None:
        raise ConfigError("lock_timeout is invalid")
    return AppConfig(
        path=path.resolve(),
        state_path=state_path,
        log_path=log_path,
        lock_timeout=lock_timeout,
        wakes=wakes,
        checks=checks,
        redactor=redactor,
    )


def configured_log_path(path: Path) -> Path:
    """Read only the log path, so `report` does not need wake credentials."""
    path = Path(path)
    data = _read_yaml(path)
    if not isinstance(data, dict):
        raise ConfigError("config must be a YAML mapping")
    raw = data.get("log", ".wakegate/runs.jsonl")
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError("log must be a path")
    errors: list[str] = []
    expanded = _expand(raw, sensitive=False, redactor=Redactor(), errors=errors, label="log")
    if errors:
        raise ConfigError(errors)
    return _resolve(path.parent.resolve(), expanded)


def _read_yaml(path: Path):
    if not path.is_file():
        raise ConfigError(f"config not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"could not read config: {exc.__class__.__name__}") from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(scrub(f"could not parse YAML: {exc}")) from exc


def _parse_wakes(raw, errors: list[str], redactor: Redactor) -> dict:
    if raw is None:
        errors.append("wakes is required")
        return {}
    if not isinstance(raw, dict) or isinstance(raw, list):
        errors.append("wakes must be a mapping of name to target")
        return {}
    if not raw:
        errors.append("wakes must contain at least one target")
        return {}
    parsed = {}
    for name, body in raw.items():
        label = f"wake {name}"
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            errors.append(f"{label}: name must match {_NAME.pattern}")
            continue
        if not isinstance(body, dict):
            errors.append(f"{label} must be a mapping")
            continue
        wake_type = body.get("type")
        if wake_type not in _WAKE_FIELDS:
            errors.append(f"{label}: type must be webhook, discord, slack, or command")
            continue
        for key in sorted(set(body) - _WAKE_FIELDS[wake_type]):
            errors.append(f"{label}: unknown setting {key}")
        retries = _int(body.get("retries", 3), f"{label}.retries", errors, minimum=1)
        backoff = _seconds(
            body.get("backoff", 0.5),
            f"{label}.backoff",
            errors,
            allow_zero=True,
        )
        timeout = _seconds(body.get("timeout", 30), f"{label}.timeout", errors, allow_zero=False)
        url = ""
        run = ""
        headers: dict[str, str] = {}
        if wake_type == "command":
            if "run" not in body:
                errors.append(f"{label}.run is required")
            else:
                run = _expand(
                    body.get("run"),
                    sensitive=True,
                    redactor=redactor,
                    errors=errors,
                    label=f"{label}.run",
                )
                if isinstance(body.get("run"), str) and not run.strip():
                    errors.append(f"{label}.run must not be empty")
        else:
            if "url" not in body:
                errors.append(f"{label}.url is required")
            else:
                url = _expand(
                    body.get("url"),
                    sensitive=True,
                    redactor=redactor,
                    errors=errors,
                    label=f"{label}.url",
                )
                if isinstance(body.get("url"), str):
                    _require_http(url, f"{label}.url", errors)
            if wake_type == "webhook" and "headers" in body:
                headers = _headers(body.get("headers"), f"{label}.headers", errors, redactor)
        parsed[name] = WakeSpec(
            name=name,
            type=wake_type,
            url=url,
            run=run,
            headers=headers,
            retries=3 if retries is None else retries,
            backoff=0.5 if backoff is None else backoff,
            timeout=30.0 if timeout is None else timeout,
        )
    return parsed


def _parse_checks(raw, errors, redactor, base: Path, wake_names: set[str]) -> list:
    if raw is None:
        errors.append("checks is required")
        return []
    if not isinstance(raw, list):
        errors.append("checks must be a list")
        return []
    if not raw:
        errors.append("checks must contain at least one check")
        return []
    parsed = []
    seen = set()
    for index, body in enumerate(raw):
        label = f"checks[{index}]"
        if not isinstance(body, dict):
            errors.append(f"{label} must be a mapping")
            continue
        name = body.get("name")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            errors.append(f"{label}.name must be a short letter-or-number identifier")
            label_name = label
        else:
            label_name = f"check {name}"
            if name in seen:
                errors.append(f"duplicate check name {name}")
            seen.add(name)
        check_type = body.get("type")
        if check_type not in _CHECK_FIELDS:
            errors.append(f"{label_name}: unknown type {check_type!r}")
            continue
        for key in sorted(set(body) - _CHECK_FIELDS[check_type]):
            errors.append(f"{label_name}: unknown setting {key}")
        wake = body.get("wake")
        if not isinstance(wake, str) or not wake:
            errors.append(f"{label_name}: wake is required")
            wake = ""
        elif wake not in wake_names:
            errors.append(f"{label_name}: unknown wake {wake}")
        cooldown = timedelta(0)
        if "cooldown" in body:
            parsed_cooldown = _duration(body.get("cooldown"), f"{label_name}.cooldown", errors)
            if parsed_cooldown is not None:
                cooldown = parsed_cooldown
        options = _check_options(check_type, body, label_name, errors, redactor, base)
        if not isinstance(name, str) or not _NAME.fullmatch(name) or options is None or not wake:
            continue
        parsed.append(
            CheckSpec(
                name=name,
                type=check_type,
                wake=wake,
                cooldown=cooldown,
                options=options,
            )
        )
    return parsed


def _check_options(check_type, body, label, errors, redactor, base: Path):
    if check_type == "file_changed":
        path = _required_path(body, "path", label, errors, redactor, base, sensitive=False)
        if path is None:
            return None
        return {
            "path": path,
            "glob": _optional_str(body, "glob", f"{label}.glob", errors),
            "recursive": _optional_bool(body, "recursive", True, f"{label}.recursive", errors),
        }
    if check_type == "feed_new_item":
        url = _required_url(body, label, errors, redactor)
        if url is None:
            return None
        return {"url": url, "timeout": _option_timeout(body, label, errors)}
    if check_type == "url_changed":
        url = _required_url(body, label, errors, redactor)
        if url is None:
            return None
        extract = None
        if "extract" in body:
            extract = _extract(body.get("extract"), f"{label}.extract", errors)
            if extract is None:
                return None
        return {"url": url, "timeout": _option_timeout(body, label, errors), "extract": extract}
    if check_type == "schedule_due":
        return _schedule_options(body, label, errors)
    if check_type == "queue_nonempty":
        path = _required_path(body, "path", label, errors, redactor, base, sensitive=False)
        if path is None:
            return None
        threshold = 0
        if "threshold" in body:
            parsed = _int(body.get("threshold"), f"{label}.threshold", errors, minimum=0)
            if parsed is None:
                return None
            threshold = parsed
        return {
            "path": path,
            "glob": _optional_str(body, "glob", f"{label}.glob", errors),
            "recursive": _optional_bool(body, "recursive", False, f"{label}.recursive", errors),
            "threshold": threshold,
        }
    if check_type == "command":
        run = body.get("run")
        expanded = _expand(run, sensitive=True, redactor=redactor, errors=errors, label=f"{label}.run")
        if not isinstance(run, str):
            return None
        if not expanded.strip():
            errors.append(f"{label}.run must not be empty")
            return None
        fire_on = 0
        if "fire_on" in body:
            parsed = _int(body.get("fire_on"), f"{label}.fire_on", errors, minimum=None)
            if parsed is None:
                return None
            fire_on = parsed
        return {"run": expanded, "fire_on": fire_on, "timeout": _option_timeout(body, label, errors)}
    return None


def _schedule_options(body, label, errors):
    timezone_name = body.get("timezone")
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        errors.append(f"{label}.timezone is required")
        return None
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        errors.append(f"{label}.timezone is not an IANA timezone: {timezone_name}")
        return None
    window = timedelta(minutes=10)
    if "window" in body:
        parsed = _duration(body.get("window"), f"{label}.window", errors, allow_zero=False)
        if parsed is None:
            return None
        window = parsed
    times = []
    if "times" in body:
        raw_times = body.get("times")
        if not isinstance(raw_times, list) or not raw_times:
            errors.append(f"{label}.times must be a non-empty list of HH:MM strings")
            return None
        for index, item in enumerate(raw_times):
            parsed = _hhmm(item, f"{label}.times[{index}]", errors)
            if parsed is None:
                return None
            if parsed not in times:
                times.append(parsed)
    ats = []
    if "at" in body:
        raw_at = body.get("at")
        if not isinstance(raw_at, list) or not raw_at:
            errors.append(f"{label}.at must be a non-empty list of ISO timestamps")
            return None
        for index, item in enumerate(raw_at):
            text = _timestamp_text(item, f"{label}.at[{index}]", errors)
            if text is None:
                return None
            try:
                parse_iso(text, naive_ok=True)
            except ValueError as exc:
                errors.append(f"{label}.at[{index}]: {exc}")
                return None
            if text not in ats:
                ats.append(text)
    if not times and not ats:
        errors.append(f"{label}: schedule_due needs times, at, or both")
        return None
    return {"timezone": timezone_name, "times": times, "at": ats, "window": window}


def _extract(raw, label, errors):
    if not isinstance(raw, dict):
        errors.append(f"{label} must be a mapping")
        return None
    keys = set(raw)
    if "regex" in raw:
        extra = keys - {"regex", "group"}
        if extra:
            errors.append(f"{label}: unknown setting {sorted(extra)[0]}")
            return None
        pattern = raw.get("regex")
        if not isinstance(pattern, str) or not pattern:
            errors.append(f"{label}.regex must be a string")
            return None
        try:
            re.compile(pattern)
        except re.error as exc:
            errors.append(f"{label}.regex is invalid: {exc}")
            return None
        result = {"regex": pattern}
        if "group" in raw:
            group = _int(raw.get("group"), f"{label}.group", errors, minimum=0)
            if group is None:
                return None
            result["group"] = group
        return result
    if keys != {"start", "end"}:
        errors.append(f"{label} needs regex, or both start and end")
        return None
    start = raw.get("start")
    end = raw.get("end")
    if not isinstance(start, str) or not start or not isinstance(end, str) or not end:
        errors.append(f"{label} start and end must be non-empty strings")
        return None
    return {"start": start, "end": end}


def _headers(raw, label, errors, redactor) -> dict:
    if not isinstance(raw, dict):
        errors.append(f"{label} must be a mapping")
        return {}
    parsed = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            errors.append(f"{label} keys must be strings")
            continue
        expanded = _expand(
            value,
            sensitive=True,
            redactor=redactor,
            errors=errors,
            label=f"{label}.{key}",
        )
        if isinstance(value, str):
            parsed[key] = expanded
    return parsed


def _required_url(body, label, errors, redactor):
    if "url" not in body:
        errors.append(f"{label}.url is required")
        return None
    raw = body.get("url")
    expanded = _expand(raw, sensitive=True, redactor=redactor, errors=errors, label=f"{label}.url")
    if not isinstance(raw, str):
        return None
    if "${" in expanded:
        return None
    if not expanded.strip():
        errors.append(f"{label}.url must not be empty")
        return None
    if not _require_http(expanded, f"{label}.url", errors):
        return None
    return expanded


def _required_path(body, key, label, errors, redactor, base, *, sensitive: bool):
    if key not in body:
        errors.append(f"{label}.{key} is required")
        return None
    raw = body.get(key)
    expanded = _expand(
        raw,
        sensitive=sensitive,
        redactor=redactor,
        errors=errors,
        label=f"{label}.{key}",
    )
    if not isinstance(raw, str):
        return None
    if "${" in expanded or not expanded.strip():
        if isinstance(raw, str) and not expanded.strip():
            errors.append(f"{label}.{key} must not be empty")
        return None
    return str(_resolve(base, expanded))


def _option_timeout(body, label, errors) -> float:
    if "timeout" not in body:
        return 30.0
    parsed = _seconds(body.get("timeout"), f"{label}.timeout", errors, allow_zero=False)
    if parsed is None:
        return 30.0
    return parsed


def _optional_str(body, key, label, errors):
    if key not in body:
        return None
    value = body.get(key)
    if not isinstance(value, str) or not value:
        errors.append(f"{label} must be a non-empty string")
        return None
    return value


def _optional_bool(body, key, default, label, errors) -> bool:
    if key not in body:
        return default
    value = body.get(key)
    if not isinstance(value, bool):
        errors.append(f"{label} must be true or false")
        return default
    return value


def _optional_path(data, key, default, errors) -> str:
    if key not in data:
        return default
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{key} must be a path")
        return default
    return value


def _expand(value, *, sensitive: bool, redactor: Redactor, errors: list[str], label: str) -> str:
    if not isinstance(value, str):
        errors.append(f"{label} must be a string")
        return ""
    missing = []
    # `$${` is an escaped `${` so a shell command can keep a literal variable.
    sentinel = "\x00WAKEGATE_DOLLAR\x00"
    protected = value.replace("$${", sentinel)

    def repl(match):
        name = match.group(1)
        if name not in os.environ:
            missing.append(name)
            return match.group(0)
        env_value = os.environ[name]
        if sensitive or _SENSITIVE_NAME.search(name):
            redactor.add(env_value)
        return env_value

    expanded = _ENV.sub(repl, protected).replace(sentinel, "${")
    for name in sorted(set(missing)):
        errors.append(f"{label} references unset environment variable {name}")
    if missing:
        return expanded
    if sensitive and expanded != value and "://" in expanded:
        redactor.add(expanded)
    return expanded


def _require_http(url: str, label: str, errors: list[str]) -> bool:
    if "${" in url:
        return False
    if not url or not url.strip():
        errors.append(f"{label} must not be empty")
        return False
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        errors.append(f"{label} must be an http(s) URL")
        return False
    return True


def _duration(value, label, errors, *, allow_zero=True):
    if not isinstance(value, str):
        errors.append(f"{label} must be a duration string like 30m")
        return None
    try:
        parsed = parse_duration(value)
    except ValueError as exc:
        errors.append(f"{label}: {exc}")
        return None
    if parsed < timedelta(0) or (parsed == timedelta(0) and not allow_zero):
        errors.append(f"{label} must be greater than zero")
        return None
    return parsed


def _seconds(value, label, errors, *, allow_zero: bool):
    if isinstance(value, bool):
        errors.append(f"{label} must be seconds or a duration")
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        try:
            seconds = parse_duration(value).total_seconds()
        except ValueError as exc:
            errors.append(f"{label}: {exc}")
            return None
    else:
        errors.append(f"{label} must be seconds or a duration")
        return None
    if seconds < 0 or seconds > _MAX_TIMEOUT or (not allow_zero and seconds <= 0):
        if allow_zero:
            errors.append(f"{label} must be between 0 and {_MAX_TIMEOUT:g} seconds")
        else:
            errors.append(f"{label} must be greater than 0 and at most {_MAX_TIMEOUT:g} seconds")
        return None
    return seconds


def _int(value, label, errors, *, minimum):
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{label} must be an integer")
        return None
    if minimum is not None and value < minimum:
        errors.append(f"{label} must be >= {minimum}")
        return None
    return value


def _hhmm(value, label, errors):
    if isinstance(value, int) and not isinstance(value, bool):
        errors.append(f'{label} must be a quoted "HH:MM" string')
        return None
    if not isinstance(value, str):
        errors.append(f'{label} must be a quoted "HH:MM" string')
        return None
    match = _HHMM.fullmatch(value.strip())
    if not match:
        errors.append(f'{label} must be a quoted "HH:MM" string')
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        errors.append(f"{label} is not a valid time of day")
        return None
    return hour, minute


def _timestamp_text(value, label, errors):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()
    errors.append(f"{label} must be a quoted ISO timestamp")
    return None


def _resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path
