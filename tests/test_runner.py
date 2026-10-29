"""Runner idempotency, cooldown, failed wakes, dry-run, and redaction."""

import contextlib
import io
import json
import os
from datetime import datetime, timedelta, timezone

from wakegate.config import load_config
from wakegate.report import render_report
from wakegate.run import execute
from wakegate.wake import _send_once

from support import TempCase

UTC = timezone.utc
START = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


class RunnerTest(TempCase):
    def _config(self, body):
        path = self.root / "wakegate.yaml"
        path.write_text(body)
        return load_config(path)

    def _file_config(self, server, cooldown="0s"):
        os.environ["WAKEGATE_WEBHOOK_TOKEN"] = "SUPERSECRETTOKEN"
        return self._config(
            f"""
lock_timeout: 2
state: .wakegate/state.json
log: .wakegate/runs.jsonl
wakes:
  hook:
    type: webhook
    url: {server.url}/hook/${{WAKEGATE_WEBHOOK_TOKEN}}
    retries: 1
    backoff: 0
    timeout: 5
checks:
  - name: notes
    type: file_changed
    path: ./notes
    wake: hook
    cooldown: {cooldown}
"""
        )

    def _state(self):
        return json.loads((self.root / ".wakegate" / "state.json").read_text())

    def _log(self):
        path = self.root / ".wakegate" / "runs.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def test_baseline_change_and_same_bytes_do_not_double_wake(self):
        server = self.server()
        config = self._file_config(server)
        notes = self.root / "notes"
        notes.write_text("v1")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(execute(config, now=START), 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(server.httpd.posts, [])
        self.assertEqual(self._log()[0]["outcome"], "avoided")
        self.assertEqual(self._log()[0]["reason"], "not_fired")

        notes.write_text("v2")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(execute(config, now=START + timedelta(minutes=1)), 0)
        self.assertIn("woke notes:", stdout.getvalue())
        self.assertEqual(len(server.httpd.posts), 1)
        payload = json.loads(server.httpd.posts[0])
        self.assertEqual(payload["check"], "notes")
        self.assertNotIn("SUPERSECRETTOKEN", json.dumps(payload))

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(execute(config, now=START + timedelta(minutes=2)), 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(len(server.httpd.posts), 1)
        self.assertEqual(self._log()[-1]["outcome"], "avoided")

        text = render_report(config.log_path, START - timedelta(days=1))
        self.assertIn("checks evaluated: 3", text)
        self.assertIn("wakes sent: 1", text)
        self.assertIn("wakes avoided: 2", text)
        self.assertIn("wake failures: 0", text)

    def test_cooldown_keeps_the_pending_change(self):
        server = self.server()
        config = self._file_config(server, cooldown="30m")
        notes = self.root / "notes"
        notes.write_text("v1")
        execute(config, now=START)
        notes.write_text("v2")
        execute(config, now=START + timedelta(minutes=1))
        self.assertEqual(len(server.httpd.posts), 1)
        v2_hash = self._state()["checks"]["notes"]["data"]["files"]["notes"]

        notes.write_text("v3")
        execute(config, now=START + timedelta(minutes=2))
        self.assertEqual(len(server.httpd.posts), 1)
        self.assertEqual(self._state()["checks"]["notes"]["data"]["files"]["notes"], v2_hash)
        self.assertEqual(self._log()[-1]["reason"], "cooldown")
        self.assertTrue(self._log()[-1]["fired"])

        execute(config, now=START + timedelta(minutes=40))
        self.assertEqual(len(server.httpd.posts), 2)
        self.assertNotEqual(self._state()["checks"]["notes"]["data"]["files"]["notes"], v2_hash)

    def test_failed_wake_does_not_mark_the_check_handled(self):
        server = self.server()
        config = self._file_config(server)
        notes = self.root / "notes"
        notes.write_text("v1")
        execute(config, now=START)
        baseline = self._state()["checks"]["notes"]["data"]["files"]["notes"]
        notes.write_text("v2")
        server.httpd.fail_remaining = 5
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(execute(config, now=START + timedelta(minutes=1)), 1)
        self.assertIn("wake failed notes", stderr.getvalue())
        self.assertNotIn("SUPERSECRETTOKEN", stderr.getvalue())
        self.assertEqual(self._state()["checks"]["notes"]["data"]["files"]["notes"], baseline)
        self.assertEqual(self._log()[-1]["outcome"], "failed")

        server.httpd.fail_remaining = 0
        self.assertEqual(execute(config, now=START + timedelta(minutes=2)), 0)
        self.assertNotEqual(self._state()["checks"]["notes"]["data"]["files"]["notes"], baseline)
        blob = (self.root / ".wakegate" / "state.json").read_text() + (self.root / ".wakegate" / "runs.jsonl").read_text()
        self.assertNotIn("SUPERSECRETTOKEN", blob)

    def test_dry_run_has_no_side_effects(self):
        server = self.server()
        config = self._file_config(server)
        notes = self.root / "notes"
        notes.write_text("v1")
        execute(config, now=START)
        notes.write_text("v2")
        state_before = (self.root / ".wakegate" / "state.json").read_bytes()
        log_before = (self.root / ".wakegate" / "runs.jsonl").read_bytes()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(execute(config, dry_run=True, now=START + timedelta(minutes=1)), 0)
        self.assertIn("notes fired:", stdout.getvalue())
        self.assertEqual(len(server.httpd.posts), 0)
        self.assertEqual((self.root / ".wakegate" / "state.json").read_bytes(), state_before)
        self.assertEqual((self.root / ".wakegate" / "runs.jsonl").read_bytes(), log_before)

        fresh_config = self._config(
            f"""
lock_timeout: 2
state: {self.root / "empty" / "state.json"}
log: {self.root / "empty" / "runs.jsonl"}
wakes:
  hook:
    type: webhook
    url: {server.url}/hook/${{WAKEGATE_WEBHOOK_TOKEN}}
    retries: 1
    backoff: 0
    timeout: 5
checks:
  - name: notes
    type: file_changed
    path: {notes}
    wake: hook
"""
        )
        with contextlib.redirect_stdout(io.StringIO()):
            execute(fresh_config, dry_run=True, now=START)
        self.assertFalse((self.root / "empty").exists())

    def test_check_error_and_redacted_exception(self):
        server = self.server()
        config = self._file_config(server)
        config.checks[0].options["path"] = str(self.root / "missing")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(execute(config, now=START), 1)
        self.assertIn("error notes:", stderr.getvalue())
        self.assertEqual(self._log()[-1]["outcome"], "error")
        self.assertFalse((self.root / ".wakegate" / "state.json").exists())

        notes = self.root / "notes"
        notes.write_text("v1")
        config = self._file_config(server)
        execute(config, now=START)
        notes.write_text("v2")

        def boom(spec, check, summary, now):
            raise RuntimeError("leaked SUPERSECRETTOKEN " + spec.url)

        import wakegate.wake as wake_module

        original = wake_module._send_once
        wake_module._send_once = boom
        try:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(execute(config, now=START + timedelta(minutes=1)), 1)
        finally:
            wake_module._send_once = original
        self.assertNotIn("SUPERSECRETTOKEN", stderr.getvalue())
        self.assertNotIn("SUPERSECRETTOKEN", (self.root / ".wakegate" / "runs.jsonl").read_text())
        self.assertIs(original, _send_once)

    def test_corrupt_state_exits_1_and_releases_the_lock(self):
        server = self.server()
        config = self._file_config(server)
        state_path = self.root / ".wakegate" / "state.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text("{")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(execute(config, now=START), 1)
        self.assertIn("state error", stderr.getvalue())
        from wakegate.state import StateStore

        store = StateStore(state_path)
        store.acquire(1)
        store.release()
