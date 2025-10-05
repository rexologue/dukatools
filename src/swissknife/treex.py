import os
import fnmatch
from argparse import ArgumentParser


def print_tree(directory: str, prefix: str = "", exclude_names: list[str] | None = None, exclude_patterns: list[str] | None = None) -> None:
    exclude_names = exclude_names or []
    exclude_patterns = exclude_patterns or []

    try:
        entries = os.listdir(directory)
    except PermissionError:
        print(f"{prefix}└── [Permission denied]")
        return

    # Filter entries by exact names and glob patterns
    filtered: list[str] = []
    for e in entries:
        if e in exclude_names:
            continue
        if any(fnmatch.fnmatch(e, pat) for pat in exclude_patterns):
            continue
        filtered.append(e)

    filtered.sort()

    for index, entry in enumerate(filtered):
        full_path = os.path.join(directory, entry)
        connector = "└── " if index == len(filtered) - 1 else "├── "
        print(f"{prefix}{connector}{entry}")

        if os.path.isdir(full_path):
            new_prefix = "    " if index == len(filtered) - 1 else "│   "
            print_tree(full_path, prefix + new_prefix, exclude_names, exclude_patterns)


def main() -> None:
    parser = ArgumentParser(description="Draw a directory in a tree format with exclusion patterns.")
    parser.add_argument("--path", type=str, default=".", help="Path to directory to tree.")
    parser.add_argument("--exclude", type=str, nargs="*", default=[], help="List of directory/file names to exclude (exact match).")
    parser.add_argument("--exclude-pattern", type=str, nargs="*", default=[], help="List of glob patterns to exclude (e.g. __pycache__, *.egg-info)")
    args = parser.parse_args()

    print(f"Directory tree for: {args.path}")
    if args.exclude:
        print(f"Excluded names: {', '.join(args.exclude)}")
    if args.exclude_pattern:
        print(f"Excluded patterns: {', '.join(args.exclude_pattern)}")

    print_tree(args.path, exclude_names=args.exclude, exclude_patterns=args.exclude_pattern)
