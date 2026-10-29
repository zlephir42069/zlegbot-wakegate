"""Result returned by every check."""

from __future__ import annotations


class CheckResult:
    def __init__(self, fired: bool, summary: str, state: dict) -> None:
        self.fired = fired
        self.summary = summary
        self.state = state

    def __repr__(self) -> str:
        return f"CheckResult(fired={self.fired!r}, summary={self.summary!r}, state={self.state!r})"
