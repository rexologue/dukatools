#!/usr/bin/env python3
"""arc: create and extract .tar.gz archives with filtered file selection."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import signal
import subprocess
import tarfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

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

from dukatools.pathfilter import PathFilter, flatten_groups


console = Console()
err_console = Console(stderr=True)
CHUNK_SIZE = 4 * 1024 * 1024


def check_bin(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Required executable not found in PATH: {name}")


def system_backend_available() -> bool:
    return shutil.which("tar") is not None and shutil.which("pigz") is not None


def install_hint() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macOS: brew install pigz"
    if system == "linux":
        return "\n".join(
            [
                "Ubuntu/Debian: sudo apt install pigz",
                "Fedora: sudo dnf install pigz",
                "Arch: sudo pacman -S pigz",
                "Alpine: sudo apk add pigz",
            ]
        )
    if system == "windows":
        return "Windows: install pigz via MSYS2/Chocolatey or use the Python fallback."
    return "Install pigz with your system package manager."


def warn_if_pigz_missing() -> None:
    if shutil.which("pigz") is not None:
        return
    err_console.print()
    err_console.rule("[bold yellow]WARNING: pigz not found")
    err_console.print(
        "[bold yellow]duka arc is using the built-in Python gzip fallback.[/bold yellow]\n"
        "It works after `uv tool install dukatools`, but compression and extraction can be slower.\n"
        "For best performance install pigz:\n"
        f"{install_hint()}"
    )
    err_console.rule()


def resolve_backend(requested: str) -> str:
    if requested == "python":
        return "python"
    if requested == "system":
        check_bin("tar")
        check_bin("pigz")
        return "system"
    if system_backend_available():
        return "system"
    return "python"


def safe_stat_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except (FileNotFoundError, PermissionError, OSError):
        return 0


def normalize_rel_str(p: Path) -> str:
    return p.as_posix()


def human_path(p: Path) -> str:
    try:
        return str(p.resolve())
    except Exception:
        return str(p)


def arc_rel(src: Path, path: Path) -> Path:
    if path == src:
        return Path(src.name)
    return Path(src.name) / path.relative_to(src)


@dataclass
class SelectionResult:
    parent: Path
    items_for_tar: list[str]
    approx_total_bytes: int
    files_count: int
    dirs_count: int
    skipped_count: int


def build_selection(src: Path, rules: PathFilter) -> SelectionResult:
    src = src.resolve()
    if not src.exists():
        raise FileNotFoundError(f"Source not found: {src}")

    parent = src.parent
    selected_files: list[Path] = []
    selected_dirs: set[Path] = set()
    total_bytes = 0
    files_count = 0
    skipped_count = 0

    if src.is_file():
        if rules.selects(src):
            rel = Path(src.name)
            return SelectionResult(parent, [normalize_rel_str(rel)], safe_stat_size(src), 1, 0, 0)
        return SelectionResult(parent, [], 0, 0, 0, 1)

    if rules.is_excluded(src):
        return SelectionResult(parent, [], 0, 0, 0, 1)

    for current_root, dirnames, filenames in os.walk(src, topdown=True, followlinks=False):
        current_root_p = Path(current_root)
        dirnames.sort()
        filenames.sort()

        kept_dirs: list[str] = []
        for d in dirnames:
            dp = current_root_p / d
            if rules.is_excluded(dp):
                skipped_count += 1
                continue
            kept_dirs.append(d)
        dirnames[:] = kept_dirs

        if current_root_p != src and rules.selects(current_root_p):
            selected_dirs.add(arc_rel(src, current_root_p))

        for fn in filenames:
            fp = current_root_p / fn
            if not rules.selects(fp):
                skipped_count += 1
                continue
            rel = arc_rel(src, fp)
            selected_files.append(rel)
            total_bytes += safe_stat_size(fp)
            files_count += 1

    root_rel = Path(src.name)
    for rel in selected_files:
        parent_rel = rel.parent
        while str(parent_rel) not in ("", "."):
            if parent_rel != root_rel:
                selected_dirs.add(parent_rel)
            parent_rel = parent_rel.parent

    items = [src.name]
    items.extend(normalize_rel_str(p) for p in sorted(selected_dirs, key=lambda x: x.as_posix()))
    items.extend(normalize_rel_str(p) for p in selected_files)

    if len(items) == 1 and not selected_dirs and not selected_files:
        items = []

    dirs_count = len(selected_dirs) + (1 if items else 0)
    return SelectionResult(parent, items, total_bytes, files_count, dirs_count, skipped_count)


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


def drain_bytes_to_console(pipe, prefix: str) -> None:
    if pipe is None:
        return
    try:
        for line in iter(pipe.readline, b""):
            try:
                text = line.decode(errors="replace").rstrip()
                if text:
                    console.log(f"[{prefix}] {text}")
            except Exception:
                pass
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def tar_verbose_dirs_reader_text(pipe, progress: Progress, task_id: TaskID) -> None:
    if pipe is None:
        return

    seen_top = set()
    seen_dirs = set()

    try:
        for raw_line in iter(pipe.readline, b""):
            try:
                entry = raw_line.decode(errors="replace").strip()
            except Exception:
                continue
            if not entry:
                continue

            is_dir = entry.endswith("/")
            normalized = entry.rstrip("/")
            if not normalized:
                continue

            top = normalized.split("/")[0]
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
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def send_list_to_stdin_null(proc_stdin, items: Iterable[str]) -> None:
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


def print_pack_summary(src: Path, archive: Path, level: int, threads: int, backend: str, sel: SelectionResult) -> None:
    console.rule("[bold green]PACK")
    console.print(f"[bold]Source:[/bold] {human_path(src)}")
    console.print(f"[bold]Archive:[/bold] {human_path(archive)}")
    console.print(f"[bold]Backend:[/bold] {backend}")
    console.print(f"[bold]Compression level:[/bold] {level}")
    if backend == "system":
        console.print(f"[bold]pigz threads:[/bold] {threads}")
    console.print(
        f"[bold]Selected:[/bold] files={sel.files_count}, dirs={sel.dirs_count}, "
        f"skipped={sel.skipped_count}, size~={sel.approx_total_bytes} B"
    )


def pack_system(src: Path, archive: Path, level: int, threads: int, sel: SelectionResult) -> None:
    tar_cmd = ["tar", "--no-recursion", "-cf", "-", "-C", str(sel.parent), "--null", "-T", "-"]
    pigz_cmd = ["pigz", f"-p{threads}", f"-{level}"]

    tar_p = subprocess.Popen(tar_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    pigz_p = subprocess.Popen(pigz_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)

    list_thread = threading.Thread(target=send_list_to_stdin_null, args=(tar_p.stdin, sel.items_for_tar), daemon=True)
    list_thread.start()

    tar_err_thread = threading.Thread(target=drain_bytes_to_console, args=(tar_p.stderr, "tar"), daemon=True)
    pigz_err_thread = threading.Thread(target=drain_bytes_to_console, args=(pigz_p.stderr, "pigz"), daemon=True)
    tar_err_thread.start()
    pigz_err_thread.start()

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
            out_f.close()
            pigz_p.stdout.close()

    writer_thread = threading.Thread(target=pigz_to_file, daemon=True)
    writer_thread.start()

    with make_progress() as progress:
        task = progress.add_task("[pack] tar->pigz", total=max(sel.approx_total_bytes, 1))
        done = 0
        try:
            while True:
                chunk = tar_p.stdout.read(CHUNK_SIZE)
                if not chunk:
                    break
                pigz_p.stdin.write(chunk)
                done += len(chunk)
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
            tar_p.stdout.close()

        writer_thread.join()
        list_thread.join()

        tar_rc = tar_p.wait()
        pigz_rc = pigz_p.wait()
        final_total = max(progress.tasks[task].total or 0, done)
        progress.update(task, total=final_total, completed=done)

    if tar_rc != 0:
        raise RuntimeError(f"tar failed with exit code {tar_rc}")
    if pigz_rc != 0:
        raise RuntimeError(f"pigz failed with exit code {pigz_rc}")


def pack_python(archive: Path, level: int, sel: SelectionResult) -> None:
    with make_progress() as progress:
        task = progress.add_task("[pack] python gzip", total=max(sel.approx_total_bytes, 1))
        done = 0
        with tarfile.open(archive, "w:gz", compresslevel=level) as tf:
            for item in sel.items_for_tar:
                source = sel.parent / item
                tf.add(source, arcname=item, recursive=False)
                if source.is_file():
                    done += safe_stat_size(source)
                    progress.update(task, completed=done)
        progress.update(task, completed=max(done, progress.tasks[task].total or done))


def pack(
    src: Path,
    archive: Path,
    level: int,
    threads: int,
    include: list[str],
    include_re: list[str],
    exclude: list[str],
    exclude_re: list[str],
    backend: str,
) -> None:
    src = src.expanduser().resolve()
    archive = archive.expanduser().resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)

    filter_root = src if src.is_dir() else src.parent
    rules = PathFilter.build(
        filter_root,
        include_paths=include,
        exclude_paths=exclude,
        include_patterns=include_re,
        exclude_patterns=exclude_re,
    )
    sel = build_selection(src, rules)
    selected_backend = resolve_backend(backend)

    print_pack_summary(src, archive, level, threads, selected_backend, sel)
    if not sel.items_for_tar:
        raise RuntimeError("Nothing to archive after applying filters.")

    if selected_backend == "system":
        pack_system(src, archive, level, threads, sel)
    else:
        pack_python(archive, level, sel)

    out_size = archive.stat().st_size if archive.exists() else 0
    ratio = (out_size / sel.approx_total_bytes) if sel.approx_total_bytes else 0.0
    console.print(f"[bold green]Done.[/bold green] {human_path(archive)} ({out_size} B, ratio~={ratio:.3f})")


def unpack_system(archive: Path, dst: Path, threads: int) -> None:
    total_in = archive.stat().st_size
    console.rule("[bold magenta]UNPACK")
    console.print(f"[bold]Archive:[/bold] {human_path(archive)}")
    console.print(f"[bold]Target dir:[/bold] {human_path(dst)}")
    console.print(f"[bold]Backend:[/bold] system")
    console.print(f"[bold]Mode:[/bold] pigz -dc | tar -xvf -")
    console.print(f"[bold]pigz threads:[/bold] {threads}")
    console.print(f"[bold]Compressed size:[/bold] {total_in} B")

    pigz_p = subprocess.Popen(["pigz", f"-p{threads}", "-dc"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    tar_p = subprocess.Popen(["tar", "-xvf", "-", "-C", str(dst)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)

    threading.Thread(target=drain_bytes_to_console, args=(pigz_p.stderr, "pigz"), daemon=True).start()
    threading.Thread(target=drain_bytes_to_console, args=(tar_p.stderr, "tar"), daemon=True).start()

    with make_progress() as progress:
        task = progress.add_task("[unpack] archive->pigz->tar", total=max(total_in, 1))
        tar_out_thread = threading.Thread(target=tar_verbose_dirs_reader_text, args=(tar_p.stdout, progress, task), daemon=True)
        tar_out_thread.start()

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
            finally:
                pigz_p.stdout.close()

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


def _safe_member_target(dst: Path, member_name: str) -> Path:
    target = (dst / member_name).resolve()
    try:
        target.relative_to(dst)
    except ValueError:
        raise RuntimeError(f"Unsafe archive member path: {member_name}")
    return target


def unpack_python(archive: Path, dst: Path) -> None:
    console.rule("[bold magenta]UNPACK")
    console.print(f"[bold]Archive:[/bold] {human_path(archive)}")
    console.print(f"[bold]Target dir:[/bold] {human_path(dst)}")
    console.print(f"[bold]Backend:[/bold] python")

    with tarfile.open(archive, "r:gz") as tf:
        members = tf.getmembers()
        total = sum(m.size for m in members if m.isfile())
        with make_progress() as progress:
            task = progress.add_task("[unpack] python gzip", total=max(total, 1))
            done = 0
            for member in members:
                _safe_member_target(dst, member.name)
                tf.extract(member, dst)
                if member.isfile():
                    done += member.size
                    progress.update(task, completed=done)
            progress.update(task, completed=max(done, progress.tasks[task].total or done))


def unpack(archive: Path, dst: Path, threads: int, backend: str) -> None:
    archive = archive.expanduser().resolve()
    dst = dst.expanduser().resolve()
    if not archive.exists():
        raise FileNotFoundError(f"Archive not found: {archive}")
    dst.mkdir(parents=True, exist_ok=True)

    selected_backend = resolve_backend(backend)
    if selected_backend == "system":
        unpack_system(archive, dst, threads)
    else:
        unpack_python(archive, dst)
    console.print(f"[bold green]Done.[/bold green] Extracted to {human_path(dst)}")


def doctor() -> None:
    tar_path = shutil.which("tar")
    pigz_path = shutil.which("pigz")
    backend = "system" if tar_path and pigz_path else "python"
    console.rule("[bold cyan]ARC DOCTOR")
    console.print(f"[bold]tar:[/bold] {tar_path or 'not found'}")
    console.print(f"[bold]pigz:[/bold] {pigz_path or 'not found'}")
    console.print(f"[bold]Default backend:[/bold] {backend}")
    if not pigz_path:
        console.print()
        console.print("[bold yellow]pigz is missing, so arc will warn and use Python gzip fallback.[/bold yellow]")
        console.print("Install pigz for best performance:")
        console.print(install_hint())


def add_backend_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--backend",
        choices=("auto", "system", "python"),
        default="auto",
        help=(
            "Archive backend. auto uses tar+pigz when available and Python gzip otherwise; "
            "system requires tar+pigz; python never requires external binaries."
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    examples = """
Examples:
  duka arc pack ./mydir ./backup.tar.gz
  duka arc pack ./mydir ./backup.tar.gz --level 4 --threads 16
  duka arc pack ./mydir ./backup.tar.gz --exclude .git build
  duka arc pack ./mydir ./backup.tar.gz --include src pyproject.toml --exclude-re "*.pyc"
  duka arc unpack ./backup.tar.gz ./restore_here
  duka arc doctor

Filter rules for pack:
  --exclude PATH       exact path to remove; relative paths are resolved from SRC
  --include PATH       exact path to keep; relative paths are resolved from SRC
  --exclude-re GLOB    glob-style POSIX path pattern to remove
  --include-re GLOB    glob-style POSIX path pattern to keep

If any include rule is present, only matching paths are archived. Exclude rules
win over include rules. For recursive name containment, write the wildcard:
  duka arc pack ./project ./project.tar.gz --exclude-re "*__pycache__*"

Performance:
  The default backend is auto. If tar+pigz are available, arc uses the fast
  system pipeline. Otherwise it prints a warning and uses Python gzip fallback,
  so `uv tool install dukatools` is enough for a working archive tool.
""".strip()

    p = argparse.ArgumentParser(
        prog="duka arc",
        description="Pack and unpack .tar.gz archives with include/exclude filters and progress output.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=examples,
    )
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    p_pack = sub.add_parser(
        "pack",
        help="Create a .tar.gz archive from a file or directory.",
        description=(
            "Create a gzip-compressed tar archive. Relative filter paths are resolved from SRC. "
            "The source directory itself is kept as the top-level archive folder."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_pack.add_argument("src", type=Path, help="Source file or directory to archive.")
    p_pack.add_argument("archive", type=Path, help="Output archive path, usually *.tar.gz.")
    p_pack.add_argument("-l", "--level", type=int, default=6, choices=range(1, 10), metavar="1..9", help="Gzip compression level (default: 6).")
    p_pack.add_argument("-p", "--threads", type=int, default=os.cpu_count() or 1, metavar="N", help="pigz thread count for the system backend.")
    p_pack.add_argument("--exclude", nargs="+", action="append", default=[], metavar="PATH", help="Exact file or directory path to exclude, relative to SRC unless absolute.")
    p_pack.add_argument("--include", nargs="+", action="append", default=[], metavar="PATH", help="Exact file or directory path to include, relative to SRC unless absolute.")
    p_pack.add_argument("--exclude-re", nargs="+", action="append", default=[], metavar="GLOB", help="Glob-style POSIX path pattern to exclude, e.g. '*.pyc' or '*__pycache__*'.")
    p_pack.add_argument("--include-re", nargs="+", action="append", default=[], metavar="GLOB", help="Glob-style POSIX path pattern to include, e.g. 'src/*.py'.")
    p_pack.add_argument("--icnlude-re", nargs="+", action="append", dest="include_re", help=argparse.SUPPRESS)
    add_backend_arg(p_pack)

    p_unpack = sub.add_parser(
        "unpack",
        help="Extract a .tar.gz archive into a target directory.",
        description="Extract a gzip-compressed tar archive using tar+pigz when available or Python gzip fallback.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_unpack.add_argument("archive", type=Path, help="Input archive path (*.tar.gz).")
    p_unpack.add_argument("dst", type=Path, help="Destination directory, created if needed.")
    p_unpack.add_argument("-p", "--threads", type=int, default=os.cpu_count() or 1, metavar="N", help="pigz thread count for the system backend.")
    add_backend_arg(p_unpack)

    sub.add_parser("doctor", help="Show backend availability and pigz installation hints.")
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.cmd != "doctor":
            warn_if_pigz_missing()

        if args.cmd == "pack":
            pack(
                src=args.src,
                archive=args.archive,
                level=args.level,
                threads=args.threads,
                include=flatten_groups(args.include),
                include_re=flatten_groups(args.include_re),
                exclude=flatten_groups(args.exclude),
                exclude_re=flatten_groups(args.exclude_re),
                backend=args.backend,
            )
        elif args.cmd == "unpack":
            unpack(archive=args.archive, dst=args.dst, threads=args.threads, backend=args.backend)
        elif args.cmd == "doctor":
            doctor()
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
