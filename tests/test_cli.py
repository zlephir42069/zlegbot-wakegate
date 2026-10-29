"""CLI exit codes. 0 ok, 1 a run failure, 2 a config error."""

import json
import os
import shutil
import subprocess
import sys

from support import REPO, TempCase


class CliTest(TempCase):
    def _child_env(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
        env["WAKEGATE_WEBHOOK_URL"] = "https://example.com/hooks/wakegate"
        env["WAKEGATE_WEBHOOK_TOKEN"] = "example-token"
        env["WAKEGATE_DISCORD_URL"] = "https://example.com/discord"
        env["WAKEGATE_SLACK_URL"] = "https://example.com/slack"
        return env

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "wakegate", *args],
            cwd=self.root,
            env=self._child_env(),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_usage_and_validate(self):
        bare = self._run()
        self.assertEqual(bare.returncode, 2)

        missing = self._run("validate", "--config", "missing.yaml")
        self.assertEqual(missing.returncode, 2)
        self.assertIn("config not found", missing.stderr)

        example = self._run("validate", "--config", str(REPO / "wakegate.example.yaml"))
        self.assertEqual(example.returncode, 0, example.stderr)
        self.assertEqual(example.stdout.strip(), "ok")

        bad = self.root / "wakegate.yaml"
        bad.write_text("checks: []\n")
        invalid = self._run("validate")
        self.assertEqual(invalid.returncode, 2)
        self.assertIn("config error:", invalid.stderr)

    def test_run_exit_codes_and_dry_run(self):
        notes = self.root / "notes"
        notes.write_text("one")
        config = self.root / "wakegate.yaml"
        config.write_text(
            """
lock_timeout: 2
state: .wakegate/state.json
log: .wakegate/runs.jsonl
wakes:
  hook:
    type: webhook
    url: ${WAKEGATE_WEBHOOK_URL}
    retries: 1
    backoff: 0
    timeout: 5
checks:
  - name: notes
    type: file_changed
    path: ./notes
    wake: hook
"""
        )
        dry = self._run("run", "--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("notes ok:", dry.stdout)
        self.assertFalse((self.root / ".wakegate").exists())

        first = self._run("run")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, "")
        self.assertTrue((self.root / ".wakegate" / "state.json").is_file())

        config.write_text(
            """
wakes:
  hook:
    type: webhook
    url: ${WAKEGATE_WEBHOOK_URL}
    retries: 1
    backoff: 0
checks:
  - name: notes
    type: file_changed
    path: ./missing
    wake: hook
"""
        )
        failed = self._run("run")
        self.assertEqual(failed.returncode, 1)
        self.assertIn("error notes:", failed.stderr)

    def test_report_reads_the_log(self):
        log = self.root / ".wakegate" / "runs.jsonl"
        log.parent.mkdir()
        log.write_text(
            json.dumps(
                {
                    "ts": "2026-10-28T00:00:00+00:00",
                    "check": "notes",
                    "outcome": "avoided",
                }
            )
            + "\n"
        )
        result = self._run("report", "--since", "2026-10-22T00:00:00Z")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("checks evaluated: 1", result.stdout)
        self.assertIn("wakes avoided: 1", result.stdout)
        self.assertIn("wakes sent: 0", result.stdout)

        bad_since = self._run("report", "--since", "tomorrow")
        self.assertEqual(bad_since.returncode, 2)
        self.assertIn("config error:", bad_since.stderr)

    def test_console_script(self):
        script = shutil.which("wakegate")
        self.assertIsNotNone(script)
        result = subprocess.run(
            [script, "validate", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("validate", result.stdout)
