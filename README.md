# dukatools

> A small, batteries-included command line toolbox for working with directories and media across Linux, macOS, and Windows.

## Table of contents
- [Features at a glance](#features-at-a-glance)
- [Installation](#installation)
- [Upgrading](#upgrading)
- [Quick start](#quick-start)
- [CLI utilities](#cli-utilities)
  - [duka — run dukatools tools](#duka--run-dukatools-tools)
  - [treex — directory trees with include/exclude filters](#treex--directory-trees-with-includeexclude-filters)
  - [dircat — batch dump directory files](#dircat--batch-dump-directory-files)
  - [vidcut — fast and accurate video trimming](#vidcut--fast-and-accurate-video-trimming)
  - [pydown — grab python-build-standalone releases](#pydown--grab-python-build-standalone-releases)
  - [arc — pack and unpack tar.gz archives](#arc--pack-and-unpack-targz-archives)
- [Configuration & environment variables](#configuration--environment-variables)
- [Development](#development)
- [License](#license)

## Features at a glance
- **Cross-platform install** via `uv tool`, `pipx`, or `pip` — no manual PATH setup required.
- **Single launcher entry point** (`duka`) to run every tool (standalone binaries are intentionally not installed).
- **Tree inspection with include/exclude filters** via `duka treex`, ideal for sharing repository structure without build artifacts.
- **Directory dumping with filters** via `duka dircat` for quick audits, backups, and reviews.
- **FFmpeg-powered video trimming** via `duka vidcut` with automatic fallback from stream-copy to frame-accurate cuts.
- **One-file Python downloads** via `duka pydown` for fetching and unpacking python-build-standalone releases.
- **Archive packing/unpacking** via `duka arc` for progress-aware `.tar.gz` workflows, with fast `pigz` acceleration when available and Python fallback otherwise.
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

All installation methods place only the `duka` entry point on your PATH. Every tool is invoked as `duka <tool> ...`.

## Upgrading
Stay current with the latest enhancements and fixes:

```bash
uv tool upgrade dukatools
# or
pipx upgrade dukatools
# or
python -m pip install --upgrade dukatools
```

From version `0.4.1` onward, `dukatools` installs **only** the `duka` launcher. If you still see old standalone binaries like `treex` or `dircat` on your PATH after upgrading, uninstall and reinstall the package (for `pipx`, `pipx uninstall dukatools` + `pipx install dukatools`).

## Quick start
```bash
# Explore a repository without build artifacts
$ duka treex . --exclude .git build --exclude-re "*.pyc"

# Dump selected Markdown files from a directory into stdout (recursively)
$ duka dircat ./notes --include-re "*.md" --exclude archive

# Trim the first five seconds off a video without re-encoding
$ duka vidcut sample.mp4 --trim-start 5s --overwrite

# Download and extract a python-build-standalone release into ~/python-builds
$ duka pydown --dest ~/python-builds --version 3.12 --extract

# Pack a project into a .tar.gz with excludes
$ duka arc pack ./project ./project.tar.gz --exclude .git --exclude-re "*.pyc"
```

Tools are launcher-only. Use `duka <tool>` and `duka <tool> --help` for full usage.

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

### treex — directory trees with include/exclude filters
`treex` renders a directory structure in a friendly tree format while supporting exact path filters and glob-style path patterns. Invoke it as `duka treex`.

**Highlights**
- Exclude generated folders such as `__pycache__`, `build`, or `node_modules` with a single command.
- Use `--include` or `--include-re` to show only selected files or subtrees.
- Handles deeply nested projects and gracefully reports permission errors.
- Works great for quickly sharing repository layouts in issues, documentation, or chats.

**Usage**
```bash
duka treex PATH \
      [--include PATH ...] \
      [--exclude NAME ...] \
      [--include-re GLOB ...] \
      [--exclude-re GLOB ...]
```

If `PATH` is omitted, `duka treex` uses the current directory.

Exact filter paths are resolved relative to the inspected `PATH` unless they are absolute. `--exclude-re` and `--include-re` are glob-style POSIX path patterns inside that root. For recursive name containment, write the wildcard explicitly, for example `--exclude-re "*__pycache__*"`. If include rules are present, exclude rules still win.

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
`dircat` walks a directory, printing every file it finds to stdout with a clear header for each file. Invoke it as `duka dircat`.

**Highlights**
- Recursive by default for quick directory audits.
- Combine `--include`, `--include-re`, `--exclude`, and `--exclude-re` to keep only the files you need.
- Prints UTF-8 text and replaces undecodable bytes when needed.
- Perfect for quickly packaging logs, notes, or configuration snapshots for debugging.

**Usage**
```bash
duka dircat ROOT_DIR \
       [--include PATH ...] \
       [--exclude PATH ...] \
       [--include-re GLOB ...] \
       [--exclude-re GLOB ...]
```

Exact filter paths are resolved relative to `ROOT_DIR` unless absolute. Glob-style `*-re` patterns match POSIX paths inside `ROOT_DIR`; use `*__pycache__*` for recursive name containment.

**Writing to a file**
```bash
duka dircat ./config --include-re "*.toml" "*.yaml" --exclude secrets > artifacts/config-dump.txt
```

### vidcut — fast and accurate video trimming
`vidcut` wraps FFmpeg and provides a friendly interface for clipping one or more video files. It prefers stream-copy (no re-encode) for speed, then transparently falls back to a frame-accurate re-encode when necessary. Invoke it as `duka vidcut`.

**Highlights**
- Accepts flexible time formats (`90`, `45.5`, `00:01:02.300`, `5s`, `2m`, etc.).
- Supports batch processing using glob patterns (`"*.mp4"`).
- Automatically discovers FFmpeg: explicit path, `DUKATOOLS_FFMPEG`, bundled `imageio-ffmpeg`, or system PATH.
- Provides a `--doctor` command to inspect and pre-download the FFmpeg binary.
- Adds MP4-friendly flags such as `+faststart` for streaming-optimized outputs.

**Usage**
```bash
duka vidcut INPUT [INPUT ...] \
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
duka vidcut clip.mp4 --from 00:00:05 --duration 12s --overwrite

# Batch trim all MOV files, append suffix, and overwrite existing clips
duka vidcut "videos/*.mov" --trim-start 3s --suffix _trimmed --overwrite

# Force frame-accurate cutting
duka vidcut input.mp4 --from 1m --duration 5s --accurate --overwrite

# Preview the ffmpeg command without running it
duka vidcut input.mp4 --from 10s --duration 15s --dry-run

# Ensure FFmpeg is available (downloads via imageio-ffmpeg if needed)
duka vidcut --doctor
```

### pydown — grab python-build-standalone releases
`pydown` automates downloading python-build-standalone artifacts from GitHub, selecting the right CPU/OS triplet and variant, and optionally extracting the archive for you. Invoke it as `duka pydown`.

**Highlights**
- Detects the correct `python-build-standalone` triplet for Linux (glibc/musl), macOS, and Windows hosts, with manual overrides when you need them.
- Supports picking variants (`install_only_stripped`, `install_only`, `full`, `debug`) and specific Python versions.
- Can extract archives in place and create helpful shims to add the installed Python to your PATH.
- Works with anonymous GitHub access or an optional `GITHUB_TOKEN` for higher rate limits.

**Usage**
```bash
duka pydown --dest PATH \
       [--version 3.12.6] \
       [--variant install_only_stripped] \
       [--extract] \
       [--triplet aarch64-apple-darwin]
```

**Common scenarios**
```bash
# Download the latest CPython build for your platform and extract it
duka pydown --dest ~/python-builds --extract

# Grab Python 3.12.6 and unpack it into a versioned directory
duka pydown --dest ./pbs --version 3.12.6 --extract

# Override the platform triplet (useful for cross-deployment scripting)
duka pydown --dest ./artifacts --triplet x86_64-unknown-linux-gnu
```

### arc — pack and unpack tar.gz archives
`arc` creates and extracts `.tar.gz` archives with progress output. It uses external `tar` + `pigz` for speed when available, and falls back to Python gzip so the tool still works after only `uv tool install dukatools`. Invoke it as `duka arc`.

**Highlights**
- Fast gzip compression via `pigz` with configurable threads when installed.
- Built-in Python gzip fallback when `pigz` is missing.
- Exact include/exclude filters and glob-style rules (`--include`, `--exclude`, `--include-re`, `--exclude-re`).
- Progress bars and throughput reporting for pack/unpack operations.
- `duka arc doctor` shows which backend will be used and how to install `pigz`.

**Usage**
```bash
duka arc pack SRC ARCHIVE.tar.gz \
      [--include PATH ...] \
      [--exclude PATH ...] \
      [--include-re GLOB ...] \
      [--exclude-re GLOB ...] \
      [--threads N] \
      [--level 1..9] \
      [--backend auto|system|python]

duka arc unpack ARCHIVE.tar.gz DEST_DIR \
      [--threads N] \
      [--backend auto|system|python]
```

Exact filter paths are resolved relative to `SRC` unless absolute. Glob-style `*-re` patterns match POSIX paths inside `SRC`. If include rules are present, only matching paths are archived; exclude rules still win.

When `pigz` is not installed, `duka arc pack` and `duka arc unpack` print a prominent warning and use the Python fallback. Install `pigz` for best performance:
```bash
sudo apt install pigz      # Ubuntu/Debian
brew install pigz          # macOS
sudo dnf install pigz      # Fedora
sudo pacman -S pigz        # Arch
```

**Example**
```bash
duka arc pack ./project ./project.tar.gz --include src pyproject.toml --exclude-re "*.pyc"
```

## Configuration & environment variables
- `DUKATOOLS_FFMPEG` — absolute path to an FFmpeg binary. Overrides auto-detection for `vidcut`.
- Standard locale and encoding settings (e.g., `LANG`, `LC_ALL`) influence how output is rendered in your terminal.

## Development
Interested in hacking on `dukatools`? Clone the repository and install local dependencies with your preferred workflow. A concise developer walkthrough lives in [DEV.md](DEV.md), covering how to add or remove CLI tools, build wheels, and publish releases.

## License
`dukatools` is released under the MIT License. See [LICENSE](LICENSE) for the full text.
