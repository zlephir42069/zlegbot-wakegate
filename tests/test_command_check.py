"""command fires on the transition into fire_on, not on every matching exit."""

import shlex
import sys
from datetime import datetime, timezone

from wakegate.checks.command import evaluate
from wakegate.errors import CheckError

from support import TempCase

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def _python(code: str) -> str:
    return shlex.quote(sys.executable) + " -c " + shlex.quote(code)


class CommandCheckTest(TempCase):
    def test_edge_and_configurable_exit_code(self):
        options = {"run": _python("import sys; sys.exit(3)"), "fire_on": 3, "timeout": 5}
        first = evaluate(options, {}, NOW)
        self.assertTrue(first.fired)
        self.assertEqual(first.summary, "exit 3")
        self.assertEqual(first.state, {"exit": 3})
        second = evaluate(options, first.state, NOW)
        self.assertFalse(second.fired)

        other = {"run": _python("import sys; sys.exit(1)"), "fire_on": 3, "timeout": 5}
        cleared = evaluate(other, second.state, NOW)
        self.assertFalse(cleared.fired)
        self.assertEqual(cleared.state["exit"], 1)
        again = evaluate(options, cleared.state, NOW)
        self.assertTrue(again.fired)

    def test_stdout_is_not_part_of_the_result(self):
        options = {
            "run": _python("print('SUPERSECRETTOKEN'); raise SystemExit(0)"),
            "fire_on": 0,
            "timeout": 5,
        }
        result = evaluate(options, {}, NOW)
        self.assertEqual(result.summary, "exit 0")
        self.assertNotIn("SUPERSECRETTOKEN", result.summary)
        self.assertNotIn("SUPERSECRETTOKEN", str(result.state))

    def test_timeout(self):
        options = {"run": _python("import time; time.sleep(5)"), "fire_on": 0, "timeout": 0.2}
        with self.assertRaises(CheckError) as caught:
            evaluate(options, {}, NOW)
        self.assertIn("timed out", str(caught.exception))
