"""treex: render a directory tree with flexible excludes.

Excludes support:
- Exact names via --exclude (matches basenames or exact relative paths).
- Regex and glob rules via --exclude-re.
  - Patterns containing * ? [] are treated as glob rules.
  - Other patterns are treated as regular expressions.
  - Rules are matched against both the basename and the relative path.
"""

from __future__ import annotations

import fnmatch
import os
import re
from argparse import ArgumentParser
from typing import Iterable, List, Tuple


def _compile_exclude_rules(patterns: Iterable[str]) -> List[Tuple[re.Pattern, bool]]:
    compiled: List[Tuple[re.Pattern, bool]] = []
    for raw in patterns:
        if not raw:
            continue
        if any(ch in raw for ch in "*?[]"):
            compiled.append((re.compile(fnmatch.translate(raw)), True))
        else:
            try:
                compiled.append((re.compile(raw), False))
            except re.error as e:
                raise SystemExit(f"Invalid regex in --exclude-re: {raw!r} -> {e}")
    return compiled


def _is_excluded(name: str, rel_posix: str, exclude_names: list[str], exclude_rules: list[tuple[re.Pattern, bool]]) -> bool:
    if name in exclude_names or rel_posix in exclude_names:
        return True
    for regex, is_glob in exclude_rules:
        if is_glob:
            if regex.fullmatch(name) or regex.fullmatch(rel_posix):
                return True
        else:
            if regex.search(name) or regex.search(rel_posix):
                return True
    return False


def print_tree(
    directory: str,
    prefix: str = "",
    rel_path: str = "",
    exclude_names: list[str] | None = None,
    exclude_rules: list[tuple[re.Pattern, bool]] | None = None,
) -> None:
    exclude_names = exclude_names or []
    exclude_rules = exclude_rules or []

    try:
        entries = os.listdir(directory)
    except PermissionError:
        print(f"{prefix}└── [Permission denied]")
        return

    # Filter entries by exact names and regex/glob rules
    filtered: list[str] = []
    for e in entries:
        rel = f"{rel_path}/{e}" if rel_path else e
        if _is_excluded(e, rel, exclude_names, exclude_rules):
            continue
        filtered.append(e)

    filtered.sort()

    for index, entry in enumerate(filtered):
        full_path = os.path.join(directory, entry)
        connector = "└── " if index == len(filtered) - 1 else "├── "
        print(f"{prefix}{connector}{entry}")

        if os.path.isdir(full_path):
            new_prefix = "    " if index == len(filtered) - 1 else "│   "
            child_rel = f"{rel_path}/{entry}" if rel_path else entry
            print_tree(full_path, prefix + new_prefix, child_rel, exclude_names, exclude_rules)


def main() -> None:
    parser = ArgumentParser(
        description=(
            "Draw a directory tree with exact excludes and regex/glob rules. "
            "Use --exclude for exact names, and --exclude-re for regex or glob rules."
        )
    )
    parser.add_argument("path", nargs="?", default=".", help="Path to directory to tree (default: current directory).")
    parser.add_argument(
        "--exclude",
        type=str,
        nargs="*",
        default=[],
        help="Names or relative paths to exclude (exact match).",
    )
    parser.add_argument(
        "--exclude-re",
        type=str,
        nargs="*",
        default=[],
        help=(
            "Regex or glob rules to exclude. "
            "Patterns containing * ? [] are treated as glob; others are regex."
        ),
    )
    args = parser.parse_args()

    print(f"Directory tree for: {args.path}")
    if args.exclude:
        print(f"Excluded names: {', '.join(args.exclude)}")
    if args.exclude_re:
        print(f"Excluded rules: {', '.join(args.exclude_re)}")

    exclude_rules = _compile_exclude_rules(args.exclude_re or [])
    print_tree(args.path, exclude_names=args.exclude, exclude_rules=exclude_rules)
