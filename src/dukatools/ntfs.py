#!/usr/bin/env python3
"""ntfs: read/write NTFS drives on macOS via FUSE-T + ntfs-3g (no kernel extension).

macOS mounts NTFS read-only. This tool automates the manual recipe documented in
docs/ntfs-macos.md (https://github.com/rexologue/dukatools/blob/main/docs/ntfs-macos.md):

  doctor   - check FUSE-T, build tools, installed ntfs-3g and connected NTFS partitions
  setup    - download ntfs-3g (pinned version + SHA-256), build it against FUSE-T,
             install ntfs-3g and ntfsfix into /usr/local/bin (asks for sudo once)
  list     - show external NTFS partitions and how they are mounted
  mount    - unmount the read-only macOS mount, check the volume, mount it read/write
  unmount  - cleanly unmount an ntfs-3g mount (optionally eject the disk)

duka itself never runs as root. Privileged steps are separate `sudo` calls to
root-owned binaries in /usr/local/bin, and every such command is printed first.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import plistlib
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from rich.console import Console
from rich.table import Table

console = Console()

NTFS3G_VERSION = "2026.9.28"
NTFS3G_SHA256 = "350d9415c59f7c3fa74e23985ad2d56423c6a168337368f672e2e362d8f295c8"
NTFS3G_URL = f"https://tuxera.com/opensource/ntfs-3g_ntfsprogs-{NTFS3G_VERSION}.tgz"

INSTALL_DIR = Path("/usr/local/bin")
NTFS3G_BIN = INSTALL_DIR / "ntfs-3g"
NTFSFIX_BIN = INSTALL_DIR / "ntfsfix"

FUSE_T_LIB = Path("/usr/local/lib/libfuse-t.dylib")
FUSE_T_PKGCONFIG_DIR = Path("/usr/local/lib/pkgconfig")
# The NFS/SMB/FSKit servers live here. `brew uninstall --cask fuse-t` removes this
# directory but leaves the library and headers in /usr/local behind, so the library
# alone does not mean FUSE-T works.
FUSE_T_SUPPORT_DIR = Path("/Library/Application Support/fuse-t")
FUSE_T_INSTALL_CMD = "brew install --cask macos-fuse-t/cask/fuse-t"

# Mount options verified on a real drive (see docs/ntfs-macos.md):
#  - uid/gid/umask: files belong to the current user, otherwise Finder cannot write
#  - windows_names: refuse names Windows cannot open (: ? * trailing dot, ...)
#  - hide_dot_files: FUSE-T's NFS backend stores macOS xattrs as ._ AppleDouble files;
#    this marks them hidden for Windows
# Deliberately NOT used: noappledouble (xattrs then fail -> Finder error -36),
# rwsize (bigger blocks made writes slower), user_xattr (no effect over NFS).
BASE_MOUNT_OPTIONS = ("umask=022", "windows_names", "hide_dot_files")

MOUNT_WAIT_SECONDS = 10.0


class NtfsError(RuntimeError):
    """User-facing error: printed without a traceback."""


# --------------------------------------------------------------------------- data


@dataclass(frozen=True)
class Partition:
    identifier: str  # disk4s2
    volume_name: str
    size: int
    macos_mount_point: str  # "" when not mounted by macOS itself

    @property
    def device(self) -> str:
        return f"/dev/{self.identifier}"


@dataclass(frozen=True)
class Ntfs3gMount:
    pid: int
    device: str
    mount_point: str


# ------------------------------------------------------------------ small helpers


def is_macos() -> bool:
    return platform.system() == "Darwin"


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1000
    return f"{n} B"


def sanitize_volume_name(name: str) -> str:
    """Mount point / Finder name without spaces or special characters.

    FUSE-T passes volname to the macOS mount command; plain ASCII names are the
    safe choice, and they also keep /Volumes/<name> easy to type.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")
    return cleaned or "NTFS"


def build_mount_options(uid: int, gid: int, volname: str, read_only: bool = False) -> str:
    opts = [f"uid={uid}", f"gid={gid}", *BASE_MOUNT_OPTIONS, f"volname={volname}"]
    if read_only:
        opts.append("ro")
    return ",".join(opts)


def normalize_device(value: str) -> str:
    """Accept disk4s2, /dev/disk4s2 or /dev/rdisk4s2 and return /dev/disk4s2."""
    v = value.strip()
    if v.startswith("/dev/"):
        v = v[len("/dev/") :]
    if v.startswith("rdisk"):
        v = v[1:]
    if not re.fullmatch(r"disk\d+s\d+", v):
        raise NtfsError(f"Not a partition identifier: {value!r} (expected something like disk4s2)")
    return f"/dev/{v}"


def show_cmd(cmd: Sequence[str]) -> None:
    console.print(f"[dim]$ {shlex.join(cmd)}[/dim]")


def run(cmd: Sequence[str], *, check: bool = True, capture: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        list(cmd),
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        text=True,
        env=env,
        check=False,
    )
    if check and proc.returncode != 0:
        out = (proc.stdout or "").strip()
        raise NtfsError(f"Command failed ({proc.returncode}): {shlex.join(cmd)}" + (f"\n{out}" if out else ""))
    return proc


def run_sudo(cmd: Sequence[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    """Run one privileged command. sudo asks for the password in the terminal."""
    full = ["sudo", *cmd]
    show_cmd(full)
    return run(full, check=check, capture=capture)


def require_macos() -> None:
    if not is_macos():
        raise NtfsError(
            "duka ntfs is only needed on macOS. Linux and Windows can write NTFS out of the box."
        )


def refuse_root() -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise NtfsError(
            "Do not run duka with sudo. It asks for your password itself, only for the "
            "individual steps that need root."
        )


# ------------------------------------------------------------ diskutil / ps / mount


def parse_external_ntfs_partitions(list_plist: dict, info_by_id: dict[str, dict]) -> list[Partition]:
    """Pick NTFS partitions out of `diskutil list -plist external` + per-partition info."""
    parts: list[Partition] = []
    for disk in list_plist.get("AllDisksAndPartitions", []):
        for p in disk.get("Partitions", []):
            ident = p.get("DeviceIdentifier", "")
            info = info_by_id.get(ident, {})
            fs_type = (info.get("FilesystemType") or "").lower()
            if fs_type != "ntfs":
                continue
            parts.append(
                Partition(
                    identifier=ident,
                    volume_name=info.get("VolumeName") or p.get("VolumeName") or ident,
                    size=int(info.get("Size") or p.get("Size") or 0),
                    macos_mount_point=info.get("MountPoint") or "",
                )
            )
    return parts


def diskutil_plist(*args: str) -> dict:
    """Run `diskutil <verb> -plist [args]` and parse the result ({} on failure)."""
    verb, *rest = args
    proc = subprocess.run(["diskutil", verb, "-plist", *rest], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        return {}
    try:
        return plistlib.loads(proc.stdout)
    except Exception:
        return {}


def diskutil_info(identifier: str) -> dict:
    return diskutil_plist("info", identifier)


def external_ntfs_partitions() -> list[Partition]:
    listing = diskutil_plist("list", "external")
    ids = [
        p.get("DeviceIdentifier", "")
        for d in listing.get("AllDisksAndPartitions", [])
        for p in d.get("Partitions", [])
    ]
    infos = {i: diskutil_info(i) for i in ids if i}
    return parse_external_ntfs_partitions(listing, infos)


_NTFS3G_PS_RE = re.compile(r"^\s*(\d+)\s+(?:\S*/)?ntfs-3g\s+(/dev/r?disk\d+s\d+)\s+(.+?)(?:\s+-o\s.*)?$")


def parse_ntfs3g_processes(ps_output: str) -> list[Ntfs3gMount]:
    mounts: list[Ntfs3gMount] = []
    for line in ps_output.splitlines():
        m = _NTFS3G_PS_RE.match(line)
        if m:
            mounts.append(Ntfs3gMount(pid=int(m.group(1)), device=normalize_device(m.group(2)), mount_point=m.group(3).strip()))
    return mounts


def ntfs3g_mounts() -> list[Ntfs3gMount]:
    proc = run(["ps", "-axww", "-o", "pid=,command="], check=False)
    return parse_ntfs3g_processes(proc.stdout or "")


def mounted_paths(mount_output: str) -> set[str]:
    paths = set()
    for line in mount_output.splitlines():
        m = re.match(r"^.+? on (.+) \([^()]*\)$", line)
        if m:
            paths.add(m.group(1))
    return paths


def is_mounted(path: str) -> bool:
    return path in mounted_paths(run(["mount"], check=False).stdout or "")


def wait_until(predicate, timeout: float = MOUNT_WAIT_SECONDS, step: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


# ------------------------------------------------------------------ install checks


def fuse_t_version() -> str | None:
    pc = FUSE_T_PKGCONFIG_DIR / "fuse-t.pc"
    if not (FUSE_T_LIB.exists() and pc.exists() and FUSE_T_SUPPORT_DIR.is_dir()):
        return None
    for line in pc.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("Version:"):
            return line.split(":", 1)[1].strip()
    return "unknown"


def command_line_tools_ok() -> bool:
    return run(["xcode-select", "-p"], check=False).returncode == 0 and shutil.which("make") is not None


def binary_problem(path: Path) -> str | None:
    """None if the binary is safe to run through sudo, otherwise the reason it is not."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return "not installed"
    if st.st_uid != 0:
        return "not owned by root"
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return "writable by group/others"
    if not os.access(path, os.X_OK):
        return "not executable"
    return None


def installed_ntfs3g_version() -> str | None:
    if not NTFS3G_BIN.exists():
        return None
    out = run([str(NTFS3G_BIN), "--version"], check=False).stdout or ""
    m = re.search(r"ntfs-3g\s+(\S+)", out)
    return m.group(1) if m else "unknown"


def linked_libraries(binary: Path) -> str:
    return run(["otool", "-L", str(binary)], check=False).stdout or ""


def require_installed() -> None:
    for b in (NTFS3G_BIN, NTFSFIX_BIN):
        problem = binary_problem(b)
        if problem:
            raise NtfsError(f"{b}: {problem}. Run `duka ntfs setup` first.")
    if fuse_t_version() is None:
        raise NtfsError(f"FUSE-T is not installed. Install it with:\n  {FUSE_T_INSTALL_CMD}")


# -------------------------------------------------------------------------- doctor


def doctor() -> int:
    console.rule("[bold cyan]NTFS DOCTOR")
    if not is_macos():
        console.print(f"Platform: {platform.system()} - NTFS is writable natively here, nothing to do.")
        return 0

    ok = True

    def line(label: str, good: bool, detail: str) -> None:
        mark = "[green]ok[/green]" if good else "[red]missing[/red]"
        console.print(f"[bold]{label:<22}[/bold] {mark}  {detail}")

    console.print(f"[bold]{'macOS':<22}[/bold] {platform.mac_ver()[0]} ({platform.machine()})")

    fv = fuse_t_version()
    line("FUSE-T", fv is not None, fv or f"install: {FUSE_T_INSTALL_CMD}")
    ok &= fv is not None

    clt = command_line_tools_ok()
    line("Command Line Tools", clt, "" if clt else "install: xcode-select --install")

    pkgc = shutil.which("pkg-config")
    line("pkg-config", pkgc is not None, pkgc or "install: brew install pkgconf")

    for b in (NTFS3G_BIN, NTFSFIX_BIN):
        problem = binary_problem(b)
        line(b.name, problem is None, str(b) if problem is None else f"{problem} -> duka ntfs setup")
        ok &= problem is None

    ver = installed_ntfs3g_version()
    if ver:
        same = ver == NTFS3G_VERSION
        console.print(f"[bold]{'ntfs-3g version':<22}[/bold] {ver}" + ("" if same else f"  (duka pins {NTFS3G_VERSION}; `duka ntfs setup --force` to rebuild)"))
        libs = linked_libraries(NTFS3G_BIN)
        if "libfuse-t" not in libs:
            console.print("[bold yellow]ntfs-3g is not linked against FUSE-T (macFUSE build?). Rebuild with `duka ntfs setup --force`.[/bold yellow]")
            ok = False

    console.print()
    print_partitions()
    return 0 if ok else 1


# --------------------------------------------------------------------------- setup


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "dukatools"})
    with urllib.request.urlopen(req, timeout=60) as resp, dest.open("wb") as out:
        shutil.copyfileobj(resp, out)


def extract(archive: Path, dest: Path) -> Path:
    with tarfile.open(archive, "r:gz") as tf:
        if hasattr(tarfile, "data_filter"):
            tf.extractall(dest, filter="data")
        else:  # Python < 3.12; archive is already SHA-256 verified
            tf.extractall(dest)
    candidates = [p for p in dest.iterdir() if p.is_dir() and p.name.startswith("ntfs-3g")]
    if len(candidates) != 1:
        raise NtfsError(f"Unexpected archive layout in {archive.name}")
    return candidates[0]


def build_env() -> dict:
    env = os.environ.copy()
    pc_path = str(FUSE_T_PKGCONFIG_DIR)
    env["PKG_CONFIG_PATH"] = pc_path + (os.pathsep + env["PKG_CONFIG_PATH"] if env.get("PKG_CONFIG_PATH") else "")
    cflags = run(["pkg-config", "--cflags", "fuse-t"], env=env).stdout.strip()
    libs = run(["pkg-config", "--libs", "fuse-t"], env=env).stdout.strip()
    # Official configure overrides: pin the FUSE library to FUSE-T even if macFUSE is installed.
    env["FUSE_MODULE_CFLAGS"] = cflags
    env["FUSE_MODULE_LIBS"] = libs
    env["CPPFLAGS"] = "-D_FILE_OFFSET_BITS=64"
    return env


def build_ntfs3g(src: Path, log: Path) -> tuple[Path, Path]:
    env = build_env()
    steps = [
        ["./configure", "--with-fuse=external", "--disable-shared", "--enable-static", "--disable-ldconfig"],
        ["make", f"-j{os.cpu_count() or 2}"],
    ]
    with log.open("w") as lf:
        for cmd in steps:
            console.print(f"[cyan]{shlex.join(cmd)}[/cyan]")
            proc = subprocess.run(cmd, cwd=src, env=env, stdout=lf, stderr=subprocess.STDOUT, check=False)
            if proc.returncode != 0:
                tail = "\n".join(log.read_text(errors="replace").splitlines()[-25:])
                raise NtfsError(f"Build step failed: {shlex.join(cmd)}\n{tail}")
    ntfs3g, ntfsfix = src / "src" / "ntfs-3g", src / "ntfsprogs" / "ntfsfix"
    for b in (ntfs3g, ntfsfix):
        if not b.exists():
            raise NtfsError(f"Build finished but {b} is missing")
    libs = linked_libraries(ntfs3g)
    if "libfuse-t" not in libs or "macfuse" in libs.lower() or "osxfuse" in libs.lower():
        raise NtfsError(f"Built ntfs-3g is not linked against FUSE-T only:\n{libs}")
    return ntfs3g, ntfsfix


def confirm(question: str, assume_yes: bool = False) -> bool:
    """Ask a y/N question. Without a terminal (or on EOF) the answer is no."""
    if assume_yes:
        console.print(f"{question} [y/N] y (--yes)")
        return True
    if not sys.stdin.isatty():
        return False
    try:
        answer = console.input(f"{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes", "д", "да")


def ensure_prerequisites(assume_yes: bool = False) -> None:
    """Offer to install missing prerequisites via Homebrew, each after an explicit yes."""
    brew = shutil.which("brew")

    def offer(what: str, cmd: list[str], note: str) -> None:
        if brew is None:
            raise NtfsError(f"{what} is missing and Homebrew is not installed. Install Homebrew (https://brew.sh), then:\n  {shlex.join(cmd)}")
        console.print(f"[bold yellow]{what} is missing.[/bold yellow] {note}")
        if not confirm(f"Run `{shlex.join(cmd)}` now?", assume_yes):
            raise NtfsError(f"{what} is required. Install it yourself and run `duka ntfs setup` again:\n  {shlex.join(cmd)}")
        show_cmd(cmd)
        run(cmd, capture=False)

    if fuse_t_version() is None:
        offer(
            "FUSE-T",
            ["brew", "install", "--cask", "macos-fuse-t/cask/fuse-t"],
            "It is the kext-free FUSE layer ntfs-3g runs on: a closed-source .pkg, free for "
            "non-commercial use. The installer asks for your admin password.",
        )
        if fuse_t_version() is None:
            raise NtfsError("FUSE-T installation did not finish. Check the brew output above.")

    if not command_line_tools_ok():
        console.print("[bold yellow]Xcode Command Line Tools are missing[/bold yellow] (compiler and make).")
        if confirm("Open Apple's installer (`xcode-select --install`)?", assume_yes):
            run(["xcode-select", "--install"], check=False)
        raise NtfsError("Finish the Command Line Tools installation, then run `duka ntfs setup` again.")

    if shutil.which("pkg-config") is None:
        offer("pkg-config", ["brew", "install", "pkgconf"], "A small build helper; no password needed.")
        if shutil.which("pkg-config") is None:
            raise NtfsError("pkg-config is still not in PATH. Open a new terminal and run `duka ntfs setup` again.")


def setup(force: bool = False, keep_build: bool = False, assume_yes: bool = False) -> int:
    require_macos()
    refuse_root()
    ensure_prerequisites(assume_yes)

    current = installed_ntfs3g_version()
    if not force and current == NTFS3G_VERSION and all(binary_problem(b) is None for b in (NTFS3G_BIN, NTFSFIX_BIN)):
        console.print(f"[green]ntfs-3g {current} is already installed in {INSTALL_DIR}.[/green] Use --force to rebuild.")
        return 0

    work = Path(tempfile.mkdtemp(prefix="duka-ntfs-"))
    try:
        archive = work / f"ntfs-3g_ntfsprogs-{NTFS3G_VERSION}.tgz"
        console.print(f"Downloading ntfs-3g {NTFS3G_VERSION} from {NTFS3G_URL}")
        download(NTFS3G_URL, archive)
        digest = sha256_file(archive)
        if digest != NTFS3G_SHA256:
            raise NtfsError(f"SHA-256 mismatch for {archive.name}:\n  expected {NTFS3G_SHA256}\n  got      {digest}")
        console.print("[green]SHA-256 verified.[/green]")

        src = extract(archive, work)
        console.print(f"Building in {src} (about a minute)...")
        ntfs3g, ntfsfix = build_ntfs3g(src, work / "build.log")
        console.print("[green]Build ok, linked against FUSE-T.[/green]")

        console.print()
        console.print(
            f"Installing ntfs-3g and ntfsfix into {INSTALL_DIR} as root:wheel 755.\n"
            "They must be root-owned because mounting runs them through sudo."
        )
        run_sudo(["install", "-d", "-m", "755", "-o", "root", "-g", "wheel", str(INSTALL_DIR)])
        run_sudo(["install", "-m", "755", "-o", "root", "-g", "wheel", str(ntfs3g), str(ntfsfix), str(INSTALL_DIR) + "/"])
    finally:
        if keep_build:
            console.print(f"Build directory kept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)

    console.print(f"[bold green]Done.[/bold green] Installed ntfs-3g {installed_ntfs3g_version()}. Next: duka ntfs mount")
    return 0


# ------------------------------------------------------------------- list / mount


def print_partitions() -> list[Partition]:
    parts = external_ntfs_partitions()
    rw = {m.device: m for m in ntfs3g_mounts()}
    if not parts:
        console.print("No external NTFS partitions found.")
        return parts
    table = Table(title="External NTFS partitions")
    table.add_column("Device")
    table.add_column("Name")
    table.add_column("Size", justify="right")
    table.add_column("State")
    for p in parts:
        if p.device in rw:
            state = f"[green]read/write[/green] at {rw[p.device].mount_point}"
        elif p.macos_mount_point:
            state = f"[yellow]read-only (macOS)[/yellow] at {p.macos_mount_point}"
        else:
            state = "not mounted"
        table.add_row(p.identifier, p.volume_name, human_size(p.size), state)
    console.print(table)
    return parts


def pick_partition(device: str | None) -> Partition:
    parts = external_ntfs_partitions()
    if device:
        dev = normalize_device(device)
        for p in parts:
            if p.device == dev:
                return p
        raise NtfsError(f"{dev} is not an external NTFS partition. See `duka ntfs list`.")
    if not parts:
        raise NtfsError("No external NTFS partitions found. Is the drive connected?")
    if len(parts) > 1:
        names = ", ".join(f"{p.identifier} ({p.volume_name})" for p in parts)
        raise NtfsError(f"Several NTFS partitions found: {names}. Pass one, e.g. `duka ntfs mount {parts[0].identifier}`.")
    return parts[0]


def mount(device: str | None, name: str | None, read_only: bool, skip_check: bool) -> int:
    require_macos()
    refuse_root()
    require_installed()

    part = pick_partition(device)
    existing = {m.device: m for m in ntfs3g_mounts()}
    if part.device in existing:
        console.print(f"{part.device} is already mounted by ntfs-3g at {existing[part.device].mount_point}.")
        return 0

    volname = sanitize_volume_name(name or part.volume_name)
    mount_point = f"/Volumes/{volname}"
    if is_mounted(mount_point) or (Path(mount_point).exists() and any(Path(mount_point).iterdir())):
        raise NtfsError(f"{mount_point} is already in use. Pick another name with --name.")

    # 1. macOS keeps the device open while its read-only mount exists.
    if part.macos_mount_point:
        console.print(f"Unmounting the read-only macOS mount at {part.macos_mount_point}")
        show_cmd(["diskutil", "unmount", part.device])
        run(["diskutil", "unmount", part.device])

    # 2. Read-only consistency check. Writing to a dirty or hibernated volume is how NTFS gets damaged.
    if not (skip_check or read_only):
        console.print("Checking the volume (read-only, nothing is written)")
        proc = run_sudo([str(NTFSFIX_BIN), "-n", part.device], check=False, capture=True)
        out = (proc.stdout or "").strip()
        if out:
            console.print(f"[dim]{out}[/dim]")
        if proc.returncode != 0:
            raise NtfsError(
                "The volume is not clean, refusing to mount it read/write.\n"
                "Connect the drive to Windows, run `chkdsk /f`, and eject it safely "
                "(also disable Fast Startup). Or mount read-only: duka ntfs mount --ro"
            )

    # 3. macOS removes /Volumes/<dir> on every unmount, so recreate it each time.
    run_sudo(["mkdir", "-p", mount_point])
    opts = build_mount_options(os.getuid(), os.getgid(), volname, read_only=read_only)
    # Not captured: ntfs-3g daemonizes, and a pipe inherited by the daemon could block us.
    # Its messages (e.g. about hibernation) go straight to the terminal.
    proc = run_sudo([str(NTFS3G_BIN), part.device, mount_point, "-o", opts], check=False)
    if proc.returncode != 0:
        raise NtfsError(f"ntfs-3g refused to mount {part.device} (see its message above).")

    if not wait_until(lambda: is_mounted(mount_point)):
        raise NtfsError(f"ntfs-3g started but {mount_point} did not appear. Check `mount` and `duka ntfs list`.")
    mode = "read-only" if read_only else "read/write"
    console.print(f"[bold green]Mounted {part.volume_name} {mode} at {mount_point}.[/bold green]")
    if not read_only:
        console.print(
            "[dim]Writes are slower than reads (typically 5-30 MB/s on a USB HDD). "
            "Eject with `duka ntfs unmount` before unplugging.[/dim]"
        )
    return 0


def unmount(target: str | None, eject: bool) -> int:
    require_macos()
    refuse_root()

    mounts = ntfs3g_mounts()
    if not mounts:
        console.print("No ntfs-3g mounts found.")
        return 0
    if target:
        t = target.rstrip("/") or "/"
        chosen = [m for m in mounts if m.mount_point == t]
        if not chosen:
            dev = normalize_device(t)
            chosen = [m for m in mounts if m.device == dev]
        if not chosen:
            raise NtfsError(f"No ntfs-3g mount matches {target!r}. See `duka ntfs list`.")
    elif len(mounts) > 1:
        names = ", ".join(m.mount_point for m in mounts)
        raise NtfsError(f"Several ntfs-3g mounts: {names}. Pass one of them.")
    else:
        chosen = mounts

    for m in chosen:
        run(["sync"], check=False)
        proc = run_sudo(["umount", m.mount_point], check=False, capture=True)
        if proc.returncode != 0:
            raise NtfsError(
                f"Could not unmount {m.mount_point} (files still open?):\n{(proc.stdout or '').strip()}\n"
                "Close apps using the drive and try again."
            )
        # ntfs-3g writes the 'clean' flag on exit; wait for the process before touching the disk.
        if not wait_until(lambda: m.pid not in {x.pid for x in ntfs3g_mounts()}, timeout=30):
            raise NtfsError(f"ntfs-3g (pid {m.pid}) is still running after unmount. Do not unplug the drive yet.")
        console.print(f"[green]Unmounted {m.mount_point}.[/green]")
        if eject:
            whole = re.sub(r"s\d+$", "", m.device)
            show_cmd(["diskutil", "eject", whole])
            run(["diskutil", "eject", whole])
            console.print(f"[green]Ejected {whole}. Safe to unplug.[/green]")
    return 0


# ---------------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    examples = f"""
Examples:
  duka ntfs doctor            # what is installed, what is connected
  duka ntfs setup             # offers to install FUSE-T/pkgconf, then builds + installs ntfs-3g {NTFS3G_VERSION}
  duka ntfs list
  duka ntfs mount             # the only external NTFS partition, read/write
  duka ntfs mount disk4s2 --name ADATA
  duka ntfs unmount --eject

Requirements: macOS, Homebrew, Xcode Command Line Tools. `setup` offers to install
FUSE-T ({FUSE_T_INSTALL_CMD}) and pkgconf, asking first.
Manual recipe and known limitations:
  https://github.com/rexologue/dukatools/blob/main/docs/ntfs-macos.md
""".strip()
    p = argparse.ArgumentParser(
        prog="duka ntfs",
        description="Read/write NTFS drives on macOS via FUSE-T + ntfs-3g (no kernel extension).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=examples,
    )
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")
    sub.add_parser("doctor", help="Check FUSE-T, build tools, installed ntfs-3g and connected drives.")

    p_setup = sub.add_parser("setup", help="Download, verify, build and install ntfs-3g (sudo once).")
    p_setup.add_argument("--force", action="store_true", help="Rebuild even if the pinned version is installed.")
    p_setup.add_argument("--keep-build", action="store_true", help="Keep the temporary build directory.")
    p_setup.add_argument("-y", "--yes", action="store_true", help="Install missing prerequisites (FUSE-T, pkgconf) without asking.")

    sub.add_parser("list", help="Show external NTFS partitions and their mount state.")

    p_mount = sub.add_parser("mount", help="Mount an NTFS partition read/write.")
    p_mount.add_argument("device", nargs="?", help="Partition, e.g. disk4s2. Optional if only one is connected.")
    p_mount.add_argument("--name", help="Mount as /Volumes/NAME (default: volume label without spaces).")
    p_mount.add_argument("--ro", action="store_true", help="Mount read-only through ntfs-3g (skips the check).")
    p_mount.add_argument("--no-check", action="store_true", help="Skip the `ntfsfix -n` consistency check.")

    p_unmount = sub.add_parser("unmount", aliases=["umount"], help="Cleanly unmount an ntfs-3g mount.")
    p_unmount.add_argument("target", nargs="?", help="Mount point or partition. Optional if only one is mounted.")
    p_unmount.add_argument("--eject", action="store_true", help="Also eject the whole disk afterwards.")
    return p


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.cmd == "doctor":
            return doctor()
        require_macos()
        if args.cmd == "setup":
            return setup(force=args.force, keep_build=args.keep_build, assume_yes=args.yes)
        if args.cmd == "list":
            refuse_root()
            print_partitions()
            return 0
        if args.cmd == "mount":
            return mount(args.device, args.name, read_only=args.ro, skip_check=args.no_check)
        if args.cmd in ("unmount", "umount"):
            return unmount(args.target, eject=args.eject)
        return 2
    except NtfsError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        return 1
    except KeyboardInterrupt:
        console.print("[bold yellow]Interrupted.[/bold yellow]")
        return 130


if __name__ == "__main__":
    sys.exit(main())
