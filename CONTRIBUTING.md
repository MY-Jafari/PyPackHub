# Contributing to PyPackHub

Thanks for wanting to help. This guide covers everything needed to get a working
checkout and to keep the security and architecture guarantees intact.

## Development setup

PyPackHub targets **Python 3.10 to 3.14** (PySide6 6.11 supports
`>=3.10,<3.15`). Any of the supported versions works; CI also runs the full
matrix, so there is no need to install several of them locally.

```bash
git clone https://github.com/MY-Jafari/PyPackHub.git
cd PyPackHub

# A virtual environment is strongly recommended; --user also works.
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

python -m pip install -e ".[dev]"
```

`uv` is faster and is what the lock files are generated with:

```bash
uv venv && uv pip install -e ".[dev]"
```

## The commands CI runs

Run these before opening a pull request. They are exactly what the workflows
execute, so a green local run means a green CI run.

```bash
ruff check .
black --check .
mypy
pytest
pytest -m security
bandit -c pyproject.toml -r src
python scripts/security_scan.py
```

Tests use Qt's offscreen platform plugin (set automatically by
`tests/conftest.py`), so they work without a display server — including on CI
runners and over SSH. Set `PYTEST_QT_API=pyside6` if you have more than one Qt
binding installed and want to be explicit about which one the Qt tests use.

`scripts/security_scan.py` is the project's own static check. It is AST based,
so it sees through formatting and through `from os import system` style imports.
It is the reason there is exactly one `subprocess` import in the codebase.

## Dependency locks

`pyproject.toml` holds loose ranges; `requirements.lock` and
`requirements-dev.lock` hold the pinned, universal resolution. After touching
dependencies, regenerate them and commit the result:

```bash
bash scripts/generate_locks.sh
```

CI regenerates them again and fails if the committed files differ, so never edit
the lock files by hand.

## Architecture rules

These are enforced by tests, not by convention:

1. **`pypackhub/core` never imports a GUI toolkit or `pypackhub.ui`.**
   `tests/test_architecture.py` fails the build if it happens. The core must
   stay usable from a terminal, from tests, and from the CLI companion.
2. **Every subprocess goes through `pypackhub.core.runner`.** Nothing else in
   the codebase may spawn a process, and no call site may use `shell=True`,
   `os.system` or a string-concatenated command line.
3. **All external input is validated** in `pypackhub.core.validation` before it
   reaches a backend: package specs, paths, environment names, index URLs and
   the contents of imported requirements/`pyproject`/`environment.yml` files.
4. **The UI never talks to pip, uv or conda directly.** It asks a backend, the
   backend asks the runner.

## Adding a language

Adding a language should mean adding exactly one file:

1. Copy `src/pypackhub/ui/i18n/en.json` to `<code>.json` and translate the
   values (never the keys).
2. Register the language in the i18n loader.
3. Add the locale to the language switcher.

No string may be hard-coded in a widget. Persian is the reference for a
right-to-left layout; check that package names, paths, commands and log output
stay left-to-right inside an RTL layout, using Unicode isolation where needed.

## Commit messages

Short imperative summary line, then a body explaining *why* when the reason is
not obvious. Example:

```
Reject leading dashes in package specs

Option injection through the name field could redirect an install to an
attacker-controlled index, so the validator now refuses any spec that starts
with "-" before it reaches a backend.
```

## Pull requests

- Keep the change focused; unrelated refactors belong in their own pull request.
- Add or update tests. A security-relevant change without a test will be asked
  to add one.
- Update `CHANGELOG.md` under `Unreleased`.
- Link the issue you are solving, and describe what you verified manually.
- Never commit a lowered security guarantee (an allow-listed exception, a
  disabled check, a skipped test) without discussion in the pull request.

## Reporting a problem

Bugs and feature requests go to
<https://github.com/MY-Jafari/PyPackHub/issues>. Security issues must **not** be
reported publicly — see [SECURITY.md](SECURITY.md).
