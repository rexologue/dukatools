# Developer Guide

Everything below uses [uv](https://docs.astral.sh/uv/). The build backend stays
`poetry-core` (see `pyproject.toml`); uv drives it through the standard PEP 517 interface.

## Dev setup

```bash
git clone https://github.com/rexologue/dukatools && cd dukatools
uv tool install --editable .   # `duka` on PATH, runs straight from this checkout
duka --version
```

With `--editable`, code changes apply immediately, no reinstall needed. Only
reinstall (`uv tool install --editable . --force`) after changing dependencies or
`[project.scripts]` in `pyproject.toml`.

## Run tests

```bash
uv run python -m unittest discover -s tests -v
```

`uv run` creates `.venv/` (git-ignored) and `uv.lock` on first use.

Known issue: `tests/test_arc_selection.py::test_selection_uses_source_relative_filters`
fails on macOS, where `/var` is a symlink to `/private/var`.

## Add a new CLI
1) Create file: `src/dukatools/<tool>.py` with `def main(): ...` (return an int exit code)
2) Add the tool to the `TOOLS` map in `src/dukatools/duka.py`
3) Add tests in `tests/test_<tool>.py`
4) Update `README.md` (table of contents, features, quick start, CLI utilities)
5) Add runtime dependencies with `uv add <package>` (updates `pyproject.toml`)
6) Bump the version (see below)
7) Test, build, release

## Remove a CLI
1) Remove the tool from the `TOOLS` map in `src/dukatools/duka.py`
2) Remove the module from `src/dukatools/` and its tests
3) Update `README.md`
4) Drop now-unused dependencies with `uv remove <package>`
5) Bump the version, test, build, release

## Versioning

```bash
uv version --short                 # current version
uv version --bump minor            # 0.6.0 -> 0.7.0 (new tool or feature)
uv version --bump patch            # 0.6.0 -> 0.6.1 (fixes)
uv version --bump minor --dry-run  # preview only
```

`duka --version` reads the same value from `pyproject.toml`
(`tests/test_duka_launcher.py` checks this).

## Build and check locally

```bash
rm -rf dist/
uv build                                   # dist/dukatools-<ver>.tar.gz + .whl
uv tool install --force dist/dukatools-*-py3-none-any.whl
duka --version && duka --list              # smoke-test the real wheel
uv tool install --editable . --force       # back to the dev install
```

## Publish

Create an API token on PyPI (Account settings → API tokens), then:

```bash
export UV_PUBLISH_TOKEN=pypi-...           # or pass --token
uv publish                                 # uploads everything in dist/
```

Optional dry run on TestPyPI (separate account and token):

```bash
uv publish --publish-url https://test.pypi.org/legacy/ --token pypi-...
uv tool install --index https://test.pypi.org/simple/ --index-strategy unsafe-best-match dukatools
```

From GitHub Actions, `uv publish` can use
[trusted publishing](https://docs.pypi.org/trusted-publishers/) instead of a token.

## Tag and push

```bash
git commit -am "Release <ver>"
git tag v<ver>
git push && git push --tags
```

Users then upgrade with `uv tool upgrade dukatools`.
