# Developer Guide

## Add a new CLI
1) Create file: src/toolskit/<tool>.py with `def main(): ...`
2) Register entry point in pyproject.toml under [project.scripts]: `<tool> = "toolskit.<tool>:main"`
3) Bump version in pyproject.toml (e.g., 0.1.0 -> 0.2.0)
4) Build & test locally
5) Tag & push to GitHub (CI will publish to PyPI)

## Local build & test
python -m pip install -U build twine
python -m build
pipx install dist/toolskit-<ver>-py3-none-any.whl
treex --help
