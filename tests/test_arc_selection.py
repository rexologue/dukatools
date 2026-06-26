from __future__ import annotations

import tempfile
import shutil
import tarfile
import unittest
from pathlib import Path

from dukatools.arc import build_selection, pack
from dukatools.pathfilter import PathFilter


class ArcSelectionTests(unittest.TestCase):
    def test_selection_uses_source_relative_filters(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "project"
            (project / "src" / "__pycache__").mkdir(parents=True)
            (project / "build").mkdir()
            (project / "src" / "app.py").write_text("print('ok')")
            (project / "src" / "__pycache__" / "app.pyc").write_text("bytecode")
            (project / "build" / "out.o").write_text("object")
            (project / "README.md").write_text("docs")

            rules = PathFilter.build(
                project,
                include_paths=["src", "README.md"],
                exclude_patterns=["*.pyc"],
            )
            selection = build_selection(project, rules)

            items = set(selection.items_for_tar)
            self.assertIn("project", items)
            self.assertIn("project/src", items)
            self.assertIn("project/src/app.py", items)
            self.assertIn("project/README.md", items)
            self.assertNotIn("project/src/__pycache__/app.pyc", items)
            self.assertNotIn("project/build/out.o", items)

    def test_system_backend_does_not_recurse_filtered_directories(self) -> None:
        if shutil.which("tar") is None or shutil.which("pigz") is None:
            self.skipTest("system backend requires tar and pigz")

        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "project"
            archive = Path(td) / "project.tar.gz"
            (project / "src").mkdir(parents=True)
            (project / "build").mkdir()
            (project / "src" / "app.py").write_text("print('ok')")
            (project / "build" / "secret.o").write_text("secret")

            pack(
                src=project,
                archive=archive,
                level=6,
                threads=1,
                include=[],
                include_re=[],
                exclude=["build"],
                exclude_re=[],
                backend="system",
            )

            with tarfile.open(archive, "r:gz") as tf:
                names = set(tf.getnames())

            self.assertIn("project/src/app.py", names)
            self.assertNotIn("project/build/secret.o", names)


if __name__ == "__main__":
    unittest.main()
