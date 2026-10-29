"""Config validation, env substitution, and the example file."""

import os
from pathlib import Path

from wakegate.config import configured_log_path, load_config
from wakegate.errors import ConfigError

from support import REPO, TempCase


class ConfigTest(TempCase):
    def _write(self, text):
        path = self.root / "wakegate.yaml"
        path.write_text(text)
        return path

    def test_example_covers_every_type(self):
        os.environ["WAKEGATE_WEBHOOK_URL"] = "https://example.com/hooks/wakegate"
        os.environ["WAKEGATE_WEBHOOK_TOKEN"] = "example-token"
        os.environ["WAKEGATE_DISCORD_URL"] = "https://example.com/discord"
        os.environ["WAKEGATE_SLACK_URL"] = "https://example.com/slack"
        config = load_config(REPO / "wakegate.example.yaml")
        self.assertEqual(
            {check.type for check in config.checks},
            {
                "file_changed",
                "feed_new_item",
                "url_changed",
                "schedule_due",
                "queue_nonempty",
                "command",
            },
        )
        self.assertEqual(
            {wake.type for wake in config.wakes.values()},
            {"webhook", "discord", "slack", "command"},
        )
        discord = config.wakes["discord"]
        self.assertEqual(discord.url, "https://example.com/discord")
        self.assertEqual(config.redactor.text("bearer example-token"), "bearer ***")

    def test_relative_paths_and_dollar_escape(self):
        os.environ["HOME"] = "/home/tester"
        os.environ["WAKEGATE_WEBHOOK_TOKEN"] = "SUPERSECRETTOKEN"
        path = self._write(
            """
wakes:
  hook:
    type: webhook
    url: https://example.com/hook/${WAKEGATE_WEBHOOK_TOKEN}
  agent:
    type: command
    run: echo $${HOME}
checks:
  - name: notes
    type: file_changed
    path: ./notes
    glob: "*.md"
    wake: hook
  - name: flag
    type: command
    run: echo $${HOME}
    wake: agent
"""
        )
        config = load_config(path)
        self.assertEqual(Path(config.checks[0].options["path"]).resolve(), (self.root / "notes").resolve())
        self.assertEqual(config.checks[0].options["glob"], "*.md")
        self.assertIn("SUPERSECRETTOKEN", config.wakes["hook"].url)
        self.assertEqual(config.wakes["agent"].run, "echo ${HOME}")
        self.assertEqual(config.checks[1].options["run"], "echo ${HOME}")
        self.assertNotIn("SUPERSECRETTOKEN", config.redactor.text(config.wakes["hook"].url))

    def test_yaml_timestamp_and_quoted_clock(self):
        os.environ["WAKEGATE_WEBHOOK_URL"] = "https://example.com/hook"
        path = self._write(
            """
wakes:
  hook:
    type: webhook
    url: ${WAKEGATE_WEBHOOK_URL}
checks:
  - name: daily
    type: schedule_due
    timezone: UTC
    times:
      - "09:00"
    at:
      - 2026-12-01T15:00:00+00:00
    window: 15m
    wake: hook
"""
        )
        config = load_config(path)
        self.assertEqual(config.checks[0].options["times"], [(9, 0)])
        self.assertEqual(config.checks[0].options["at"], ["2026-12-01T15:00:00+00:00"])

    def test_collects_independent_errors(self):
        path = self._write(
            """
wakes:
  hook:
    type: webhook
checks:
  - name: notes
    type: file_changed
    wake: missing
  - name: notes
    type: no_such
    wake: hook
  - name: daily
    type: schedule_due
    timezone: Not/AZone
    times: ["09:00"]
    wake: hook
  - name: clock
    type: schedule_due
    timezone: UTC
    times: ["25:99"]
    wake: hook
  - name: span
    type: schedule_due
    timezone: UTC
    times: ["09:00"]
    window: nope
    wake: hook
  - name: flag
    type: command
    run: echo hi
    wake: hook
    cooldown: 30 minutes
"""
        )
        with self.assertRaises(ConfigError) as caught:
            load_config(path)
        text = "\n".join(caught.exception.messages)
        self.assertIn("wake hook.url is required", text)
        self.assertIn("unknown wake missing", text)
        self.assertIn("duplicate check name notes", text)
        self.assertIn("unknown type", text)
        self.assertIn("Not/AZone", text)
        self.assertIn("not a valid time", text)
        self.assertIn("nope", text)
        self.assertIn("30 minutes", text)
        self.assertIn("path is required", text)

    def test_rejects_bad_url_unset_env_and_empty_command(self):
        path = self._write(
            """
wakes:
  hook:
    type: webhook
    url: ${WAKEGATE_MISSING_URL}
  local:
    type: command
    run: ""
checks:
  - name: page
    type: url_changed
    url: file:///tmp/page
    wake: hook
"""
        )
        with self.assertRaises(ConfigError) as caught:
            load_config(path)
        text = "\n".join(caught.exception.messages)
        self.assertIn("WAKEGATE_MISSING_URL", text)
        self.assertIn("must not be empty", text)
        self.assertIn("http(s)", text)

    def test_report_log_path_does_not_need_wake_credentials(self):
        path = self._write(
            """
log: custom/runs.jsonl
wakes:
  hook:
    url: ${WAKEGATE_DOES_NOT_EXIST}
"""
        )
        self.assertEqual(
            configured_log_path(path),
            self.root.resolve() / "custom" / "runs.jsonl",
        )
