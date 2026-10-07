# PyPackHub

[![CI](https://github.com/MY-Jafari/PyPackHub/actions/workflows/ci.yml/badge.svg)](https://github.com/MY-Jafari/PyPackHub/actions/workflows/ci.yml)
[![Security](https://github.com/MY-Jafari/PyPackHub/actions/workflows/security.yml/badge.svg)](https://github.com/MY-Jafari/PyPackHub/actions/workflows/security.yml)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](pyproject.toml)
[![Status: pre-alpha](https://img.shields.io/badge/status-pre--alpha-orange.svg)](CHANGELOG.md)

**One visual interface for every Python environment on your machine.**

PyPackHub is a cross-platform desktop application (Windows, Linux, macOS) for
looking at and managing Python packages and environments across all the major
package managers — pip, uv, conda/mamba, poetry, pipx, pipenv and pdm — instead
of remembering the exact command line of each one.

It is built for people who want to *see* what is installed, what is out of date,
what is vulnerable, what depends on what, and what is safe to remove — before
anything is changed, not after.

## Status

**Pre-alpha.** The project is being built section by section, in the open, and
nothing here is available as a release yet. This table is kept honest on
purpose:

| Capability | State |
| --- | --- |
| Project scaffold, dependency locks, CI, security workflow, docs | ✅ done |
| Hardened process runner, input validation, log redaction | 🚧 section 1 |
| Environment detection, package listing, pip/uv backends | 🚧 section 2 |
| Theme, translations (English + Persian RTL), window shell | 🚧 section 3 |
| Package table, install/uninstall/upgrade with confirmations and live log | 🚧 section 4 |
| Settings, PEP 668 protection flow, Phase 1 acceptance | 🚧 section 5 |
| Dependency graph, security scanning, snapshots, environment management | 📋 later phases |

The full plan, the phase breakdown and every decision taken so far live in
[`docs/DECISIONS.md`](docs/DECISIONS.md).

## Installing (from source)

A release is not published yet, so installation means running from a checkout.
Python 3.10–3.14 is required (PySide6 does not support anything older).

```bash
git clone https://github.com/MY-Jafari/PyPackHub.git
cd PyPackHub

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

python -m pip install -e ".[dev]"
```

`uv` users can replace the last line with `uv venv && uv pip install -e ".[dev]"`.

## Development

Everything CI runs, runnable locally:

```bash
ruff check .            # lint (includes the flake8-bandit rule set)
black --check .         # formatting
mypy                    # types (strict in core/, relaxed in ui/)
pytest                  # tests, using Qt's offscreen platform plugin
bandit -c pyproject.toml -r src
```

After changing dependencies, regenerate and commit the locks:

```bash
bash scripts/generate_locks.sh
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the architecture rules that tests
enforce, how to add a language, and the pull-request checklist.

## Architecture

```
src/pypackhub/
  core/          # headless: no Qt import is allowed here, enforced by tests
    runner.py       # the only place in the project that spawns a process
    validation.py   # all external input is validated before it is used
    backends/       # one class per package manager, one shared interface
    environments.py # discovery, create, clone, compare, purpose inference
    deps.py         # dependency graph, reverse dependencies, orphans
    pypi.py         # search, versions, metadata, TTL cache
    security.py     # vulnerability lookups
    trust.py        # typosquatting detection and package trust signals
    snapshots.py    # snapshot and rollback
    tasks.py        # job queue, dry-run, cancel, progress, history
  ui/            # PySide6 (Qt 6) presentation layer
    theme/        # design tokens, dark.qss, light.qss, theme manager
    i18n/         # en.json, fa.json, translator, RTL direction manager
    widgets/      # cards, badges, tables, graph view, terminal panel, toasts
    views/        # dashboard, packages, environments, dependencies, ...
  assets/        # icons, bundled fonts, popular-package list
tests/           # unit tests, plus tests/security for the security guarantees
```

Three rules hold the design together, and each is enforced by a test rather
than by discipline:

1. **`core` never imports a GUI toolkit or `ui`.** The CLI companion and the
   test-suite have to work without a display server.
2. **Every subprocess goes through `core/runner.py`**, with an argument list,
   a sanitized environment and a killable process tree. `shell=True`,
   `os.system` and string-concatenated commands are banned and scanned for.
3. **The UI never calls pip, uv or conda.** It asks a backend; the backend asks
   the runner.

## Security

Installing packages means running code, so the guarantees are spelled out and
tested rather than promised: hardened process execution, strict validation of
everything that comes from outside the application, typosquatting and
supply-chain warnings, read-only protection for system interpreters, HTTPS-only
networking, credentials in the OS keychain, and redaction of secrets out of
logs and diagnostics.

Read [SECURITY.md](SECURITY.md) for the full model, including what is
implemented today and how to report a vulnerability privately.

## Roadmap

| Phase | Theme | Highlights |
| --- | --- | --- |
| 1 | Foundation + security core | Environment detection, package table with status and sizes, install/uninstall/upgrade/downgrade, detail panel, confirmation dialogs showing the exact command, live cancellable log, dark/light themes, English + Persian RTL |
| 2 | Smart management + supply chain | Dependency tree and reverse dependencies, orphan clean-up, dry-run previews, conflict checks, PyPI browsing, trust panel and typosquatting warnings, import/export, disk-usage analysis, dashboard |
| 3 | Multi-environment, multi-manager | Discover and switch between every environment on the machine, create/clone/back up/delete, compare two environments, move packages between them, interpreter manager |
| 4 | Security scanning and power tools | Vulnerability scan with one-click fixes, snapshots and rollback, undo history, tags and pins, hash-pinned installs, command palette, app updates, CLI companion |

## Comparison with similar tools

A feature matrix against Pakku, PyScope, PyLite Manager, pip-gui and similar
projects is planned, but it will only be published once each claim has been
verified against the current release of each tool. Publishing an unverified
comparison would be exactly the kind of thing this project exists to avoid.

## License

GPL-3.0. See [LICENSE](LICENSE). Bundled fonts are under the SIL Open Font
License — see `src/pypackhub/assets/fonts/`.
