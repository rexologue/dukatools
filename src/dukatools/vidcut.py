from __future__ import annotations
import argparse, subprocess, sys, shutil, os, glob, math, re
from pathlib import Path
from typing import List, Optional

def _fail(msg: str, code: int = 2) -> None:
    print(f"[vidcut] {msg}", file=sys.stderr); sys.exit(code)

def _parse_time(s: str) -> float:
    s = s.strip().lower()
    if s.endswith("ms"): return float(s[:-2]) / 1000.0
    if s.endswith("s"): s = s[:-1]
    if ":" in s:
        parts = [float(p) for p in s.split(":")]
        while len(parts) < 3: parts.insert(0, 0.0)
        h, m, sec = parts; return h*3600.0 + m*60.0 + sec
    return float(s)

def _fmt_time(t: float) -> str:
    if t < 0: t = 0.0
    ms = int(round((t - math.floor(t)) * 1000.0))
    total = int(t); hh = total // 3600; mm = (total % 3600) // 60; ss = total % 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}.{ms:03d}"

def _resolve_ffmpeg(explicit: Optional[str]) -> str:
    # 1) explicit path/name
    if explicit:
        p = shutil.which(explicit) or (explicit if Path(explicit).exists() else None)
        if p: return str(p)
    # 2) env override
    env = os.environ.get("DUKATOOLS_FFMPEG")
    if env and Path(env).exists(): return env
    # 3) imageio-ffmpeg (auto-download/cache)
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        return get_ffmpeg_exe()
    except Exception:
        pass
    # 4) system ffmpeg
    p = shutil.which("ffmpeg")
    if p: return p
    _fail("ffmpeg not found. Install it system-wide OR rely on imageio-ffmpeg (bundled via dukatools) to auto-download; set DUKATOOLS_FFMPEG to override.")

def _is_mp4(path: Path) -> bool:
    return path.suffix.lower() in {".mp4", ".m4v", ".mov"}

def _probe_duration_via_ffmpeg(ffmpeg: str, path: Path) -> Optional[float]:
    # Parse stderr of `ffmpeg -i input`: Duration: HH:MM:SS.xx
    try:
        proc = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr)
        if not m: return None
        h, mnt, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        return h*3600 + mnt*60 + s
    except Exception:
        return None

def _build_fast_cmd(ffmpeg: str, inp: Path, outp: Path, start: Optional[float], dur: Optional[float], overwrite: bool) -> List[str]:
    # Fast stream-copy: -ss before -i, -t duration, -c copy, keep all streams
    cmd: List[str] = [ffmpeg, "-hide_banner"]
    cmd += ["-y" if overwrite else "-n"]
    if start is not None: cmd += ["-ss", _fmt_time(start)]
    cmd += ["-i", str(inp)]
    if dur is not None: cmd += ["-t", _fmt_time(max(0.0, dur))]
    cmd += ["-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero"]
    if _is_mp4(outp): cmd += ["-movflags", "+faststart"]
    cmd += [str(outp)]
    return cmd

def _build_acc_cmd(ffmpeg: str, inp: Path, outp: Path, start: Optional[float], dur: Optional[float], overwrite: bool) -> List[str]:
    # Accurate cut: -i first, then -ss/-t; re-encode video for frame-accuracy, copy audio
    cmd: List[str] = [ffmpeg, "-hide_banner"]
    cmd += ["-y" if overwrite else "-n", "-i", str(inp)]
    if start is not None: cmd += ["-ss", _fmt_time(start)]
    if dur is not None: cmd += ["-t", _fmt_time(max(0.0, dur))]
    cmd += ["-map", "0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "copy"]
    if _is_mp4(outp): cmd += ["-movflags", "+faststart"]
    cmd += [str(outp)]
    return cmd

def _run(cmd: List[str]) -> int:
    try:
        return subprocess.run(cmd).returncode
    except FileNotFoundError:
        return 127

def _derive_output(inp: Path, suffix: str) -> Path:
    return inp.with_name(inp.stem + suffix + inp.suffix)

def _expand_inputs(items: List[str]) -> List[Path]:
    acc: List[Path] = []
    for it in items:
        if any(ch in it for ch in "*?[]"): acc += [Path(p) for p in glob.glob(it)]
        else: acc.append(Path(it))
    seen = set(); out: List[Path] = []
    for p in acc:
        if p not in seen: seen.add(p); out.append(p)
    return out

def main():
    epilog = """
EXAMPLES (all single-line):

  # Fast, no re-encode: cut from 00:00:05 to 00:00:12
  vidcut input.mp4 --from 00:00:05 --to 00:00:12 --overwrite

  # Keep 10 seconds starting at 45.5s, write to out.mp4
  vidcut input.mp4 --from 45.5 --duration 10s -o out.mp4 --overwrite

  # Trim the first 4 seconds, keep the rest
  vidcut input.mp4 --trim-start 4s --overwrite

  # Trim the last 3 seconds (auto-detects duration)
  vidcut input.mp4 --trim-end 3s --overwrite

  # Frame-accurate cut (re-encodes video, audio copied)
  vidcut input.mp4 --from 00:01:00 --duration 5s --accurate --overwrite

  # Batch: all mp4, keep 15s clips with suffix _clip
  vidcut "*.mp4" --from 2m --duration 15s --suffix _clip --overwrite

  # Inspect and cache ffmpeg that vidcut will use
  vidcut --doctor
"""
    p = argparse.ArgumentParser(
        prog="vidcut",
        description=(
            "Fast & accurate video trimming powered by FFmpeg.\n"
            "Default mode performs stream copy (no re-encode) for speed; if it fails, "
            "vidcut automatically falls back to frame-accurate cutting with video re-encode.\n"
            "FFmpeg is auto-bundled via imageio-ffmpeg when not found on PATH."
        ),
        epilog=epilog,
        formatter_class=argparse.RawTextHelpFormatter,
    )

    p.add_argument("inputs", nargs="+", help="Input file(s) or glob (e.g. *.mp4)")
    p.add_argument("-o", "--out", help="Output file (single input only)")
    p.add_argument("--suffix", default="_cut", help="Suffix for batch outputs (default: _cut)")
    p.add_argument("--from", dest="start", help="Start time (e.g. 5, 00:00:05.200, 90s)")
    p.add_argument("--to", help="End time (absolute), e.g. 00:00:10.000")
    p.add_argument("-t", "--duration", help="Duration, e.g. 5s or 00:00:05")
    p.add_argument("--trim-start", help="Trim N seconds from the start, e.g. 4s")
    p.add_argument("--trim-end", help="Trim N seconds from the end (keeps the rest)")
    p.add_argument("--accurate", action="store_true", help="Frame-accurate (re-encode video, copy audio)")
    p.add_argument("--fast", action="store_true", help="Force fast stream copy mode (default)")
    p.add_argument("--overwrite", action="store_true", help="Overwrite outputs if they exist")
    p.add_argument("--dry-run", action="store_true", help="Print the ffmpeg command(s) and exit")
    p.add_argument("--ffmpeg", default=None, help="Override ffmpeg binary path/name")
    p.add_argument("--doctor", action="store_true", help="Show which ffmpeg is used and prefetch it")

    args = p.parse_args()

    ffmpeg = _resolve_ffmpeg(args.ffmpeg)

    if args.doctor:
        try:
            out = subprocess.check_output([ffmpeg, "-version"], stderr=subprocess.STDOUT).decode("utf-8", "ignore")
            print("[vidcut] ffmpeg:", ffmpeg)
            print(out.splitlines()[0])
        except Exception as e:
            _fail(f"Cannot run ffmpeg: {e}")
        sys.exit(0)

    inputs = _expand_inputs(args.inputs)
    if not inputs: _fail("No input files matched.")
    if args.out and len(inputs) != 1: _fail("--out is only allowed with a single input.")

    start = _parse_time(args.start) if args.start else None
    to_abs = _parse_time(args.to) if args.to else None
    dur = _parse_time(args.duration) if args.duration else None
    trim_start = _parse_time(args.trim_start) if args.trim_start else None
    trim_end = _parse_time(args.trim_end) if args.trim_end else None

    for inp in inputs:
        if not inp.exists():
            print(f"[vidcut] skip (not found): {inp}", file=sys.stderr); continue

        s = start if start is not None else 0.0
        if trim_start: s = max(0.0, s + trim_start)

        D = None
        if to_abs is not None or trim_end is not None:
            D = _probe_duration_via_ffmpeg(ffmpeg, inp)
            if D is None and trim_end is not None:
                _fail(f"Cannot detect duration for: {inp}")

        if to_abs is not None and to_abs >= 0: d = max(0.0, to_abs - s)
        elif dur is not None: d = max(0.0, dur)
        else: d = None

        if trim_end is not None:
            keep_to = max(0.0, (D or 0.0) - trim_end)
            d = keep_to - s
            if d < 0: _fail(f"Trim range invalid for {inp.name}: start={_fmt_time(s)} > keep_to={_fmt_time(max(0.0, keep_to))}")

        outp = Path(args.out) if args.out else _derive_output(inp, args.suffix)

        prefer_fast = not args.accurate or args.fast
        if prefer_fast:
            cmd = _build_fast_cmd(ffmpeg, inp, outp, s if s>0 else None, d, args.overwrite)
            if args.dry_run: print(" ".join(cmd)); continue
            rc = _run(cmd)
            if rc != 0:
                print(f"[vidcut] fast copy failed (rc={rc}), falling back to accurate…", file=sys.stderr)
                cmd2 = _build_acc_cmd(ffmpeg, inp, outp, s if s>0 else None, d, args.overwrite)
                if args.dry_run: print(" ".join(cmd2)); continue
                rc = _run(cmd2)
                if rc != 0: _fail(f"Accurate fallback failed for {inp.name} (rc={rc}).")
        else:
            cmd = _build_acc_cmd(ffmpeg, inp, outp, s if s>0 else None, d, args.overwrite)
            if args.dry_run: print(" ".join(cmd)); continue
            rc = _run(cmd)
            if rc != 0: _fail(f"Accurate cut failed for {inp.name} (rc={rc}).")

        print(f"[vidcut] OK: {inp.name} -> {outp.name}")

if __name__ == "__main__":
    main()
