"""
python-build-standalone installer (one-file, no deps)

Env:
  GITHUB_TOKEN  Optional GitHub token to avoid API rate limits.

Notes:
  - Default variant: install_only_stripped (compact; .tar.gz)
  - On Linux, musl vs glibc is auto-detected (ldd or /etc/alpine-release).
  - .tar.zst archives (rare here) require external `tar` for extraction.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


API_DEFAULT = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"


def log(msg: str) -> None:
    print(msg, flush=True)


def detect_triplet() -> str:
    sysname = platform.system()
    mach = platform.machine().lower()
    # arch map
    if mach in ("x86_64", "amd64"):
        arch = "x86_64"
    elif mach in ("aarch64", "arm64"):
        arch = "aarch64"
    else:
        raise SystemExit(f"Unsupported CPU architecture: {mach}")

    if sysname == "Linux":
        libc = detect_libc()
        return f"{arch}-unknown-linux-{libc}"
    elif sysname == "Darwin":
        return f"{arch}-apple-darwin"
    elif sysname == "Windows":
        return f"{arch}-pc-windows-msvc"
    else:
        # MSYS/CYGWIN sometimes report differently
        if sys.platform.startswith(("msys", "cygwin")):
            return f"{arch}-pc-windows-msvc"
        raise SystemExit(f"Unsupported OS: {sysname}")


def detect_libc() -> str:
    # Try to detect musl vs glibc
    try:
        out = subprocess.check_output(["ldd", "--version"], stderr=subprocess.STDOUT, text=True)
        if "musl" in out.lower():
            return "musl"
        return "gnu"
    except Exception:
        # Alpine hint
        if Path("/etc/alpine-release").exists():
            return "musl"
        # default assume glibc
        return "gnu"


def http_get(url: str, headers: Optional[Dict[str, str]] = None) -> bytes:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def fetch_latest_release(api_url: str, token: Optional[str]) -> Dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "pbs-install/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        data = http_get(api_url, headers=headers)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"GitHub API error: {e.code} {e.reason}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Network error: {e.reason}")
    return json.loads(data.decode("utf-8"))


_version_re = re.compile(r"^cpython-(\d+\.\d+(?:\.\d+)?)")


def parse_version_from_name(name: str) -> Optional[Tuple[int, ...]]:
    m = _version_re.match(name)
    if not m:
        return None
    parts = tuple(int(p) for p in m.group(1).split("."))
    return parts


def version_key_tuple(name: str) -> Tuple[int, ...]:
    v = parse_version_from_name(name)
    if v is None:
        return (0,)
    return v


def select_asset(
    release: Dict[str, Any],
    triplet: str,
    variant: str,
    want_version: Optional[str],
) -> Dict[str, Any]:
    assets: List[Dict[str, Any]] = release.get("assets", [])
    # base filter
    cands = [
        a for a in assets
        if a.get("name", "").startswith("cpython-")
        and triplet in a.get("name", "")
        and variant in a.get("name", "")
    ]

    if want_version:
        # allow "3.12" or "3.12.12"
        # match: cpython-<want>(.|t|+)
        pattern = re.compile(rf"^cpython-{re.escape(want_version)}(\.|t|\+)")
        cands = [a for a in cands if pattern.search(a.get("name", ""))]

    if not cands:
        raise SystemExit(
            f"No matching asset found for triplet={triplet} variant={variant} version='{want_version or 'latest'}'"
        )

    # Sort by semantic version and pick the highest
    cands.sort(key=lambda a: version_key_tuple(a.get("name", "")))
    return cands[-1]


def download(url: str, out_path: Path, token: Optional[str]) -> None:
    headers = {"User-Agent": "pbs-install/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # Stream to file
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp, open(out_path, "wb") as f:
        shutil.copyfileobj(resp, f)


def safe_extract(archive: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()
    if name.endswith((".tar.gz", ".tgz", ".tar.xz")):
        with tarfile.open(archive, mode="r:*") as tf:
            tf.extractall(target_dir)
    elif name.endswith(".zip"):
        import zipfile
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(target_dir)
    elif name.endswith(".tar.zst"):
        # Python stdlib has no zstd tar support; try system tar
        if shutil.which("tar"):
            subprocess.check_call(["tar", "-axf", str(archive), "-C", str(target_dir)])
        else:
            raise SystemExit("Cannot extract .tar.zst without external `tar` supporting zstd")
    else:
        raise SystemExit(f"Unknown archive format: {archive.name}")


def try_find_installed_python(root: Path) -> Optional[Path]:
    # Common layouts:
    #   <target>/python/bin/python3 (Unix)
    #   <target>/python/python.exe   (Windows)
    unix = root / "python" / "bin" / "python3"
    win = root / "python" / "python.exe"
    if unix.exists():
        return unix
    if win.exists():
        return win
    # maybe top-level pythonX.Y
    for p in root.rglob("python3*"):
        if p.is_file() and os.access(p, os.X_OK):
            return p
    for p in root.rglob("python.exe"):
        if p.is_file():
            return p
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Download (and optionally extract) python-build-standalone asset.")
    ap.add_argument("--dest", required=True, help="Destination directory for downloads/extraction")
    ap.add_argument("--version", default="", help="Desired Python version, e.g. 3.12 or 3.12.6 (default: latest)")
    ap.add_argument("--variant", default="install_only_stripped",
                    help="Asset variant: install_only_stripped | install_only | full | debug (default: install_only_stripped)")
    ap.add_argument("--extract", action="store_true", help="Extract archive after download")
    ap.add_argument("--api", default=API_DEFAULT, help="Releases API URL (default: latest release endpoint)")
    ap.add_argument("--triplet", default="", help="Override platform triplet (advanced)")
    args = ap.parse_args()

    dest = Path(args.dest).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)

    token = os.environ.get("GITHUB_TOKEN")
    triplet = args.triplet or detect_triplet()

    log(f"• Platform triplet: {triplet}")
    log(f"• Variant: {args.variant}")
    log(f"• Version: {args.version or 'latest'}")
    log("• Querying GitHub releases...")

    release = fetch_latest_release(args.api, token)
    asset = select_asset(release, triplet, args.variant, args.version or None)

    name = asset["name"]
    url = asset["browser_download_url"]
    ver = ".".join(map(str, parse_version_from_name(name) or [])) or "unknown"

    out_path = dest / name
    log(f"• Selected: {name}  (Python {ver})")
    log(f"• Downloading → {out_path}")
    download(url, out_path, token)
    log("• Download complete.")

    if args.extract:
        target_dir = dest / ver
        log(f"• Extracting to {target_dir}")
        safe_extract(out_path, target_dir)
        pybin = try_find_installed_python(target_dir)
        if pybin:
            log(f"• Python installed at: {pybin}")
            # Optional shims for Unix
            if os.name != "nt":
                bin_dir = dest / "bin"
                bin_dir.mkdir(exist_ok=True)
                minor = ".".join(str(x) for x in (parse_version_from_name(name) or (0, 0))[:2])
                shim1 = bin_dir / "python"
                shim2 = bin_dir / f"python{minor}"
                try:
                    if shim1.exists() or shim1.is_symlink():
                        shim1.unlink()
                    if shim2.exists() or shim2.is_symlink():
                        shim2.unlink()
                    shim1.symlink_to(pybin)
                    shim2.symlink_to(pybin)
                    log(f"• Shims created: {shim1} ; {shim2}")
                    log(f"• Add to PATH: export PATH=\"{bin_dir}:$PATH\"")
                except Exception as e:
                    log(f"• Shim creation skipped: {e}")
        else:
            log("• Warning: could not locate python binary inside extracted tree (layout may differ).")

    log("✓ Done.")


if __name__ == "__main__":
    main()
