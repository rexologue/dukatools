from __future__ import annotations
import os
import re
from pathlib import Path
from argparse import ArgumentParser, Namespace
from typing import Iterable, List, Set, Optional


def detect_encoding(file_path: str) -> str:
    try:
        import chardet
    except ModuleNotFoundError:
        print("chardet is necessary. Make an uodate: uv tool upgrade dukatools", flush=True)
        raise

    with open(file_path, "rb") as file:
        raw_data = file.read()
        result = chardet.detect(raw_data)
        return result.get("encoding") or "latin1"


def compile_patterns(patterns: Iterable[str]) -> List[re.Pattern]:
    compiled = []
    for pat in patterns:
        try:
            compiled.append(re.compile(pat))
        except re.error as e:
            raise SystemExit(f"Invalid regex in --exclude-pattern: {pat!r} -> {e}")
    return compiled


def should_exclude(path: Path, rel_posix: str, exclude_names: Set[str], exclude_regex: List[re.Pattern]) -> bool:
    # Точное совпадение имени (базовое имя, без пути)
    if path.name in exclude_names:
        return True

    # Соответствие любому из regex по относительному POSIX-пути
    for rx in exclude_regex:
        if rx.search(rel_posix):
            return True

    return False


def process_directory(
    root_dir: Path,
    current_rel: Path,
    out_fh,  # текстовый файл или None (stdout)
    recursive: bool,
    exclude_names: Set[str],
    exclude_regex: List[re.Pattern],
) -> None:
    base = root_dir / current_rel
    try:
        entries = list(os.scandir(base))
    except FileNotFoundError:
        msg = f"Path not found: {base}"
        if out_fh:
            print(msg, file=out_fh)
        else:
            print(msg)
        return

    for entry in entries:
        p = Path(entry.path)
        rel = current_rel / entry.name
        rel_posix = rel.as_posix()

        if should_exclude(p, rel_posix, exclude_names, exclude_regex):
            # Если это директория — не заходим внутрь
            continue

        if entry.is_dir(follow_symlinks=False):
            if recursive:
                process_directory(root_dir, rel, out_fh, recursive, exclude_names, exclude_regex)
        elif entry.is_file(follow_symlinks=False):
            result = f"File: {rel_posix}\n\n"
            try:
                encoding = detect_encoding(str(p))
                with open(p, "r", encoding=encoding, errors="replace") as f:
                    content = f.read()
                result += f"Content:\n{content}\n\n"
            except Exception as e:
                result += f"Error reading file {rel_posix}: {e}\n\n"

            if out_fh:
                out_fh.write(result)
            else:
                print(result)


def main() -> None:
    parser = ArgumentParser(description="Dump files of a directory; optionally save to a file. No auto-excludes.")
    parser.add_argument("root_dir", type=str, help="Root directory to process.")
    parser.add_argument("--output-file", type=str, default=None, help="Where to save output (UTF-8).")
    parser.add_argument("-nR", "--non-recursive", action="store_true", help="Disable recursion.")
    parser.add_argument(
        "--exclude-name",
        action="append",
        default=[],
        help="Exclude by exact base name (file or directory). Can be repeated.",
    )
    parser.add_argument(
        "--exclude-pattern",
        action="append",
        default=[],
        help="Exclude by regex applied to relative POSIX path. Can be repeated.",
    )

    args: Namespace = parser.parse_args()

    root_dir = Path(args.root_dir).resolve()

    # Собрать список паттернов (append -> может быть списком списков, нормализуем)
    # Здесь action='append', так что это уже плоский список или None -> []
    exclude_names: Set[str] = set(args.exclude_name or [])
    exclude_regex: List[re.Pattern] = compile_patterns(args.exclude_pattern or [])

    out_fh = None
    try:
        if args.output_file:
            out_path = Path(args.output_file)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_fh = open(out_path, "w", encoding="utf-8")

        process_directory(
            root_dir=root_dir,
            current_rel=Path("."),
            out_fh=out_fh,
            recursive=not args.non_recursive,
            exclude_names=exclude_names,
            exclude_regex=exclude_regex,
        )
    finally:
        if out_fh:
            out_fh.close()


if __name__ == "__main__":
    main()
