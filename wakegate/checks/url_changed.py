"""Fire when a URL body hash changes.

An optional extract keeps the hash on a substring (start/end markers) or a
regex group so timestamps and other noise outside that region do not wake.
The first successful read is a baseline and does not fire.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime

from wakegate.checks.base import CheckResult
from wakegate.errors import CheckError
from wakegate.http import fetch


def evaluate(options: dict, state: dict, now: datetime) -> CheckResult:
    del now
    body = fetch(options["url"], options.get("timeout", 30))
    extract = options.get("extract")
    if extract:
        text = _extract(body.decode("utf-8", errors="replace"), extract)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    else:
        digest = hashlib.sha256(body).hexdigest()

    if "hash" not in state:
        return CheckResult(False, f"baseline {digest[:12]}", {"hash": digest})
    previous = state.get("hash")
    if previous == digest:
        return CheckResult(False, "unchanged", {"hash": digest})
    prev_text = previous[:12] if isinstance(previous, str) else "?"
    return CheckResult(True, f"changed {prev_text} -> {digest[:12]}", {"hash": digest})


def _extract(text: str, extract: dict) -> str:
    if "regex" in extract:
        try:
            match = re.search(extract["regex"], text)
        except re.error as exc:
            raise CheckError(f"extract regex is invalid: {exc}") from exc
        if not match:
            raise CheckError("extract regex did not match")
        group = extract.get("group", 1 if match.groups() else 0)
        try:
            value = match.group(group)
        except IndexError as exc:
            raise CheckError(f"extract regex has no group {group}") from exc
        if value is None:
            raise CheckError(f"extract regex group {group} did not participate")
        return value

    start = extract["start"]
    end = extract["end"]
    begin = text.find(start)
    if begin < 0:
        raise CheckError("extract start marker was not found")
    begin += len(start)
    finish = text.find(end, begin)
    if finish < 0:
        raise CheckError("extract end marker was not found")
    return text[begin:finish]
