# Design decisions

This file is the durable record of the decisions that were settled *before* the
first line of code was written. It exists so that a later reader can tell the
difference between "we thought about this and chose X" and "nobody noticed".

`SPEC.md` remains the source of truth for the product. This file records where
we deliberately deviate from it, and why.

## Project identity

| Decision | Choice | Notes |
| --- | --- | --- |
| Distribution, import and CLI name | `pypackhub` | The repo directory is `PyPackHub` and the spec title says *PyPackHub*, but the spec body repeatedly writes `pyphackhub` — clearly a typo propagated through the document. Both names were free on PyPI at the time of writing. |
| Display name | `PyPackHub` | Window titles, about dialog, README headings. |
| Version | `0.1.0`, classifier `Development Status :: 2 - Pre-Alpha` | Nothing is released yet. |
| License | GPL-3.0-only | The repository already carried the GPL-3.0 text in `LICENSE`. PySide6 is LGPLv3/GPLv3, so the combination is compatible. |
| Repository layout | `src/pypackhub/` | The spec diagram shows a flat `pyphackhub/`; `src/` guarantees tests import the installed distribution instead of the checkout. |
| Build backend | Hatchling | Modern, handles `src/` layout and package data (the bundled fonts) with almost no configuration. |

## Environment and tooling

| Decision | Choice | Notes |
| --- | --- | --- |
| Minimum Python | `>=3.10` | **Deviation from the spec**, which asks for 3.9+. PySide6 6.11 declares `requires_python = ">=3.10,<3.15"`, so 3.9 is unreachable. |
| CI Python matrix | 3.10, 3.11, 3.12, 3.13 | 3.14 is allowed by PySide6 but deliberately left out of the matrix until the ecosystem settles. |
| Dev install | `python -m pip install --user -e ".[dev]"` | No virtual environment on the maintainer's machine; the editable install makes the console entry point and metadata available. |
| Dependency pinning | `requirements.lock` + `requirements-dev.lock`, generated with `uv pip compile --universal --python-version 3.10 --generate-hashes` | Universal resolution means one lock file valid on all three platforms and every supported interpreter. Hashes make the locks tamper-evident, let `pip-audit --disable-pip` audit exactly the pinned set without re-resolving, and make `pip install --require-hashes` possible now instead of in Phase 4. CI regenerates and diffs them. |
| Lint/format/type | ruff (incl. `S` = bandit rules), black, mypy, bandit | mypy is strict across `core/` and slightly relaxed for `ui/`, where Qt's signal/slot typing is noisy. |
| Fonts | Bundled, static TTFs, weights Regular/Medium/SemiBold/Bold | Variable fonts behave inconsistently across Qt platforms. Inter + JetBrains Mono + Vazirmatn, all OFL, license files shipped next to them. |
| Icons | Hand-authored SVG set | Avoids adding `qtawesome` (an extra dependency the spec does not list) and keeps per-theme recolouring simple. |
| Documentation language | English | The application UI is bilingual by design; a `README.fa.md` can be added later without touching anything else. |

## CI strategy

| Decision | Choice | Notes |
| --- | --- | --- |
| On every push | Linux, Python 3.10-3.13, plus the security workflow | Fast feedback. **Deviation from the spec**, which asks for the full matrix on every push — that costs 2x on Windows and 10x on macOS runners for a repo that may be private. |
| On pull request / tag / manual run | Full Windows + macOS matrix (Python 3.10 and 3.13) | The spec's cross-platform requirement is still honoured, just not on every single push. |
| Build workflows | A minimal build job in section 5, full release/signing in Phase 4 | PyInstaller/Briefcase packaging belongs with the release work, not with the scaffold. |

## Runtime behaviour

| Decision | Choice | Notes |
| --- | --- | --- |
| "Latest version" / "outdated" | Fast local metadata first (<1s for 300 packages), then parallel PyPI lookups resolving progressively in the background with a TTL cache | PyPI has no batch endpoint, so the spec's "<3s for 300 packages" cannot include a network round trip per package. Matches the spec's own "scan streams results progressively" requirement. |
| Package size on disk | Computed lazily in a worker, cached | Dist metadata (`RECORD`) lists paths, not sizes, so each size costs `stat()` calls. Doing that eagerly would blow the startup budget. |
| Default target environment | The interpreter the application runs under. If it is a system / PEP 668 managed interpreter, the UI is **read-only** and offers a "create a virtual environment" call to action | The spec's safety rules make this the only honest default; silently falling back to `--break-system-packages` is forbidden. |
| Created environment location | Chosen by the user (folder picker, `.venv` suggested) with a base-interpreter picker | Matches what Python developers expect and keeps environments discoverable by other tools. |
| Landing view | Packages | The Dashboard's content is Phase 2; Phase 1 is the package table. |
| Unbuilt navigation entries | Hidden | The spec lists ten sidebar entries; showing entries whose views do not exist would be a silent stub. They appear as each view is built. |

## Process

| Decision | Choice | Notes |
| --- | --- | --- |
| Delivery | Phase 1 is split into six sections; after each section the maintainer commits and pushes manually | Keeps every pushed commit reviewable and keeps the working tree in a known-good state. |
| Verification per section | `pytest` plus an offscreen Qt smoke test, with copy-paste instructions for the maintainer to launch the GUI | Nothing is reported as finished without a run that produced output. |
| Entry points | The `pypackhub` console script is added in section 3, once there is an application to launch | Adding it now would install a command that does nothing. |

## Open items

- `pyproject.toml` uses the placeholder author `PyPackHub contributors`; the real
  name and email still need to be filled in.
- `SPEC.md` is written in the second person singular and sometimes refers to
  `pyphackhub`; it is kept verbatim in the repository as the product reference.

## Section plan for Phase 1

| Section | Contents | Gate |
| --- | --- | --- |
| 0 | Scaffold: `pyproject.toml`, `src/` layout, lock files, bundled fonts, docs, CI and security workflows | Editable install works, every check passes |
| 1 | Security core: `runner`, `validation`, `secrets`/redaction, the security test-suite, the project's banned-pattern scanner | All section 11.9 cases pass |
| 2 | Core: settings, logging, environment detection and system info, installed-package listing, sizes, PyPI client, task queue, `pip` and `uv` backends | Unit tests with mocked subprocess output, plus a real run against this machine |
| 3 | UI foundation: design tokens and QSS themes, i18n and RTL direction management, window shell, settings persistence | Offscreen smoke test, live theme and language switching |
| 4 | Packages view: table, search/filter/sort, detail panel, confirmation dialog, live terminal with cancel, install/uninstall/upgrade/downgrade/upgrade-all | Offscreen smoke test plus a manual run |
| 5 | Settings page, the PEP 668 "create a virtual environment" flow, workflow finalisation, README completion | The Phase 1 acceptance checklist from SPEC section 13 |

Phases 2 to 4 are split the same way once Phase 1 is done and reviewed.
