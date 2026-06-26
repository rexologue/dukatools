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
    hidden: bool = False


TOOLS: Dict[str, ToolInfo] = {
    "treex": ToolInfo(
        "dukatools.treex",
        "Directory tree with include/exclude path filters.",
        "duka treex [PATH] [--include PATH ...] [--exclude PATH ...] [--*-re GLOB ...]",
    ),
    "dircat": ToolInfo(
        "dukatools.dircat",
        "Dump selected directory files to stdout with clear headers.",
        "duka dircat ROOT_DIR [--include PATH ...] [--exclude PATH ...] [--*-re GLOB ...]",
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
    "arc": ToolInfo(
        "dukatools.arc",
        "Create/extract .tar.gz archives with include/exclude filters.",
        "duka arc pack SRC ARCHIVE.tar.gz [...] | duka arc unpack ARCHIVE.tar.gz DEST_DIR [...]",
    ),
    "pyarc": ToolInfo(
        "dukatools.pyarc",
        "Deprecated alias for arc.",
        "duka arc pack SRC ARCHIVE.tar.gz [...] | duka arc unpack ARCHIVE.tar.gz DEST_DIR [...]",
        hidden=True,
    ),
}


def _print_tools() -> None:
    print("duka: launcher-only access to dukatools.")
    print("Usage: duka <tool> [args...]")
    print("Available tools:")
    for name, info in TOOLS.items():
        if info.hidden:
            continue
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
    rc = module.main()
    if isinstance(rc, int):
        sys.exit(rc)


if __name__ == "__main__":
    main()
