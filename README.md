# wakegate

Scheduled AI agents often wake every few minutes and find nothing to do, but each wake still spends model credits. wakegate is a cron-first gate: it evaluates cheap signals on a schedule and calls your agent only when one of them fires.

The scheduled process is this script. It does not call a model. A webhook or a local command is the only path that starts the agent, and that path runs only after a check fires and the check is outside its cooldown.

## 60-second quickstart

Requires Python 3.9+ and a POSIX system (the state lock uses `fcntl`).

```bash
python -m pip install -e .
cp wakegate.example.yaml wakegate.yaml
# Edit paths, the feed URL, and the agent command in wakegate.yaml.
export WAKEGATE_WEBHOOK_URL="https://example.com/hooks/wakegate"
export WAKEGATE_WEBHOOK_TOKEN="replace-me"
export WAKEGATE_DISCORD_URL="https://discord.com/api/webhooks/WEBHOOK_ID/WEBHOOK_TOKEN"
export WAKEGATE_SLACK_URL="https://hooks.slack.com/services/T000/B000/XXXXXXXX"
wakegate validate
wakegate run --dry-run
wakegate run
wakegate report --since 7d
```

`python -m wakegate` is the same entry point. `--dry-run` prints each check and does not wake, write state, or append the run log.

Put the real `run` on a timer. See `examples/crontab`, `examples/wakegate.service` with `examples/wakegate.timer`, and `examples/github-actions.yml`.

## Config reference

`wakegate.yaml` is the default path. Relative paths, including `state` and `log`, are resolved from the config file's directory.

| Setting | Default | Meaning |
| --- | --- | --- |
| `state` | `.wakegate/state.json` | Check state. Hashes, feed ids, queue fingerprints, fired slots, exit codes, and last wake time. |
| `log` | `.wakegate/runs.jsonl` | One JSON object per check per real run. |
| `lock_timeout` | `60` | Seconds to wait for the state lock. A duration such as `60s` is also accepted. |
| `wakes` | required | Named wake targets. |
| `checks` | required | List of checks. Each `wake` value is a name from `wakes`. |

Every check accepts `name`, `type`, `wake`, and optional `cooldown` (`30s`, `15m`, `12h`, `7d`, `1w`). Cooldown starts when a wake succeeds. A check that fires during cooldown is not woken and its new state is not saved, so the signal is still there when the cooldown ends.

`${NAME}` anywhere in a string is replaced from the environment at load time. A referenced variable that is unset is a config error. `$${NAME}` becomes a literal `${NAME}` so a shell command can keep its own variable. Values substituted into wake URLs, headers, and commands are redacted from stdout, stderr, and the run log. Incoming-webhook URLs are redacted even when they were pasted as literals.

Quote globs (`"*.md"`), clock times (`"09:00"`), and timestamps. Unquoted `*` is a YAML alias, and unquoted timestamps may become datetime objects.

`wakegate validate` checks the file and exits non-zero with one message per problem. It does not contact the network. Wake URLs must already be in the environment.

Exit codes for every command: `0` ok, `1` a wake failed, a check failed, the lock timed out, or state could not be written, `2` config or usage error. If one wake fails and another succeeds, the exit code is `1` and the success is still recorded.

## Checks

Each check lives in its own module under `wakegate/checks/`. It returns whether it fired, a short summary, and the state to store if this result is committed. The same signal does not wake twice.

### `file_changed`

SHA-256 of a file, or of every file under a directory. `glob` is optional. `recursive` defaults to true. A pattern with no `/` matches the file name (`*.md`). `*` does not cross `/`. `**` does, so `**/*.md` matches at any depth. Symlinked directories are not walked. The first successful read is a baseline and does not wake. Later runs wake when a file is added, removed, or its bytes change. The summary names up to five paths.

### `feed_new_item`

Fetches an RSS or Atom document and parses it with `xml.etree` (no feed library and no network beyond that GET). An item's id is `<guid>` or `<link>` for RSS, and `<id>` or the alternate link `href` for Atom. The first successful fetch records the current ids and does not wake. Later runs wake when an id has not been seen. State keeps the newest 500 ids; a feed that rotates through more than that can eventually treat a very old id as new. Responses larger than 2 MB are rejected.

### `url_changed`

SHA-256 of a URL body. The first successful read is a baseline. Optional `extract` ignores the rest of the page:

```yaml
extract:
  regex: '(?s)<main[^>]*>(.*?)</main>'
  # group: 1
```

```yaml
extract:
  start: "<main>"
  end: "</main>"
```

The regex is a Python regular expression. `(?s)` makes `.` match newlines. If the pattern has groups, group 1 is hashed unless `group` says otherwise. A start/end extract hashes the text between the first start marker and the following end marker. If the marker or regex does not match, the check errors and does not update state.

### `schedule_due`

Fires when a slot is due and that occurrence has not yet been marked fired. `times` are daily `HH:MM` values. `at` values are one-shot ISO-8601 timestamps; a trailing `Z` is UTC, and a value with no offset uses `timezone` (an IANA name, via `zoneinfo`). `window` defaults to `10m`. A slot is due when `slot <= now < slot + window`, which is also how a daily slot just before midnight is still due after midnight. One run wakes once if several slots are due, and a successful wake marks all of them. Make the window longer than the gap between cron runs. Leave `cooldown` unset on this check unless you want a slot dropped when the window closes first. Daylight-saving gaps are best-effort.

### `queue_nonempty`

Fires when a queue has more than `threshold` items (default `0`, so one item is enough) and its fingerprint changed.

- A directory counts files. `glob` and `recursive` work as in `file_changed`, except `recursive` defaults to false. The fingerprint is the relative path plus the SHA-256 of each file.
- A `.json` file must contain an array. Object key order and insignificant whitespace do not change the fingerprint. Array order does.
- A `.jsonl` file counts non-empty lines, which must be JSON.

An empty queue updates the fingerprint, so draining it and adding the same bytes again can wake. The same fingerprint does not wake twice. A failed wake does not store the new fingerprint.

### `command`

Runs a shell command with `timeout` seconds (default 30). The config file is trusted. The check fires when the exit code equals `fire_on` (default `0`) and the previous handled exit code was different. The same exit code does not fire again until the command returns something else and later matches again. `cooldown` also applies to that new transition. Stdout and stderr are not stored or logged. A timeout is a check error.

## Wake targets

`wakegate/wake.py` sends the check name, summary, and timestamp. Retries default to 3 with a backoff of 0.5s, doubled after each failure. The timeout default is 30 seconds. A failed wake, including a non-zero command, does not mark the check handled.

| Type | What it sends |
| --- | --- |
| `webhook` | `POST` JSON `{"check", "summary", "timestamp"}`. Optional `headers`. |
| `discord` | Incoming webhook `content`, plus `allowed_mentions: {"parse": []}` so the message cannot mass-ping. This is always set. |
| `slack` | Incoming webhook `{"text": "<check>: <summary>"}`. |
| `command` | The shell command, with `WAKEGATE_CHECK`, `WAKEGATE_SUMMARY`, and `WAKEGATE_TIMESTAMP` in the environment. |

Wake URLs and tokens are read from the environment. They are not written to state. Do not commit them.

## State and overlapping runs

State is written by writing a temp file in the same directory, `fsync`, and `os.replace`. A `fcntl` lock on `state.json.lock` is held for the whole non-dry run, so two cron runs do not interleave. If the lock is not acquired within `lock_timeout`, the run exits `1` and evaluates nothing. Deleting the state file re-baselines change checks and re-arms schedule slots.

The run log is append-only JSONL. Each line has `ts`, `check`, `type`, `fired`, `outcome`, `reason`, and `summary`. `outcome` is `sent`, `avoided`, `failed`, or `error`.

## Scheduling

| File | Use |
| --- | --- |
| `examples/crontab` | One cron line, every 10 minutes. |
| `examples/wakegate.service` and `examples/wakegate.timer` | systemd oneshot plus a 10-minute timer. |
| `examples/github-actions.yml` | Scheduled workflow. Copy it into `.github/workflows/`. |

On GitHub Actions, caching `.wakegate/` avoids a commit on every run. Caches can be evicted. After a miss, file, URL, and feed checks take a new baseline and do not wake; a schedule slot fires again only if it is still inside its window. The example cache key includes `wakegate.yaml`, so an edit starts a fresh baseline too.

Committing `.wakegate/state.json` survives eviction and can be reviewed. The workflow then needs permission to push, and every state change is a commit. State does not contain wake URLs. `.gitignore` ignores `.wakegate/`, so that workflow has to force-add the state file. Leaving `runs.jsonl` uncommitted keeps history smaller; the report then only sees the log that is still on the runner.

## `wakegate report`

```bash
wakegate report --since 7d
wakegate report --config wakegate.yaml --since 2026-10-01T00:00:00Z
```

`--since` is a duration before now, or an absolute timestamp. The default is `7d`. If `wakegate.yaml` exists, the log path comes from it (wake credentials are not required). Otherwise the default `.wakegate/runs.jsonl` in the current directory is used.

The output is only counts of log lines in the window. Lines older than `--since` are ignored. A line that is not valid JSON, or that lacks `ts`, `check`, and a known `outcome`, is counted as malformed and is not part of the other totals.

```
checks evaluated = wakes sent + wakes avoided + wake failures + check errors
```

- **checks evaluated**: one log line per check per real run inside the window.
- **wakes sent**: the wake call succeeded (`outcome` `sent`).
- **wakes avoided**: the check did not fire, or it fired and cooldown suppressed the wake (`outcome` `avoided`).
- **wake failures**: a wake was attempted and failed (`outcome` `failed`).
- **check errors**: the check could not be evaluated (`outcome` `error`).

The same totals are printed per check. Dry-runs are not in the log, so they do not change the report. Nothing in the report is estimated, sampled, or converted into money.

## How it saves credits

A cron entry that launches an agent pays for a model call even when the inbox is empty. Point the cron entry at `wakegate run` instead. The process hashes files, reads a feed, or checks the clock, then exits. The agent command or webhook runs only for a check that fired and was not in cooldown. `wakegate report` shows how many evaluations did not send a wake. That is the number of logged checks that did not start the agent. What those avoided wakes would have cost depends on the agent you attached; wakegate does not estimate it.

## Development

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

See `CONTRIBUTING.md`. The license is MIT, copyright zlephir42069.
