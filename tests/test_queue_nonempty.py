"""queue_nonempty fires above the threshold only when the fingerprint changes."""

from datetime import datetime, timezone

from wakegate.checks.queue_nonempty import evaluate
from wakegate.errors import CheckError

from support import TempCase

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


class QueueTest(TempCase):
    def _dir_options(self, path, **extra):
        options = {"path": str(path), "glob": "*.txt", "recursive": False, "threshold": 0}
        options.update(extra)
        return options

    def test_directory_fingerprint_and_refill(self):
        folder = self.root / "inbox"
        folder.mkdir()
        options = self._dir_options(folder)
        empty = evaluate(options, {}, NOW)
        self.assertFalse(empty.fired)
        self.assertEqual(empty.summary, "0 items")

        (folder / "a.txt").write_text("a")
        waiting = evaluate(options, empty.state, NOW)
        self.assertTrue(waiting.fired)
        self.assertEqual(waiting.summary, "1 item waiting")
        self.assertFalse(evaluate(options, waiting.state, NOW).fired)

        (folder / "b.txt").write_text("b")
        more = evaluate(options, waiting.state, NOW)
        self.assertTrue(more.fired)
        self.assertEqual(more.summary, "2 items waiting")

        (folder / "a.txt").unlink()
        (folder / "b.txt").unlink()
        drained = evaluate(options, more.state, NOW)
        self.assertFalse(drained.fired)
        (folder / "a.txt").write_text("a")
        refilled = evaluate(options, drained.state, NOW)
        self.assertTrue(refilled.fired)

    def test_threshold_is_strictly_greater_than(self):
        folder = self.root / "inbox"
        folder.mkdir()
        (folder / "a.txt").write_text("a")
        (folder / "b.txt").write_text("b")
        options = self._dir_options(folder, threshold=2)
        self.assertFalse(evaluate(options, {}, NOW).fired)
        (folder / "c.txt").write_text("c")
        self.assertTrue(evaluate(options, {}, NOW).fired)

    def test_json_array_ignores_key_order_and_whitespace(self):
        path = self.root / "jobs.json"
        path.write_text('[ {"b": 1, "a": 2} ]')
        options = {"path": str(path), "glob": None, "recursive": False, "threshold": 0}
        first = evaluate(options, {}, NOW)
        self.assertTrue(first.fired)
        path.write_text('[{"a":2,"b":1}]')
        self.assertFalse(evaluate(options, first.state, NOW).fired)
        path.write_text("[1, 2]")
        self.assertTrue(evaluate(options, first.state, NOW).fired)

        path.write_text('{"a": 1}')
        with self.assertRaises(CheckError) as caught:
            evaluate(options, {}, NOW)
        self.assertIn("array", str(caught.exception))

    def test_jsonl_counts_records_not_blank_lines(self):
        path = self.root / "jobs.jsonl"
        path.write_text('{"a": 1}\n\n{"b": 2}\n')
        options = {"path": str(path), "glob": None, "recursive": False, "threshold": 0}
        first = evaluate(options, {}, NOW)
        self.assertTrue(first.fired)
        self.assertEqual(first.state["count"], 2)
        path.write_text('{ "b" : 2 }\n{"a":1}\n')
        # Line order changed, so this is a different queue.
        self.assertTrue(evaluate(options, first.state, NOW).fired)
        path.write_text('{"b":2}\n{"a":1}\n')
        reordered = evaluate(options, {}, NOW)
        path.write_text('{ "b" : 2 }\n{ "a" : 1 }\n')
        self.assertFalse(evaluate(options, reordered.state, NOW).fired)

        path.write_text('{"ok": 1}\nnot-json\n')
        with self.assertRaises(CheckError) as caught:
            evaluate(options, {}, NOW)
        self.assertIn("line 2", str(caught.exception))

    def test_rejects_unknown_files_and_missing_paths(self):
        path = self.root / "notes.txt"
        path.write_text("x")
        with self.assertRaises(CheckError):
            evaluate({"path": str(path), "glob": None, "recursive": False, "threshold": 0}, {}, NOW)
        with self.assertRaises(CheckError):
            evaluate(
                {"path": str(self.root / "missing"), "glob": None, "recursive": False, "threshold": 0},
                {},
                NOW,
            )
