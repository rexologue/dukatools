"""treex: render a filtered directory tree."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from dukatools.pathfilter import PathFilter, flatten_groups


@dataclass
class TreeNode:
    name: str
    children: list["TreeNode"]
    denied: bool = False


def build_tree(path: Path, rules: PathFilter, *, is_root: bool = False) -> TreeNode | None:
    if not is_root and rules.is_excluded(path):
        return None

    if not path.is_dir():
        if rules.selects(path):
            return TreeNode(path.name, [])
        return None

    children: list[TreeNode] = []
    try:
        entries = sorted(path.iterdir(), key=lambda p: p.name)
    except PermissionError:
        if is_root or rules.selects(path):
            return TreeNode(path.name, [TreeNode("[Permission denied]", [], denied=True)])
        return None

    for entry in entries:
        child = build_tree(entry, rules)
        if child is not None:
            children.append(child)

    if is_root or not rules.has_includes or rules.selects(path) or children:
        return TreeNode(path.name, children)
    return None


def print_tree(node: TreeNode, prefix: str = "") -> None:
    for index, child in enumerate(node.children):
        is_last = index == len(node.children) - 1
        connector = "└── " if is_last else "├── "
        print(f"{prefix}{connector}{child.name}")
        if child.children and not child.denied:
            new_prefix = "    " if is_last else "│   "
            print_tree(child, prefix + new_prefix)


def build_parser() -> argparse.ArgumentParser:
    epilog = """
Filter rules:
  --exclude PATH       exact path to remove; relative paths are resolved from PATH
  --include PATH       exact path to keep; relative paths are resolved from PATH
  --exclude-re GLOB    glob-style path pattern to remove
  --include-re GLOB    glob-style path pattern to keep

Include and exclude can be combined. If any include rule is present, treex shows
only matching paths and the parent directories needed to display them. Exclude
rules win over include rules.

Glob patterns are matched against POSIX-style paths inside the inspected root.
For recursive name containment, write the wildcard explicitly:
  duka treex . --exclude-re "*__pycache__*"

Examples:
  duka treex .
  duka treex ./project --exclude .git build
  duka treex ./project --include src pyproject.toml --exclude-re "*.pyc"
  duka treex ./project --include-re "src/*.py" --exclude tests
""".strip()
    parser = argparse.ArgumentParser(
        prog="duka treex",
        description="Render a directory tree with exact path and glob-style include/exclude filters.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Directory to inspect (default: current directory).",
    )
    parser.add_argument(
        "--exclude",
        nargs="+",
        action="append",
        default=[],
        metavar="PATH",
        help="Exact file or directory path to exclude, relative to PATH unless absolute.",
    )
    parser.add_argument(
        "--include",
        nargs="+",
        action="append",
        default=[],
        metavar="PATH",
        help="Exact file or directory path to include, relative to PATH unless absolute.",
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
        dest="include_re",
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
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = Path(args.path).expanduser().resolve()

    if not root.is_dir():
        print(f"Error: {args.path} is not a directory", file=sys.stderr)
        raise SystemExit(1)

    rules = PathFilter.build(
        root,
        include_paths=flatten_groups(args.include),
        exclude_paths=flatten_groups(args.exclude),
        include_patterns=flatten_groups(args.include_re),
        exclude_patterns=flatten_groups(args.exclude_re),
    )

    print(f"Directory tree for: {args.path}")
    if args.include:
        print(f"Included paths: {', '.join(flatten_groups(args.include))}")
    if args.include_re:
        print(f"Included patterns: {', '.join(flatten_groups(args.include_re))}")
    if args.exclude:
        print(f"Excluded paths: {', '.join(flatten_groups(args.exclude))}")
    if args.exclude_re:
        print(f"Excluded patterns: {', '.join(flatten_groups(args.exclude_re))}")

    tree = build_tree(root, rules, is_root=True)
    if tree is not None:
        print_tree(tree)


if __name__ == "__main__":
    main()
