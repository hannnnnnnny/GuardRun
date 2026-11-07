# Contributing

1. Create a virtual environment and install dev dependencies:
   `python -m venv .venv && .venv/bin/pip install -e ".[dev]"`
2. Keep changes small and focused; one logical change per commit.
3. Use Conventional Commit prefixes (`feat:`, `fix:`, `test:`, `docs:` ...).
4. Before opening a PR run `pytest --cov`, `ruff check .`, `ruff format --check .` and `mypy`.
   `pre-commit install` runs the lint and type checks on every commit.
5. Any change to the safety layer needs tests that exercise the bypass it prevents.
   New attack patterns belong in `tests/adversarial/`, written as failing tests first.
6. Edit text files as UTF-8 (on Windows, set `PYTHONUTF8=1` for scripts);
   `tests/test_encoding.py` rejects double-encoded text.

## Releasing

1. Bump `version` in `pyproject.toml` and `__version__` in
   `src/guardrun/__init__.py`, and move the `Unreleased` changelog entries
   under the new version.
2. Merge to `main`, then push a `vX.Y.Z` tag. The release workflow checks the
   tag matches the version, builds the wheel, sdist, and Windows executable,
   smoke-tests the executable, and publishes them as a GitHub release.

To build the executable locally on Windows (use a clean virtual environment;
PyInstaller bundles whatever is installed):

```bash
pip install . pyinstaller
python packaging/build_exe.py
```
