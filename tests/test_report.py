"""Report counts are log lines, not estimates."""

import json
from datetime import datetime, timedelta, timezone

from wakegate.duration import parse_since
from wakegate.report import render_report

from support import TempCase

UTC = timezone.utc


class ReportTest(TempCase):
    def test_math_window_and_malformed_lines(self):
        log = self.root / "runs.jsonl"
        rows = [
            {"ts": "2026-10-01T00:00:00+00:00", "check": "a", "outcome": "sent"},
            {"ts": "2026-10-22T00:00:00+00:00", "check": "c", "outcome": "sent"},
            {"ts": "2026-10-28T00:00:00+00:00", "check": "a", "outcome": "avoided"},
            {"ts": "2026-10-28T01:00:00+00:00", "check": "a", "outcome": "failed"},
            {"ts": "2026-10-28T02:00:00+00:00", "check": "b", "outcome": "error"},
            {"ts": "2026-10-28T03:00:00+00:00", "check": "b", "outcome": "sent"},
            {"ts": "2026-10-28T04:00:00+00:00", "check": "b", "outcome": "avoided"},
            {"ts": "not-a-time", "check": "b", "outcome": "sent"},
        ]
        lines = [json.dumps(row) for row in rows]
        lines.append("not json")
        log.write_text("\n".join(lines) + "\n")
        since = datetime(2026, 10, 22, tzinfo=UTC)
        text = render_report(log, since)

        self.assertIn("checks evaluated: 6", text)
        self.assertIn("wakes sent: 2", text)
        self.assertIn("wakes avoided: 2", text)
        self.assertIn("wake failures: 1", text)
        self.assertIn("check errors: 1", text)
        self.assertIn("malformed log lines: 2", text)
        self.assertIn(
            "a: evaluated 2, wakes sent 0, wakes avoided 1, wake failures 1, check errors 0",
            text,
        )
        self.assertIn(
            "b: evaluated 3, wakes sent 1, wakes avoided 1, wake failures 0, check errors 1",
            text,
        )
        self.assertIn(
            "c: evaluated 1, wakes sent 1, wakes avoided 0, wake failures 0, check errors 0",
            text,
        )
        self.assertEqual(2 + 2 + 1 + 1, 6)

    def test_missing_log_is_zero(self):
        text = render_report(self.root / "missing.jsonl", datetime(2026, 10, 1, tzinfo=UTC))
        self.assertIn("checks evaluated: 0", text)
        self.assertIn("wakes sent: 0", text)
        self.assertIn("wakes avoided: 0", text)
        self.assertIn("wake failures: 0", text)
        self.assertIn("check errors: 0", text)
        self.assertIn("malformed log lines: 0", text)
        self.assertIn("(none)", text)

    def test_parse_since(self):
        now = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
        self.assertEqual(parse_since("7d", now), datetime(2026, 10, 22, 12, 0, tzinfo=UTC))
        self.assertEqual(parse_since("90m", now), now - timedelta(minutes=90))
        self.assertEqual(
            parse_since("2026-10-01T00:00:00Z", now),
            datetime(2026, 10, 1, tzinfo=UTC),
        )
        with self.assertRaises(ValueError):
            parse_since("tomorrow", now)
