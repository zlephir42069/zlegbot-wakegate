"""Fire when a shell command's exit code transitions to ``fire_on``.

The same exit code does not fire again. After the command returns something
else, a later run that returns ``fire_on`` can fire. ``cooldown`` on the
check still applies to that new wake. The config file is trusted: the
command runs through the shell.
"""

from __future__ import annotations

import subprocess
from datetime import datetime

from wakegate.checks.base import CheckResult
from wakegate.errors import CheckError


def evaluate(options: dict, state: dict, now: datetime) -> CheckResult:
    del now
    timeout = options.get("timeout", 30)
    try:
        completed = subprocess.run(
            options["run"],
            shell=True,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise CheckError(f"command timed out after {timeout:g}s") from exc
    code = completed.returncode
    fired = code == options.get("fire_on", 0) and state.get("exit") != code
    return CheckResult(fired, f"exit {code}", {"exit": code})
