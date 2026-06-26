from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dukatools.pathfilter import PathFilter


class PathFilterTests(unittest.TestCase):
    def test_exact_paths_are_relative_to_root_and_match_subtrees(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "build" / "nested").mkdir(parents=True)
            target = root / "build" / "nested" / "out.o"
            target.write_text("x")

            rules = PathFilter.build(root, exclude_paths=["build"])

            self.assertTrue(rules.is_excluded(root / "build"))
            self.assertTrue(rules.is_excluded(target))

    def test_glob_patterns_match_posix_paths_and_ancestors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache_file = root / "pkg" / "__pycache__" / "mod.pyc"
            cache_file.parent.mkdir(parents=True)
            cache_file.write_text("x")

            rules = PathFilter.build(root, exclude_patterns=["*__pycache__*"])

            self.assertTrue(rules.is_excluded(cache_file.parent))
            self.assertTrue(rules.is_excluded(cache_file))

    def test_include_filters_select_only_matches(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_file = root / "src" / "app.py"
            doc_file = root / "docs" / "guide.md"
            src_file.parent.mkdir()
            doc_file.parent.mkdir()
            src_file.write_text("x")
            doc_file.write_text("x")

            rules = PathFilter.build(root, include_paths=["src"])

            self.assertTrue(rules.selects(src_file))
            self.assertFalse(rules.selects(doc_file))

    def test_exclude_wins_over_include(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            keep = root / "src" / "keep.py"
            drop = root / "src" / "secret.py"
            keep.parent.mkdir()
            keep.write_text("x")
            drop.write_text("x")

            rules = PathFilter.build(root, include_paths=["src"], exclude_paths=["src/secret.py"])

            self.assertTrue(rules.selects(keep))
            self.assertFalse(rules.selects(drop))

    def test_symlink_to_outside_root_uses_lexical_path(self) -> None:
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as external_td:
            root = Path(td)
            external = Path(external_td) / "outside.txt"
            external.write_text("x")
            link = root / "outside-link.txt"
            try:
                link.symlink_to(external)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink creation is unavailable: {exc}")

            rules = PathFilter.build(root)

            self.assertEqual(rules.rel(link), "outside-link.txt")
            self.assertTrue(rules.selects(link))


if __name__ == "__main__":
    unittest.main()
