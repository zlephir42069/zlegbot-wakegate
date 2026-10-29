"""schedule_due fires a slot once, inside its window, in the configured zone."""

import unittest
from datetime import datetime, timedelta, timezone

from wakegate.checks.schedule_due import evaluate

UTC = timezone.utc


def _options(**overrides):
    options = {
        "timezone": "UTC",
        "times": [],
        "at": [],
        "window": timedelta(minutes=10),
    }
    options.update(overrides)
    return options


class ScheduleDueTest(unittest.TestCase):
    def test_daily_slot_inside_and_outside_the_window(self):
        due = evaluate(
            _options(times=[(9, 0)]),
            {},
            datetime(2026, 10, 29, 9, 5, tzinfo=UTC),
        )
        self.assertTrue(due.fired)
        self.assertEqual(due.summary, "due: 2026-10-29 09:00 UTC")
        again = evaluate(_options(times=[(9, 0)]), due.state, datetime(2026, 10, 29, 9, 6, tzinfo=UTC))
        self.assertFalse(again.fired)

        early = evaluate(_options(times=[(9, 0)]), {}, datetime(2026, 10, 29, 8, 50, tzinfo=UTC))
        self.assertFalse(early.fired)
        late = evaluate(_options(times=[(9, 0)]), {}, datetime(2026, 10, 29, 9, 10, tzinfo=UTC))
        self.assertFalse(late.fired)

    def test_window_crosses_midnight(self):
        result = evaluate(
            _options(times=[(23, 55)], window=timedelta(minutes=20)),
            {},
            datetime(2026, 10, 30, 0, 5, tzinfo=UTC),
        )
        self.assertTrue(result.fired)
        self.assertIn("2026-10-29 23:55 UTC", result.summary)

    def test_timezone_is_not_treated_as_utc(self):
        local_due = evaluate(
            _options(timezone="America/New_York", times=[(10, 0)]),
            {},
            datetime(2026, 10, 29, 14, 5, tzinfo=UTC),
        )
        self.assertTrue(local_due.fired)
        self.assertIn("2026-10-29 10:00 America/New_York", local_due.summary)

        utc_morning = evaluate(
            _options(timezone="America/New_York", times=[(10, 0)]),
            {},
            datetime(2026, 10, 29, 10, 5, tzinfo=UTC),
        )
        self.assertFalse(utc_morning.fired)

    def test_one_shot_and_several_daily_slots(self):
        now = datetime(2026, 10, 29, 9, 5, tzinfo=UTC)
        once = evaluate(_options(at=["2026-10-29T09:00:00Z"]), {}, now)
        self.assertTrue(once.fired)
        self.assertFalse(evaluate(_options(at=["2026-10-29T09:00:00Z"]), once.state, now).fired)

        naive = evaluate(
            _options(timezone="America/New_York", at=["2026-10-29T10:00:00"]),
            {},
            datetime(2026, 10, 29, 14, 5, tzinfo=UTC),
        )
        self.assertTrue(naive.fired)

        both = evaluate(_options(times=[(9, 0), (9, 2)]), {}, now)
        self.assertTrue(both.fired)
        self.assertIn("09:00 UTC", both.summary)
        self.assertIn("09:02 UTC", both.summary)
        self.assertEqual(len(both.state["fired"]), 2)

    def test_old_slots_are_pruned_when_nothing_is_due(self):
        first = evaluate(
            _options(times=[(9, 0)]),
            {},
            datetime(2026, 10, 29, 9, 5, tzinfo=UTC),
        )
        later = evaluate(
            _options(times=[(9, 0)]),
            first.state,
            datetime(2026, 10, 31, 12, 0, tzinfo=UTC),
        )
        self.assertFalse(later.fired)
        self.assertEqual(later.state["fired"], [])

    def test_future_one_shot_does_not_fire(self):
        result = evaluate(
            _options(at=["2026-12-01T15:00:00+00:00"]),
            {},
            datetime(2026, 10, 29, 12, 0, tzinfo=UTC),
        )
        self.assertFalse(result.fired)
        self.assertEqual(result.state["fired"], [])
