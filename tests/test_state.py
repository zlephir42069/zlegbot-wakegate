"""Atomic state writes and the overlapping-run lock."""

import json
from unittest.mock import patch

from wakegate.errors import StateError
from wakegate.state import StateStore, append_jsonl

from support import TempCase


class StateTest(TempCase):
    def test_save_is_atomic_and_round_trips(self):
        path = self.root / "state.json"
        store = StateStore(path)
        store.save({"checks": {"notes": {"data": {"hash": "abc"}, "last_wake": None}}})
        self.assertFalse(path.with_name(path.name + ".tmp").exists())
        self.assertEqual(store.load()["checks"]["notes"]["data"]["hash"], "abc")

        with patch("wakegate.state.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(StateError):
                store.save({"checks": {"notes": {"data": {"hash": "new"}}}})
        self.assertEqual(json.loads(path.read_text())["checks"]["notes"]["data"]["hash"], "abc")
        self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_corrupt_state_is_an_error(self):
        path = self.root / "state.json"
        path.write_text("{")
        with self.assertRaises(StateError):
            StateStore(path).load()

    def test_lock_blocks_until_timeout_then_can_be_taken(self):
        path = self.root / "state.json"
        first = StateStore(path)
        second = StateStore(path)
        first.acquire(1)
        with self.assertRaises(TimeoutError):
            second.acquire(0.25)
        first.release()
        second.acquire(1)
        second.save({"checks": {}})
        second.release()
        self.assertEqual(StateStore(path).load(), {"checks": {}})

    def test_jsonl_appends_one_object_per_line(self):
        path = self.root / "runs.jsonl"
        append_jsonl(path, [{"check": "a", "outcome": "sent"}, {"check": "b", "outcome": "avoided"}])
        append_jsonl(path, [{"check": "c", "outcome": "failed"}])
        lines = path.read_text().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(json.loads(lines[2])["check"], "c")
