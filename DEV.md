# Developer Guide

## Add a new CLI
1) Create file: `src/dukatools/<tool>.py` with `def main(): ...`
2) Add the tool to the `TOOLS` map in `src/dukatools/duka.py`
3) Update `README.md` (features, quick start, CLI utilities)
4) Ensure runtime dependencies are listed in `pyproject.toml`
5) Bump version in `pyproject.toml` (e.g., 0.1.0 -> 0.2.0)
6) Build & test locally
7) Tag & push to GitHub

## Remove a CLI
1) Remove the tool from the `TOOLS` map in `src/dukatools/duka.py`
2) Remove or archive the module in `src/dukatools/`
3) Update `README.md` (features, quick start, CLI utilities)
4) Remove any now-unused runtime dependencies from `pyproject.toml`
5) Bump version in `pyproject.toml`
6) Build & test locally
7) Tag & push to GitHub

## Local build & test
python -m pip install -U build twine
python -m build
pipx install dist/dukatools-<ver>-py3-none-any.whl
duka treex --help
python -m twine upload dist/*
