# dukatools

> A small, batteries-included command line toolbox for working with directories and media across Linux, macOS, and Windows.

## Table of contents
- [Features at a glance](#features-at-a-glance)
- [Installation](#installation)
- [Upgrading](#upgrading)
- [Quick start](#quick-start)
- [CLI utilities](#cli-utilities)
  - [treex — directory trees with excludes](#treex--directory-trees-with-excludes)
  - [dirproc — batch dump directory files](#dirproc--batch-dump-directory-files)
  - [vidcut — fast and accurate video trimming](#vidcut--fast-and-accurate-video-trimming)
  - [pydown — grab python-build-standalone releases](#pydown--grab-python-build-standalone-releases)
- [Configuration & environment variables](#configuration--environment-variables)
- [Development](#development)
- [License](#license)

## Features at a glance
- **Cross-platform binaries** via `uv tool`, `pipx`, or `pip` — no manual PATH setup required.
- **Tree inspection with smart excludes** through `treex`, ideal for sharing repository structure without build artifacts.
- **Directory dumping** with on-the-fly encoding detection (`dirproc`) for audits, backups, and quick reviews.
- **FFmpeg-powered video trimming** (`vidcut`) with automatic fallback from stream-copy to frame-accurate cuts.
- **One-file Python downloads** (`pydown`) for fetching and unpacking python-build-standalone releases without manual API spelunking.
- **Zero-config defaults** plus optional environment overrides when you need extra control.

## Installation
`dukatools` is distributed as a standard Python package targeting Python 3.8+.

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

All installation methods place the `treex`, `dirproc`, `vidcut`, and `pydown` entry points on your PATH.

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
$ treex --path . --exclude .git --exclude-pattern "*.pyc" "build"

# Dump every text file in a directory into stdout (recursively)
$ dirproc ./notes --exclude-pattern "^archive/"

# Trim the first five seconds off a video without re-encoding
$ vidcut sample.mp4 --trim-start 5s --overwrite

# Download and extract a python-build-standalone release into ~/python-builds
$ pydown --dest ~/python-builds --version 3.12 --extract
```

## CLI utilities

### treex — directory trees with excludes
`treex` renders a directory structure in a friendly tree format while supporting both exact-name excludes and glob patterns.

**Highlights**
- Exclude generated folders such as `__pycache__`, `build`, or `node_modules` with a single command.
- Handles deeply nested projects and gracefully reports permission errors.
- Works great for quickly sharing repository layouts in issues, documentation, or chats.

**Usage**
```bash
treex --path PATH \
      [--exclude NAME ...] \
      [--exclude-pattern GLOB ...]
```

**Example output**
```
Directory tree for: ./project
Excluded patterns: *.pyc
├── pyproject.toml
├── README.md
└── src
    ├── __init__.py
    └── project
        └── core.py
```

### dirproc — batch dump directory files
`dirproc` walks a directory, opening each text file, detecting its encoding (via `chardet`), and streaming the content either to stdout or to a UTF-8 file you specify.

**Highlights**
- Recursive by default, with `--non-recursive` available for shallow inspections.
- Combine `--exclude-name` and `--exclude-pattern` (regex) to skip sensitive or noisy paths.
- Emits friendly messages when files are unreadable, including the error reason.
- Perfect for quickly packaging logs, notes, or configuration snapshots for debugging.

**Usage**
```bash
dirproc ROOT_DIR \
        [--output-file OUTPUT.txt] \
        [--non-recursive] \
        [--exclude-name NAME ...] \
        [--exclude-pattern REGEX ...]
```

**Writing to a file**
```bash
dirproc ./config --output-file artifacts/config-dump.txt --exclude-pattern "\.git/"
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

## Configuration & environment variables
- `DUKATOOLS_FFMPEG` — absolute path to an FFmpeg binary. Overrides auto-detection for `vidcut`.
- Standard locale and encoding settings (e.g., `LANG`, `LC_ALL`) influence how output is rendered in your terminal.

## Development
Interested in hacking on `dukatools`? Clone the repository and install local dependencies with your preferred workflow. A concise developer walkthrough lives in [DEV.md](DEV.md), covering how to add new CLI tools, build wheels, and publish releases.

## License
`dukatools` is released under the MIT License. See [LICENSE](LICENSE) for the full text.
