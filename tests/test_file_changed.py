"""file_changed baselines once, then fires only when bytes change."""

import unittest
from datetime import datetime, timezone

from wakegate.checks.file_changed import evaluate
from wakegate.errors import CheckError
from wakegate.fileset import glob_match

from support import TempCase

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


class FileChangedTest(TempCase):
    def test_directory_glob_and_idempotent_hash(self):
        folder = self.root / "docs"
        (folder / "sub").mkdir(parents=True)
        (folder / "a.md").write_text("a")
        (folder / "b.txt").write_text("ignore")
        (folder / "sub" / "c.md").write_text("c")
        options = {"path": str(folder), "glob": "*.md", "recursive": True}

        first = evaluate(options, {}, NOW)
        self.assertFalse(first.fired)
        self.assertEqual(first.summary, "baseline, 2 files")
        self.assertEqual(set(first.state["files"]), {"a.md", "sub/c.md"})

        second = evaluate(options, first.state, NOW)
        self.assertFalse(second.fired)
        self.assertEqual(second.summary, "unchanged")

        (folder / "a.md").write_text("changed")
        (folder / "sub" / "d.md").write_text("d")
        third = evaluate(options, second.state, NOW)
        self.assertTrue(third.fired)
        self.assertIn("changed: a.md", third.summary)
        self.assertIn("added: sub/d.md", third.summary)

        fourth = evaluate(options, third.state, NOW)
        self.assertFalse(fourth.fired)

        (folder / "sub" / "d.md").unlink()
        fifth = evaluate(options, fourth.state, NOW)
        self.assertTrue(fifth.fired)
        self.assertIn("removed: sub/d.md", fifth.summary)

    def test_non_recursive_skips_nested_files(self):
        folder = self.root / "docs"
        (folder / "sub").mkdir(parents=True)
        (folder / "a.md").write_text("a")
        (folder / "sub" / "c.md").write_text("c")
        options = {"path": str(folder), "glob": "*.md", "recursive": False}
        result = evaluate(options, {}, NOW)
        self.assertEqual(set(result.state["files"]), {"a.md"})
        self.assertEqual(result.summary, "baseline, 1 file")

    def test_single_file_and_missing_path(self):
        path = self.root / "note.txt"
        path.write_text("v1")
        options = {"path": str(path), "glob": None, "recursive": True}
        first = evaluate(options, {}, NOW)
        self.assertFalse(first.fired)
        path.write_text("v2")
        second = evaluate(options, first.state, NOW)
        self.assertTrue(second.fired)
        self.assertIn("changed: note.txt", second.summary)
        with self.assertRaises(CheckError):
            evaluate({"path": str(self.root / "missing"), "glob": None, "recursive": True}, {}, NOW)

    def test_symlinks_are_not_followed(self):
        folder = self.root / "docs"
        folder.mkdir()
        outside = self.root / "outside.txt"
        outside.write_text("v1")
        (folder / "link.txt").symlink_to(outside)
        real = folder / "real.txt"
        real.write_text("a")
        options = {"path": str(folder), "glob": None, "recursive": True}
        first = evaluate(options, {}, NOW)
        self.assertEqual(set(first.state["files"]), {"real.txt"})
        outside.write_text("v2")
        self.assertFalse(evaluate(options, first.state, NOW).fired)

        nested = self.root / "nested"
        nested.mkdir()
        (nested / "x.txt").write_text("x")
        (folder / "nested").symlink_to(nested, target_is_directory=True)
        self.assertFalse(evaluate(options, first.state, NOW).fired)
        real.write_text("b")
        changed = evaluate(options, first.state, NOW)
        self.assertTrue(changed.fired)
        self.assertIn("real.txt", changed.summary)

        linked_root = self.root / "linkdir"
        linked_root.symlink_to(folder, target_is_directory=True)
        with self.assertRaises(CheckError):
            evaluate({"path": str(linked_root), "glob": None, "recursive": True}, {}, NOW)


class GlobTest(unittest.TestCase):
    def test_glob_dialect(self):
        self.assertTrue(glob_match("a.md", "*.md"))
        self.assertTrue(glob_match("sub/a.md", "*.md"))
        self.assertFalse(glob_match("a.txt", "*.md"))
        self.assertTrue(glob_match("sub/a.md", "sub/*.md"))
        self.assertFalse(glob_match("sub/nested/a.md", "sub/*.md"))
        self.assertTrue(glob_match("a.md", "**/*.md"))
        self.assertTrue(glob_match("sub/nested/a.md", "**/*.md"))
        self.assertFalse(glob_match("sub/a.txt", "**/*.md"))
        self.assertTrue(glob_match("docs/a.md", "docs/**/*.md"))
        self.assertTrue(glob_match("docs/sub/a.md", "docs/**/*.md"))
        self.assertFalse(glob_match("other/a.md", "docs/**/*.md"))
