"""Wake adapters, retry, and redaction. HTTP stays on the local server."""

import json
import shlex
import sys
from datetime import datetime, timezone

from wakegate.config import WakeSpec
from wakegate.errors import WakeError
from wakegate.http import post_json
from wakegate.redact import Redactor
from wakegate.wake import send_wake

from support import TempCase

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


class WakeTest(TempCase):
    def _spec(self, server, wake_type, **extra):
        values = {
            "name": "hook",
            "type": wake_type,
            "url": server.url + "/hook",
            "retries": 1,
            "backoff": 0,
            "timeout": 5,
        }
        values.update(extra)
        return WakeSpec(**values)

    def test_webhook_discord_and_slack_payloads(self):
        server = self.server()
        send_wake(self._spec(server, "webhook", headers={"Authorization": "Bearer example"}), "notes", "changed: a.md", NOW, Redactor())
        body = json.loads(server.httpd.posts[-1])
        self.assertEqual(
            body,
            {"check": "notes", "summary": "changed: a.md", "timestamp": NOW.isoformat()},
        )
        self.assertEqual(server.httpd.seen_headers[-1]["authorization"], "Bearer example")
        self.assertIn("application/json", server.httpd.seen_headers[-1]["content_type"])

        send_wake(self._spec(server, "discord"), "notes", "hello", NOW, Redactor())
        discord = json.loads(server.httpd.posts[-1])
        self.assertEqual(discord["content"], "notes: hello")
        self.assertEqual(discord["allowed_mentions"], {"parse": []})

        long_summary = "x" * 2000
        send_wake(self._spec(server, "discord"), "notes", long_summary, NOW, Redactor())
        truncated = json.loads(server.httpd.posts[-1])
        self.assertLessEqual(len(truncated["content"]), 1900)
        self.assertTrue(truncated["content"].endswith("..."))
        self.assertEqual(truncated["allowed_mentions"], {"parse": []})

        send_wake(self._spec(server, "slack"), "notes", "hello", NOW, Redactor())
        self.assertEqual(json.loads(server.httpd.posts[-1]), {"text": "notes: hello"})

    def test_retry_then_success_and_exhausted_retries(self):
        server = self.server()
        server.httpd.fail_remaining = 2
        delays = []
        send_wake(
            self._spec(server, "webhook", retries=3, backoff=0.25),
            "notes",
            "hello",
            NOW,
            Redactor(),
            sleeper=delays.append,
        )
        self.assertEqual(len(server.httpd.posts), 3)
        self.assertEqual(delays, [0.25, 0.5])

        server.httpd.posts.clear()
        server.httpd.fail_remaining = 5
        with self.assertRaises(WakeError):
            send_wake(self._spec(server, "webhook", retries=2, backoff=0), "notes", "hello", NOW, Redactor())
        self.assertEqual(len(server.httpd.posts), 2)

    def test_http_error_does_not_include_the_url(self):
        server = self.server()
        server.httpd.fail_remaining = 1
        with self.assertRaises(WakeError) as caught:
            post_json(server.url + "/hook/SUPERSECRETTOKEN", {"a": 1}, None, 5)
        self.assertNotIn("SUPERSECRETTOKEN", str(caught.exception))
        self.assertIn("HTTP 500", str(caught.exception))

    def test_command_receives_summary_and_hides_output(self):
        script = self.root / "grab.py"
        output = self.root / "out.txt"
        script.write_text(
            "import os\n"
            "from pathlib import Path\n"
            "Path(os.environ['OUT']).write_text(\n"
            "    os.environ['WAKEGATE_CHECK'] + ':' + os.environ['WAKEGATE_SUMMARY']\n"
            "    + ':' + os.environ['WAKEGATE_TIMESTAMP']\n"
            ")\n"
        )
        spec = WakeSpec(
            name="agent",
            type="command",
            run=shlex.quote(sys.executable) + " " + shlex.quote(str(script)),
            retries=1,
            backoff=0,
            timeout=5,
        )
        import os

        os.environ["OUT"] = str(output)
        send_wake(spec, "notes", "changed: a.md", NOW, Redactor())
        self.assertEqual(output.read_text(), f"notes:changed: a.md:{NOW.isoformat()}")

        leak = self.root / "leak.py"
        leak.write_text("import sys\nsys.stderr.write('SUPERSECRETTOKEN')\nraise SystemExit(4)\n")
        spec.run = shlex.quote(sys.executable) + " " + shlex.quote(str(leak))
        with self.assertRaises(WakeError) as caught:
            send_wake(spec, "notes", "hello", NOW, Redactor())
        self.assertIn("exited 4", str(caught.exception))
        self.assertNotIn("SUPERSECRETTOKEN", str(caught.exception))

    def test_command_timeout(self):
        spec = WakeSpec(
            name="agent",
            type="command",
            run=shlex.quote(sys.executable) + " -c " + shlex.quote("import time; time.sleep(5)"),
            retries=1,
            backoff=0,
            timeout=0.2,
        )
        with self.assertRaises(WakeError) as caught:
            send_wake(spec, "notes", "hello", NOW, Redactor())
        self.assertIn("timed out", str(caught.exception))
