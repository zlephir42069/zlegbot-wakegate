# Contributing

Thanks for looking at wakegate. Keep changes small, and cover them with tests.

## Setup

Python 3.10 or newer. The runtime dependencies are the standard library and PyYAML.

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

Tests are offline. HTTP is mocked with a local `http.server` or a monkeypatch, and they do not call the network.

## Guidelines

- Do not add secrets, tokens, or personal data to the repo, tests, fixtures, or logs.
- Wake URLs belong in environment variables referenced as `${NAME}`. Anything new that prints or writes a log line has to pass through the redactor.
- A failed wake must not mark a check as handled. The same signal has to still be pending on the next run.
- Prefer a clear config key over a new feature. `wakegate report` may only print counts that are actually in the run log.
