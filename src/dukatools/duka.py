"""duka: dukatools launcher (the only supported entry point).

Usage:
  duka <tool> [args...]
  duka --list
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from typing import Dict

@dataclass(frozen=True)
class ToolInfo:
    module: str
    summary: str
    usage: str


TOOLS: Dict[str, ToolInfo] = {
    "treex": ToolInfo(
        "dukatools.treex",
        "Directory tree with excludes (exact names + regex/glob).",
        "duka treex [PATH] [--exclude NAME ...] [--exclude-re RULE ...]",
    ),
    "dircat": ToolInfo(
        "dukatools.dircat",
        "Dump directory files to stdout with clear headers.",
        "duka dircat ROOT_DIR [--exclude PATH ...] [--exclude-re RULE ...]",
    ),
    "vidcut": ToolInfo(
        "dukatools.vidcut",
        "Fast video trimming via FFmpeg (fast copy + accurate fallback).",
        "duka vidcut INPUT... [--from START] [--to END] [--duration D] [--trim-*] [--accurate]",
    ),
    "pydown": ToolInfo(
        "dukatools.pydown",
        "Download python-build-standalone releases.",
        "duka pydown --dest PATH [--version X.Y[.Z]] [--variant NAME] [--extract]",
    ),
    "pyarc": ToolInfo(
        "dukatools.pyarc",
        "Create/extract .tar.gz using tar + pigz.",
        "duka pyarc pack SRC ARCHIVE.tar.gz [...] | duka pyarc unpack ARCHIVE.tar.gz DEST_DIR [...]",
    ),
}


def _print_tools() -> None:
    print("duka: launcher-only access to dukatools.")
    print("Usage: duka <tool> [args...]")
    print("Available tools:")
    for name, info in TOOLS.items():
        print(f"  {name:<7} {info.summary}")
        print(f"         {info.usage}")
    print("Tip: run `duka <tool> --help` for full usage and examples.")
    print("Example: duka treex . --exclude .git --exclude-re \"*.pyc\"")


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

    module_path = TOOLS[tool].module
    module = importlib.import_module(module_path)
    if not hasattr(module, "main"):
        print(f"duka: tool '{tool}' has no main()", file=sys.stderr)
        sys.exit(2)

    sys.argv = [tool] + argv[1:]
    module.main()


if __name__ == "__main__":
    main()
