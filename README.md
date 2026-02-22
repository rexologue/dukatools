# dukatools

> A small, batteries-included command line toolbox for working with directories and media across Linux, macOS, and Windows.

## Table of contents
- [Features at a glance](#features-at-a-glance)
- [Installation](#installation)
- [Upgrading](#upgrading)
- [Quick start](#quick-start)
- [CLI utilities](#cli-utilities)
  - [duka — run dukatools tools](#duka--run-dukatools-tools)
  - [treex — directory trees with excludes](#treex--directory-trees-with-excludes)
  - [dircat — batch dump directory files](#dircat--batch-dump-directory-files)
  - [vidcut — fast and accurate video trimming](#vidcut--fast-and-accurate-video-trimming)
  - [pydown — grab python-build-standalone releases](#pydown--grab-python-build-standalone-releases)
  - [pyarc — pack and unpack tar.gz archives](#pyarc--pack-and-unpack-targz-archives)
- [Configuration & environment variables](#configuration--environment-variables)
- [Development](#development)
- [License](#license)

## Features at a glance
- **Cross-platform binaries** via `uv tool`, `pipx`, or `pip` — no manual PATH setup required.
- **One entry point** (`duka`) to launch every other utility.
- **Tree inspection with smart excludes** through `treex`, ideal for sharing repository structure without build artifacts.
- **Directory dumping** via `dircat` for quick audits, backups, and reviews.
- **FFmpeg-powered video trimming** (`vidcut`) with automatic fallback from stream-copy to frame-accurate cuts.
- **One-file Python downloads** (`pydown`) for fetching and unpacking python-build-standalone releases without manual API spelunking.
- **Archive packing/unpacking** (`pyarc`) for fast, progress-aware `.tar.gz` workflows.
- **Zero-config defaults** plus optional environment overrides when you need extra control.

## Installation
`dukatools` is distributed as a standard Python package targeting Python 3.10+.

### Using [uv](https://docs.astral.sh/uv/)
```bash
uv tool install dukatools
```

### Using [pipx](https://pipx.pypa.io/)
```bash
pipx install dukatools
```

### Using pip
```bash
python -m pip install --user dukatools
```

All installation methods place the `duka`, `treex`, `dircat`, `vidcut`, `pydown`, and `pyarc` entry points on your PATH.

## Upgrading
Stay current with the latest enhancements and fixes:

```bash
uv tool upgrade dukatools
# or
pipx upgrade dukatools
# or
python -m pip install --upgrade dukatools
```

## Quick start
```bash
# Explore a repository without build artifacts
$ duka treex . --exclude .git build --exclude-re "*.pyc"

# Dump every text file in a directory into stdout (recursively)
$ duka dircat ./notes --exclude-re "^archive/"

# Trim the first five seconds off a video without re-encoding
$ duka vidcut sample.mp4 --trim-start 5s --overwrite

# Download and extract a python-build-standalone release into ~/python-builds
$ duka pydown --dest ~/python-builds --version 3.12 --extract

# Pack a project into a .tar.gz with excludes
$ duka pyarc pack ./project ./project.tar.gz --exclude .git --exclude-re "*.pyc"
```

You can still call `treex`, `dircat`, `vidcut`, `pydown`, and `pyarc` directly if you prefer.

## CLI utilities

### duka — run dukatools tools
`duka` is a lightweight launcher so you do not have to remember the individual binary names.

**Usage**
```bash
duka <tool> [args...]
```

**Example**
```bash
duka treex /home/user/project1 --exclude .git
```

If you run `duka` with no arguments, it prints the list of available tools.

### treex — directory trees with excludes
`treex` renders a directory structure in a friendly tree format while supporting both exact-name excludes and regex/glob rules.

**Highlights**
- Exclude generated folders such as `__pycache__`, `build`, or `node_modules` with a single command.
- Handles deeply nested projects and gracefully reports permission errors.
- Works great for quickly sharing repository layouts in issues, documentation, or chats.

**Usage**
```bash
treex PATH \
      [--exclude NAME ...] \
      [--exclude-re RULE ...]
```

If `PATH` is omitted, `treex` uses the current directory.

**Example output**
```
Directory tree for: ./project
Excluded rules: *.pyc
├── pyproject.toml
├── README.md
└── src
    ├── __init__.py
    └── project
        └── core.py
```

### dircat — batch dump directory files
`dircat` walks a directory, printing every file it finds to stdout with a clear header for each file.

**Highlights**
- Recursive by default for quick directory audits.
- Combine `--exclude` and `--exclude-re` to skip sensitive or noisy paths.
- Prints UTF-8 text and replaces undecodable bytes when needed.
- Perfect for quickly packaging logs, notes, or configuration snapshots for debugging.

**Usage**
```bash
dircat ROOT_DIR \
       [--exclude PATH ...] \
       [--exclude-re RULE ...]
```

**Writing to a file**
```bash
dircat ./config --exclude-re "\.git/" > artifacts/config-dump.txt
```

### vidcut — fast and accurate video trimming
`vidcut` wraps FFmpeg and provides a friendly interface for clipping one or more video files. It prefers stream-copy (no re-encode) for speed, then transparently falls back to a frame-accurate re-encode when necessary.

**Highlights**
- Accepts flexible time formats (`90`, `45.5`, `00:01:02.300`, `5s`, `2m`, etc.).
- Supports batch processing using glob patterns (`"*.mp4"`).
- Automatically discovers FFmpeg: explicit path, `DUKATOOLS_FFMPEG`, bundled `imageio-ffmpeg`, or system PATH.
- Provides a `--doctor` command to inspect and pre-download the FFmpeg binary.
- Adds MP4-friendly flags such as `+faststart` for streaming-optimized outputs.

**Usage**
```bash
vidcut INPUT [INPUT ...] \
       [--out OUTPUT.mp4] \
       [--suffix _cut] \
       [--from START] [--to END] [--duration DURATION] \
       [--trim-start SECONDS] [--trim-end SECONDS] \
       [--accurate | --fast] \
       [--overwrite] [--dry-run] [--ffmpeg PATH] [--doctor]
```

**Common scenarios**
```bash
# Keep a 12 second clip (fast copy mode)
vidcut clip.mp4 --from 00:00:05 --duration 12s --overwrite

# Batch trim all MOV files, append suffix, and overwrite existing clips
vidcut "videos/*.mov" --trim-start 3s --suffix _trimmed --overwrite

# Force frame-accurate cutting
vidcut input.mp4 --from 1m --duration 5s --accurate --overwrite

# Preview the ffmpeg command without running it
vidcut input.mp4 --from 10s --duration 15s --dry-run

# Ensure FFmpeg is available (downloads via imageio-ffmpeg if needed)
vidcut --doctor
```

### pydown — grab python-build-standalone releases
`pydown` automates downloading python-build-standalone artifacts from GitHub, selecting the right CPU/OS triplet and variant, and optionally extracting the archive for you.

**Highlights**
- Detects the correct `python-build-standalone` triplet for Linux (glibc/musl), macOS, and Windows hosts, with manual overrides when you need them.
- Supports picking variants (`install_only_stripped`, `install_only`, `full`, `debug`) and specific Python versions.
- Can extract archives in place and create helpful shims to add the installed Python to your PATH.
- Works with anonymous GitHub access or an optional `GITHUB_TOKEN` for higher rate limits.

**Usage**
```bash
pydown --dest PATH \
       [--version 3.12.6] \
       [--variant install_only_stripped] \
       [--extract] \
       [--triplet aarch64-apple-darwin]
```

**Common scenarios**
```bash
# Download the latest CPython build for your platform and extract it
pydown --dest ~/python-builds --extract

# Grab Python 3.12.6 and unpack it into a versioned directory
pydown --dest ./pbs --version 3.12.6 --extract

# Override the platform triplet (useful for cross-deployment scripting)
pydown --dest ./artifacts --triplet x86_64-unknown-linux-gnu
```

### pyarc — pack and unpack tar.gz archives
`pyarc` creates and extracts `.tar.gz` archives with progress output. It uses external `pigz` and `tar` for speed.

**Highlights**
- Fast gzip compression via `pigz` with configurable threads.
- Exact excludes and flexible rules (`--exclude` / `--exclude-re`).
- Progress bars and throughput reporting for pack/unpack operations.

**Usage**
```bash
pyarc pack SRC ARCHIVE.tar.gz \
      [--exclude NAME ...] \
      [--exclude-re RULE ...] \
      [--threads N] \
      [--level 1..9]

pyarc unpack ARCHIVE.tar.gz DEST_DIR \
      [--threads N]
```

**Example**
```bash
pyarc pack ./project ./project.tar.gz --exclude .git --exclude-re "*.pyc"
```

## Configuration & environment variables
- `DUKATOOLS_FFMPEG` — absolute path to an FFmpeg binary. Overrides auto-detection for `vidcut`.
- Standard locale and encoding settings (e.g., `LANG`, `LC_ALL`) influence how output is rendered in your terminal.

## Development
Interested in hacking on `dukatools`? Clone the repository and install local dependencies with your preferred workflow. A concise developer walkthrough lives in [DEV.md](DEV.md), covering how to add or remove CLI tools, build wheels, and publish releases.

## License
`dukatools` is released under the MIT License. See [LICENSE](LICENSE) for the full text.
