#!/usr/bin/env python3
"""dircat: print the content of selected files in a directory tree."""

import argparse
import os
import sys
from pathlib import Path

from dukatools.pathfilter import PathFilter, flatten_groups


def parse_args() -> argparse.Namespace:
    epilog = """
Filter rules:
  --exclude PATH       exact path to remove; relative paths are resolved from ROOT_DIR
  --include PATH       exact path to keep; relative paths are resolved from ROOT_DIR
  --exclude-re GLOB    glob-style path pattern to remove
  --include-re GLOB    glob-style path pattern to keep

If any include rule is present, dircat prints only matching files. Exclude rules
win over include rules. Glob patterns are matched against POSIX-style paths
inside ROOT_DIR. For recursive name containment, write the wildcard explicitly:
  duka dircat . --exclude-re "*__pycache__*"

Examples:
  duka dircat ./project
  duka dircat ./project --exclude .git build
  duka dircat ./project --include src pyproject.toml --exclude-re "*.pyc"
  duka dircat ./project --include-re "*.toml" "*.md"
""".strip()
    parser = argparse.ArgumentParser(
        prog="duka dircat",
        description="Recursively print selected files from a directory with clear file headers.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "directory",
        metavar="ROOT_DIR",
        help="Root directory to read files from.",
    )
    parser.add_argument(
        "-e",
        "--exclude",
        nargs="+",
        action="append",
        default=[],
        metavar="PATH",
        help="Exact file or directory path to exclude, relative to ROOT_DIR unless absolute.",
    )
    parser.add_argument(
        "-i",
        "--include",
        nargs="+",
        action="append",
        default=[],
        metavar="PATH",
        help="Exact file or directory path to include, relative to ROOT_DIR unless absolute.",
    )
    parser.add_argument(
        "--exclude-re",
        nargs="+",
        action="append",
        default=[],
        metavar="GLOB",
        help="Glob-style POSIX path pattern to exclude, e.g. '*.pyc' or '*__pycache__*'.",
    )
    parser.add_argument(
        "--include-re",
        nargs="+",
        action="append",
        default=[],
        metavar="GLOB",
        help="Glob-style POSIX path pattern to include, e.g. 'src/*.py'.",
    )
    parser.add_argument(
        "--icnlude-re",
        nargs="+",
        action="append",
        dest="include_re",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def print_file(full_path: str, display_path: str) -> None:
    label = f"# FILE: {display_path} #"
    border = "#" * len(label)

    print(border)
    print(label)
    print(border)
    print()

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        try:
            with open(full_path, "rb") as f:
                data = f.read()
            content = data.decode("utf-8", errors="replace")
        except OSError as e:
            content = f"[Error reading file as binary: {e}]"
    except OSError as e:
        content = f"[Error reading file: {e}]"

    print(content)
    print()


def main() -> None:
    args = parse_args()
    root = Path(args.directory).expanduser().resolve()

    if not root.is_dir():
        print(f"Error: {args.directory} is not a directory", file=sys.stderr)
        sys.exit(1)

    rules = PathFilter.build(
        root,
        include_paths=flatten_groups(args.include),
        exclude_paths=flatten_groups(args.exclude),
        include_patterns=flatten_groups(args.include_re),
        exclude_patterns=flatten_groups(args.exclude_re),
    )

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames.sort()
        filenames.sort()

        filtered_dirs = []
        for d in dirnames:
            full = os.path.join(dirpath, d)
            if not rules.is_excluded(full):
                filtered_dirs.append(d)
        dirnames[:] = filtered_dirs

        for fname in filenames:
            full = os.path.join(dirpath, fname)
            if not rules.selects(full):
                continue

            rel_display = os.path.relpath(full, root).replace(os.sep, "/")
            print_file(full, rel_display)


if __name__ == "__main__":
    main()
