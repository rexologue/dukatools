#!/usr/bin/env python3
"""dircat: print the content of files in a directory tree.

Features:
- Walks the directory recursively and prints each file with a clear header.
- Excludes paths by exact match (--exclude) and by regex/glob rules (--exclude-re).
- Glob rules use * ? [] and match full relative paths or basenames.
- Regex rules are searched against basenames and relative paths.
"""

import argparse
import fnmatch
import os
import re
import sys
from typing import List, Tuple


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Рекурсивно вывести содержимое всех файлов в директории."
    )
    parser.add_argument(
        "directory",
        help="Корневая директория, для которой нужно вывести содержимое файлов.",
    )
    parser.add_argument(
        "-e",
        "--exclude",
        nargs="+",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "Исключить файлы/директории по путям. "
            "Пути могут быть абсолютными или относительными относительно корня обхода. "
            "Работает только по точному совпадению пути."
        ),
    )
    parser.add_argument(
        "--exclude-re",
        nargs="+",
        action="append",
        default=[],
        metavar="PATTERN",
        help=(
            "Исключить файлы/директории по паттернам (regex / glob). "
            "Паттерны с символами * ? [] трактуются как glob (например: *.obj). "
            "Остальные трактуются как regex и применяются к имени и относительному пути. "
            "Работают независимо от вложенности."
        ),
    )
    return parser.parse_args()


def flatten(nested: List[List[str]]) -> List[str]:
    result: List[str] = []
    for group in nested:
        result.extend(group)
    return result


def build_exclude_paths(root_abs: str, exclude_args: List[str]) -> set:
    """
    Возвращает множество абсолютных путей, которые нужно исключить.
    Если путь относительный — он трактуется относительно root_abs.
    """
    exclude_abs_paths = set()

    for raw in exclude_args:
        if not raw:
            continue

        norm = os.path.normpath(raw)

        if os.path.isabs(norm):
            full = os.path.abspath(norm)
        else:
            full = os.path.abspath(os.path.join(root_abs, norm))

        exclude_abs_paths.add(full)

    return exclude_abs_paths


def compile_exclude_patterns(pattern_strings: List[str]):
    """
    Возвращает список (regex, is_glob).

    is_glob = True  -> паттерн интерпретируется как glob (через fnmatch.translate).
    is_glob = False -> паттерн интерпретируется как обычный regex.
    """
    patterns = []
    for s in pattern_strings:
        if not s:
            continue
        if any(ch in s for ch in "*?[]"):
            # glob-паттерн
            regex = re.compile(fnmatch.translate(s))
            patterns.append((regex, True))
        else:
            # обычный regex
            regex = re.compile(s)
            patterns.append((regex, False))
    return patterns


def is_excluded_by_patterns(
    full_path: str,
    name: str,
    patterns,
    root_abs: str,
) -> bool:
    # относительный путь относительно корня обхода
    rel_path = os.path.relpath(full_path, root_abs)
    rel_path = rel_path.replace(os.sep, "/")

    for regex, is_glob in patterns:
        if is_glob:
            # glob-паттерн: матчим по basename и по относительному пути целиком
            if regex.fullmatch(name) or regex.fullmatch(rel_path):
                return True
        else:
            # regex: ищем в имени и в относительном пути
            if regex.search(name) or regex.search(rel_path):
                return True
    return False


def should_exclude(
    full_path: str,
    name: str,
    exclude_abs_paths: set,
    exclude_patterns,
    root_abs: str,
) -> bool:
    full_path = os.path.abspath(full_path)

    # Точное совпадение пути для --exclude
    if full_path in exclude_abs_paths:
        return True

    # Паттерны из --exclude-re (независимо от вложенности)
    if exclude_patterns and is_excluded_by_patterns(
        full_path, name, exclude_patterns, root_abs
    ):
        return True

    return False


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
    root = args.directory

    if not os.path.isdir(root):
        print(f"Error: {root} is not a directory", file=sys.stderr)
        sys.exit(1)

    root_abs = os.path.abspath(root)

    exclude_args = flatten(args.exclude) if args.exclude else []
    exclude_re_args = flatten(args.exclude_re) if args.exclude_re else []

    exclude_abs_paths = build_exclude_paths(root_abs, exclude_args)
    exclude_patterns = compile_exclude_patterns(exclude_re_args)

    for dirpath, dirnames, filenames in os.walk(root_abs, topdown=True):
        dirnames.sort()
        filenames.sort()

        # Фильтруем директории (не заходим в исключённые)
        filtered_dirs = []
        for d in dirnames:
            full = os.path.join(dirpath, d)
            if not should_exclude(
                full,
                d,
                exclude_abs_paths,
                exclude_patterns,
                root_abs,
            ):
                filtered_dirs.append(d)
        dirnames[:] = filtered_dirs

        # Файлы
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            if should_exclude(
                full,
                fname,
                exclude_abs_paths,
                exclude_patterns,
                root_abs,
            ):
                continue

            rel_display = os.path.relpath(full, root_abs).replace(os.sep, "/")
            print_file(full, rel_display)


if __name__ == "__main__":
    main()
