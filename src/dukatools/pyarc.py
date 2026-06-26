"""Compatibility wrapper for the renamed arc tool."""

from __future__ import annotations

import sys

from dukatools.arc import main as arc_main


def main() -> int:
    print("duka pyarc is deprecated; use duka arc instead.", file=sys.stderr)
    return arc_main()


if __name__ == "__main__":
    raise SystemExit(main())
