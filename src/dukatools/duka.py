"""duka: run dukatools utilities without memorizing binary names.

Usage:
  duka <tool> [args...]
  duka --list
"""

from __future__ import annotations

import importlib
import sys
from typing import Dict, Tuple

TOOLS: Dict[str, Tuple[str, str]] = {
    "treex": ("dukatools.treex", "Directory tree with excludes"),
    "dircat": ("dukatools.dircat", "Dump directory files to stdout"),
    "vidcut": ("dukatools.vidcut", "Fast and accurate video trimming"),
    "pydown": ("dukatools.pydown", "Download python-build-standalone releases"),
    "pyarc": ("dukatools.pyarc", "Create and extract .tar.gz archives"),
}


def _print_tools() -> None:
    print("duka: choose a tool to run.")
    print("Available tools:")
    for name, (_, desc) in TOOLS.items():
        print(f"  {name:<7} {desc}")
    print("Example: duka treex --path .")


def main() -> None:
    argv = sys.argv[1:]
    if not argv:
        print("duka: please specify a tool to run.")
        _print_tools()
        sys.exit(2)

    if argv[0] in ("-h", "--help", "--list"):
        _print_tools()
        return

    tool = argv[0]
    if tool not in TOOLS:
        print(f"duka: unknown tool: {tool}", file=sys.stderr)
        _print_tools()
        sys.exit(2)

    module_path, _ = TOOLS[tool]
    module = importlib.import_module(module_path)
    if not hasattr(module, "main"):
        print(f"duka: tool '{tool}' has no main()", file=sys.stderr)
        sys.exit(2)

    sys.argv = [tool] + argv[1:]
    module.main()


if __name__ == "__main__":
    main()
