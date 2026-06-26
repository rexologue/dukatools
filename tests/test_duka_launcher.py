from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class DukaLauncherTests(unittest.TestCase):
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
