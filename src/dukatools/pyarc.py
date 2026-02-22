#!/usr/bin/env python3
"""pyarc: create compressed archives with smart excludes and progress output.

Features:
- Uses system tar for speed and compatibility.
- Supports exact excludes and flexible rules (glob or substring).
- Shows progress bars and throughput via rich.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)


console = Console()
CHUNK_SIZE = 4 * 1024 * 1024  # 4 MiB


# ----------------------------- Utils ----------------------------- #

def check_bin(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Required executable not found in PATH: {name}")


def has_glob_syntax(pattern: str) -> bool:
    return any(ch in pattern for ch in "*?[")


def safe_stat_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except (FileNotFoundError, PermissionError, OSError):
        return 0


def normalize_rel_str(p: Path) -> str:
    # tar likes POSIX-style separators in file lists
    return p.as_posix()


def human_path(p: Path) -> str:
    try:
        return str(p.resolve())
    except Exception:
        return str(p)


# -------------------------- Exclude Logic ------------------------ #

@dataclass
class ExcludeRules:
    exclude_names: set[str]               # exact name match (basename)
    exclude_rules: list[str]              # glob or substring

    def matches(self, rel_path: Path, is_dir: bool) -> bool:
        """
        Exclusion semantics:
        1) --exclude <name1> <name2> ... : exact match by basename OR exact relative path string
        2) --exclude-re <rule1> <rule2> ...:
           - if rule contains glob chars (* ? [) -> fnmatch on basename and full relative path
           - else -> substring match on basename and full relative path
        """
        rel_s = normalize_rel_str(rel_path)
        base = rel_path.name

        # Exact exclusions by name/path
        if base in self.exclude_names or rel_s in self.exclude_names:
            return True

        # Flexible rules
        for rule in self.exclude_rules:
            if has_glob_syntax(rule):
                if fnmatch.fnmatch(base, rule) or fnmatch.fnmatch(rel_s, rule):
                    return True
            else:
                # substring contains
                if rule in base or rule in rel_s:
                    return True

        return False


# -------------------------- File Selection ----------------------- #

@dataclass
class SelectionResult:
    parent: Path                 # tar -C <parent>
    items_for_tar: list[str]     # relative entries to pass to tar via -T list (POSIX-like)
    approx_total_bytes: int      # sum of selected file sizes (for progress estimate)
    files_count: int
    dirs_count: int
    skipped_count: int


def build_selection(src: Path, rules: ExcludeRules) -> SelectionResult:
    """
    Build explicit tar input list.
    We feed tar entries via: tar -cf - -C <parent> --null -T -
    """
    src = src.resolve()
    if not src.exists():
        raise FileNotFoundError(f"Source not found: {src}")

    parent = src.parent
    root_name = src.name

    items: list[str] = []
    total_bytes = 0
    files_count = 0
    dirs_count = 0
    skipped_count = 0

    # Single file case
    if src.is_file():
        rel = Path(root_name)
        if rules.matches(rel, is_dir=False):
            skipped_count += 1
        else:
            items.append(normalize_rel_str(rel))
            total_bytes += safe_stat_size(src)
            files_count += 1
        return SelectionResult(parent, items, total_bytes, files_count, dirs_count, skipped_count)

    # Directory case:
    # We walk and explicitly add directories + files, while pruning excluded dirs early.
    # Add root dir itself unless excluded.
    root_rel = Path(root_name)
    if rules.matches(root_rel, is_dir=True):
        # If root itself excluded -> empty selection
        skipped_count += 1
        return SelectionResult(parent, [], 0, 0, 0, skipped_count)

    items.append(normalize_rel_str(root_rel))
    dirs_count += 1

    for current_root, dirnames, filenames in os.walk(src, topdown=True, followlinks=False):
        current_root_p = Path(current_root)
        current_rel = current_root_p.relative_to(parent)  # includes root_name prefix

        # Prune excluded directories in-place
        pruned_dirs: list[str] = []
        kept_dirs: list[str] = []
        for d in dirnames:
            rel = current_rel / d
            if rules.matches(rel, is_dir=True):
                skipped_count += 1
                pruned_dirs.append(d)
            else:
                kept_dirs.append(d)
                items.append(normalize_rel_str(rel))
                dirs_count += 1
        dirnames[:] = kept_dirs

        # Files
        for fn in filenames:
            rel = current_rel / fn
            if rules.matches(rel, is_dir=False):
                skipped_count += 1
                continue
            fp = current_root_p / fn
            items.append(normalize_rel_str(rel))
            total_bytes += safe_stat_size(fp)
            files_count += 1

    return SelectionResult(parent, items, total_bytes, files_count, dirs_count, skipped_count)


# --------------------------- Progress UI ------------------------- #

def make_progress() -> Progress:
    return Progress(
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=None),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        transient=False,
        console=console,
    )


# ------------------------- Stream Helpers ------------------------ #

def drain_bytes_to_console(pipe, prefix: str) -> None:
    """Drain stderr/stdout bytes to avoid deadlocks."""
    if pipe is None:
        return
    for line in iter(pipe.readline, b""):
        try:
            text = line.decode(errors="replace").rstrip()
            if text:
                console.log(f"[{prefix}] {text}")
        except Exception:
            pass


def tar_verbose_dirs_reader_text(pipe, progress: Progress, task_id: TaskID) -> None:
    """
    Reads `tar -xvf -` stdout (bytes), decodes lines and logs only directories / top-level groups.
    """
    if pipe is None:
        return

    seen_top = set()
    seen_dirs = set()

    for raw_line in iter(pipe.readline, b""):
        try:
            entry = raw_line.decode(errors="replace").strip()
        except Exception:
            continue

        if not entry:
            continue

        # tar verbose prints extracted paths. We suppress file spam.
        # Show:
        # - explicit directories (ending with '/')
        # - top-level components once
        is_dir = entry.endswith("/")
        normalized = entry.rstrip("/")
        if not normalized:
            continue

        parts = normalized.split("/")
        top = parts[0]
        top_key = top + "/"

        if top_key not in seen_top:
            seen_top.add(top_key)
            console.log(f"[unpack] entering [bold]{top_key}[/bold]")

        if is_dir:
            dir_key = normalized + "/"
            if dir_key not in seen_dirs:
                seen_dirs.add(dir_key)
                if dir_key != top_key:
                    console.log(f"[unpack] dir {dir_key}")
                    

def send_list_to_stdin_null(proc_stdin, items: Iterable[str]) -> None:
    """
    Send NUL-separated tar file list for `tar --null -T -`.
    """
    try:
        for item in items:
            proc_stdin.write(item.encode("utf-8", errors="surrogateescape"))
            proc_stdin.write(b"\0")
        proc_stdin.flush()
    finally:
        try:
            proc_stdin.close()
        except Exception:
            pass


# ----------------------------- PACK ------------------------------ #

def pack(
    src: Path,
    archive: Path,
    level: int,
    threads: int,
    exclude: list[str],
    exclude_re: list[str],
    show_selection_summary: bool = True,
) -> None:
    check_bin("tar")
    check_bin("pigz")

    src = src.resolve()
    archive = archive.resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)

    rules = ExcludeRules(set(exclude), list(exclude_re))
    sel = build_selection(src, rules)

    console.rule("[bold green]PACK")
    console.print(f"[bold]Source:[/bold] {human_path(src)}")
    console.print(f"[bold]Archive:[/bold] {human_path(archive)}")
    console.print(f"[bold]Mode:[/bold] tar | pigz  (gzip-compatible .tar.gz)")
    console.print(f"[bold]Compression level:[/bold] {level}")
    console.print(f"[bold]pigz threads:[/bold] {threads}")

    if show_selection_summary:
        console.print(
            f"[bold]Selected:[/bold] files={sel.files_count}, dirs={sel.dirs_count}, skipped={sel.skipped_count}, "
            f"size≈{sel.approx_total_bytes} B"
        )

    if not sel.items_for_tar:
        raise RuntimeError("Nothing to archive after applying excludes.")

    # tar reads explicit entries from stdin file list
    tar_cmd = ["tar", "-cf", "-", "-C", str(sel.parent), "--null", "-T", "-"]
    pigz_cmd = ["pigz", f"-p{threads}", f"-{level}"]

    tar_p = subprocess.Popen(
        tar_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    pigz_p = subprocess.Popen(
        pigz_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    # Send file list to tar stdin in background
    list_thread = threading.Thread(
        target=send_list_to_stdin_null,
        args=(tar_p.stdin, sel.items_for_tar),
        daemon=True,
    )
    list_thread.start()

    # Drain stderrs
    tar_err_thread = threading.Thread(target=drain_bytes_to_console, args=(tar_p.stderr, "tar"), daemon=True)
    pigz_err_thread = threading.Thread(target=drain_bytes_to_console, args=(pigz_p.stderr, "pigz"), daemon=True)
    tar_err_thread.start()
    pigz_err_thread.start()

    # Thread: pigz stdout -> archive file
    out_f = open(archive, "wb")

    def pigz_to_file() -> None:
        try:
            while True:
                chunk = pigz_p.stdout.read(CHUNK_SIZE)
                if not chunk:
                    break
                out_f.write(chunk)
            out_f.flush()
        finally:
            try:
                out_f.close()
            except Exception:
                pass
            try:
                pigz_p.stdout.close()
            except Exception:
                pass

    writer_thread = threading.Thread(target=pigz_to_file, daemon=True)
    writer_thread.start()

    total_est = max(sel.approx_total_bytes, 1)

    with make_progress() as progress:
        task = progress.add_task("[pack] tar->pigz", total=total_est)
        done = 0
        try:
            while True:
                chunk = tar_p.stdout.read(CHUNK_SIZE)
                if not chunk:
                    break
                pigz_p.stdin.write(chunk)
                done += len(chunk)
                # tar stream can exceed sum(file sizes) due to headers/padding
                if done > progress.tasks[task].total:
                    progress.update(task, total=done)
                progress.update(task, completed=done)
            pigz_p.stdin.close()
        except KeyboardInterrupt:
            for p in (tar_p, pigz_p):
                try:
                    p.send_signal(signal.SIGINT)
                except Exception:
                    pass
            raise
        finally:
            try:
                tar_p.stdout.close()
            except Exception:
                pass

        writer_thread.join()
        list_thread.join()

        tar_rc = tar_p.wait()
        pigz_rc = pigz_p.wait()

        # ensure full bar
        final_total = max(progress.tasks[task].total or 0, done)
        progress.update(task, total=final_total, completed=done)

    if tar_rc != 0:
        raise RuntimeError(f"tar failed with exit code {tar_rc}")
    if pigz_rc != 0:
        raise RuntimeError(f"pigz failed with exit code {pigz_rc}")

    out_size = archive.stat().st_size if archive.exists() else 0
    ratio = (out_size / sel.approx_total_bytes) if sel.approx_total_bytes else 0.0
    console.print(f"[bold green]Done.[/bold green] {human_path(archive)} ({out_size} B, ratio≈{ratio:.3f})")


# ---------------------------- UNPACK ----------------------------- #

def unpack(archive: Path, dst: Path, threads: int) -> None:
    check_bin("tar")
    check_bin("pigz")

    archive = archive.resolve()
    dst = dst.resolve()
    if not archive.exists():
        raise FileNotFoundError(f"Archive not found: {archive}")
    dst.mkdir(parents=True, exist_ok=True)

    total_in = archive.stat().st_size

    console.rule("[bold magenta]UNPACK")
    console.print(f"[bold]Archive:[/bold] {human_path(archive)}")
    console.print(f"[bold]Target dir:[/bold] {human_path(dst)}")
    console.print(f"[bold]Mode:[/bold] pigz -dc | tar -xvf -")
    console.print(f"[bold]pigz threads:[/bold] {threads}")
    console.print(f"[bold]Compressed size:[/bold] {total_in} B")

    # We push compressed archive bytes into pigz stdin (so progress = exact % of archive read)
    pigz_cmd = ["pigz", f"-p{threads}", "-dc"]
    tar_cmd = ["tar", "-xvf", "-", "-C", str(dst)]

    pigz_p = subprocess.Popen(
        pigz_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    tar_p = subprocess.Popen(
        tar_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,   # verbose extracted entries (bytes; decode in reader thread)
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    # Drain non-verbose stderr
    pigz_err_thread = threading.Thread(target=drain_bytes_to_console, args=(pigz_p.stderr, "pigz"), daemon=True)
    tar_err_thread = threading.Thread(target=drain_bytes_to_console, args=(tar_p.stderr, "tar"), daemon=True)
    pigz_err_thread.start()
    tar_err_thread.start()

    with make_progress() as progress:
        task = progress.add_task("[unpack] archive->pigz->tar", total=max(total_in, 1))

        # Thread: parse tar verbose output and print only dirs/top groups
        tar_out_thread = threading.Thread(
            target=tar_verbose_dirs_reader_text,
            args=(tar_p.stdout, progress, task),
            daemon=True,
        )
        tar_out_thread.start()

        # Thread: pigz stdout (decompressed tar stream) -> tar stdin
        def decompressed_pipe() -> None:
            try:
                while True:
                    chunk = pigz_p.stdout.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    try:
                        tar_p.stdin.write(chunk)
                    except BrokenPipeError:
                        break
                try:
                    tar_p.stdin.close()
                except (BrokenPipeError, OSError, ValueError):
                    pass
            except Exception as e:
                console.log(f"[decompressed_pipe] {type(e).__name__}: {e}")
            finally:
                try:
                    pigz_p.stdout.close()
                except Exception:
                    pass

        pipe_thread = threading.Thread(target=decompressed_pipe, daemon=True)
        pipe_thread.start()

        done = 0
        try:
            with open(archive, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    try:
                        pigz_p.stdin.write(chunk)
                    except BrokenPipeError:
                        # pigz завершился раньше — дочитывать архив дальше нет смысла
                        break
                    done += len(chunk)
                    progress.update(task, completed=done)
            try:
                pigz_p.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass
        except KeyboardInterrupt:
            for p in (pigz_p, tar_p):
                try:
                    p.send_signal(signal.SIGINT)
                except Exception:
                    pass
            raise

        pipe_thread.join()
        pigz_rc = pigz_p.wait()
        tar_rc = tar_p.wait()
        tar_out_thread.join(timeout=0.2)

        progress.update(task, completed=done)

    if pigz_rc != 0:
        raise RuntimeError(f"pigz failed with exit code {pigz_rc}")
    if tar_rc != 0:
        raise RuntimeError(f"tar failed with exit code {tar_rc}")

    console.print(f"[bold green]Done.[/bold green] Extracted to {human_path(dst)}")


# ------------------------------ CLI ------------------------------ #

def build_parser() -> argparse.ArgumentParser:
    examples = """
Examples:
  pyarc pack ./mydir ./backup.tar.gz
  pyarc pack ./mydir ./backup.tar.gz --level 4 --threads 16
  pyarc pack ./mydir ./backup.tar.gz --exclude .git __pycache__ node_modules
  pyarc pack ./mydir ./backup.tar.gz --exclude-re git *.txt
  pyarc unpack ./backup.tar.gz ./restore_here
  pyarc unpack ./backup.tar.gz ./restore_here --threads 8

Exclude semantics:
  --exclude ...     exact name/path match (basename OR relative path)
                    Example: --exclude .git __pycache__ file.tmp
  --exclude-re ...  flexible rules (each token is either glob OR substring)
                    If token contains * ? [ -> glob
                    otherwise -> substring containment
                    Example: --exclude-re git *.txt build/
                    Means: exclude anything containing "git", all .txt files, and paths matching build/

Performance note:
  Python here does NOT compress files itself.
  It orchestrates external tools: tar + pigz via subprocess, so speed stays close to shell pipeline.
""".strip("\n")

    p = argparse.ArgumentParser(
        prog="pyarc",
        description=(
            "Fast tar.gz pack/unpack wrapper with rich progress UI (ETA, speed, bytes) using tar + pigz.\n"
            "Designed as a convenient operator tool around subprocess pipelines."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=examples,
    )

    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    # PACK
    p_pack = sub.add_parser(
        "pack",
        help="Create a .tar.gz archive from a file or directory.",
        description=(
            "Create a gzip-compressed tar archive (.tar.gz) using external tools tar + pigz.\n"
            "Progress is shown using rich (progress bar, speed, ETA).\n"
            "Exclusions are applied before archiving."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    p_pack.add_argument(
        "src",
        type=Path,
        help=(
            "Source path to archive.\n"
            "Can be either a single file or a directory.\n"
            "If it is a directory, the directory itself is included as the top-level folder in the archive."
        ),
    )
    p_pack.add_argument(
        "archive",
        type=Path,
        help=(
            "Output archive path (usually *.tar.gz).\n"
            "Parent directories are created automatically if needed."
        ),
    )
    p_pack.add_argument(
        "-l", "--level",
        type=int,
        default=6,
        choices=range(1, 10),
        metavar="1..9",
        help=(
            "pigz compression level.\n"
            "1 = fastest / larger archive\n"
            "6 = default balanced mode (recommended)\n"
            "9 = slowest / smaller archive"
        ),
    )
    p_pack.add_argument(
        "-p", "--threads",
        type=int,
        default=os.cpu_count() or 1,
        metavar="N",
        help=(
            "Number of pigz threads (parallel gzip compression).\n"
            "Default: number of CPU cores.\n"
            "Increase for faster compression on multi-core systems."
        ),
    )
    p_pack.add_argument(
        "--exclude",
        nargs="+",
        default=[],
        metavar="NAME",
        help=(
            "Exact exclude list (space-separated).\n"
            "Each value is treated as an exact match by basename OR relative path.\n"
            "Examples:\n"
            "  --exclude .git __pycache__ node_modules file.tmp\n"
            "  --exclude logs/debug.log"
        ),
    )
    p_pack.add_argument(
        "--exclude-re",
        nargs="+",
        default=[],
        metavar="RULE",
        help=(
            "Flexible exclude rules (space-separated).\n"
            "Each rule is interpreted as:\n"
            "  - glob, if it contains wildcard chars: * ? [\n"
            "  - substring, otherwise (\"contains\" match)\n"
            "Examples:\n"
            "  --exclude-re git *.txt\n"
            "    -> excludes any path/name containing 'git' and all .txt files\n"
            "  --exclude-re cache temp *.log build/*"
        ),
    )

    # UNPACK
    p_unpack = sub.add_parser(
        "unpack",
        help="Extract a .tar.gz archive into a target directory.",
        description=(
            "Extract a gzip-compressed tar archive (.tar.gz) using external tools pigz + tar.\n"
            "Progress is exact over compressed archive bytes.\n"
            "During extraction, output is compact: directory/top-level path updates instead of every file."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p_unpack.add_argument(
        "archive",
        type=Path,
        help=(
            "Input archive path (*.tar.gz).\n"
            "Must exist."
        ),
    )
    p_unpack.add_argument(
        "dst",
        type=Path,
        help=(
            "Destination directory for extraction.\n"
            "Created automatically if it does not exist."
        ),
    )
    p_unpack.add_argument(
        "-p", "--threads",
        type=int,
        default=os.cpu_count() or 1,
        metavar="N",
        help=(
            "Number of pigz threads for decompression.\n"
            "pigz can parallelize decompression setup and throughput handling.\n"
            "Default: number of CPU cores."
        ),
    )

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.cmd == "pack":
            pack(
                src=args.src,
                archive=args.archive,
                level=args.level,
                threads=args.threads,
                exclude=args.exclude,
                exclude_re=args.exclude_re,
            )
        elif args.cmd == "unpack":
            unpack(
                archive=args.archive,
                dst=args.dst,
                threads=args.threads,
            )
        else:
            parser.error("Unknown command")
        return 0

    except KeyboardInterrupt:
        console.print("[bold yellow]Interrupted.[/bold yellow]")
        return 130
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
