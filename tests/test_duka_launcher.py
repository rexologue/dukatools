from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class DukaLauncherTests(unittest.TestCase):
    def test_version_matches_pyproject(self) -> None:
        root = Path(__file__).resolve().parents[1]
        expected = ""
        in_project = False
        for line in (root / "pyproject.toml").read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped == "[project]":
                in_project = True
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                in_project = False
                continue
            if in_project and stripped.startswith("version"):
                expected = stripped.split("=", 1)[1].strip().strip('"')
                break

        env = os.environ.copy()
        env["PYTHONPATH"] = "src"
        proc = subprocess.run(
            [sys.executable, "-m", "dukatools.duka", "--version"],
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), expected)
        self.assertEqual(proc.stderr, "")

    def test_launcher_propagates_tool_return_code(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            env = os.environ.copy()
            env["PYTHONPATH"] = "src"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "dukatools.duka",
                    "arc",
                    "pack",
                    str(Path(td) / "missing"),
                    str(Path(td) / "out.tar.gz"),
                    "--backend",
                    "python",
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

            self.assertEqual(proc.returncode, 1)


if __name__ == "__main__":
    unittest.main()
