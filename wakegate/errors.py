"""Error types that map onto the CLI exit codes."""


class ConfigError(Exception):
    """Invalid config or CLI arguments. Exit code 2."""

    def __init__(self, messages):
        if isinstance(messages, str):
            messages = [messages]
        self.messages = [str(m) for m in messages if str(m)]
        super().__init__("\n".join(self.messages))


class CheckError(Exception):
    """A single check could not be evaluated. Other checks still run."""


class WakeError(Exception):
    """The wake target failed after retries."""


class StateError(Exception):
    """State or the run log could not be read or written."""
