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
| Unbuilt navigation entries | Visible but disabled, with a tooltip and a placeholder page that names the phase which builds them | **Changed in section 3.** The original plan hid them; an empty sidebar tells the user nothing about where a feature will live, and the placeholder page says the same thing in the window. The alternative — an enabled entry that opens a fake screen — is a silent stub, which the spec forbids. |

## Process

| Decision | Choice | Notes |
| --- | --- | --- |
| Delivery | Phase 1 is split into six sections; after each section the maintainer commits and pushes manually | Keeps every pushed commit reviewable and keeps the working tree in a known-good state. |
| Verification per section | `pytest` plus an offscreen Qt smoke test, with copy-paste instructions for the maintainer to launch the GUI | Nothing is reported as finished without a run that produced output. |
| Entry points | The `pypackhub` console script (and `python -m pypackhub`) were added in section 3, once there was an application to launch | Both call the same `pypackhub.ui.app:run`, and a test resolves the declared target so a typo in `pyproject.toml` cannot ship. |
| Stylesheets | Generated from the design tokens at import time instead of two checked-in `.qss` files | SPEC section 4 lists `dark.qss`/`light.qss`. Hand-maintained sheets drift from the tokens they are supposed to implement, and two of them drift from each other; `build_qss()` produces exactly the sheets the spec asks for from the single source of truth. |
| Settings location | One `settings.ini` in the platformdirs application data directory, written through `QSettings` | The registry/plist alternatives scatter the value over keys that cannot be inspected or permission-restricted. INI keeps settings, logs and cache under one directory with user-only permissions (SPEC section 11.6). |

## Notes on section 1

**Modules added beyond the spec's list.** The spec's architecture (section 4)
lists `pypi.py` under core but no shared HTTP layer. One was added as
`core/net.py`, because "HTTPS only, no `verify=False`, timeouts, size limits"
is a property of a client, not of a call site - putting it in `pypi.py` would
leave every later caller free to create a raw `httpx` client and opt out.
`core/platform_guard.py` moved from section 2 into section 1 for the same
reason: the Phase 1 acceptance criteria require system-Python protection to be
verifiable now, and detection is a policy question that section 2's discovery
logic should consume rather than duplicate.

**Two of the section 11.9 tests are deliberately not in Phase 1.**

| Test | Where it belongs | Why not now |
| --- | --- | --- |
| Typosquatting: `reqeusts`, `numpyy`, `pandsa` warn | Phase 2 (`trust.py`, per section 5) | Needs the bundled popular-package list and the trust panel. Building it now would be skipping ahead of the roadmap the spec sets out. |
| Vulnerability scan renders a mocked OSV response and snapshots before fixing | Phase 4 (`security.py`) | Depends on `snapshots.py`, which arrives in Phase 4. |

Everything else in 11.9 that Phase 1 owns is implemented and tested:
injection and homoglyph rejection, the static scan, path and deletion
refusals, the imported-requirements review rules, log redaction, HTTPS-only
networking and system-Python protection.

**Bandit's B404 and B603 are skipped** in `[tool.bandit]`. They are superseded
by the project's own scanner, which allows the `subprocess` import in exactly
one audited module instead of emitting a warning about it everywhere. B602
(`shell=True`) and B607 (partial executable path) remain enabled.

## Open items

- `pyproject.toml` uses the placeholder author `PyPackHub contributors`; the real
  name and email still need to be filled in.
- `SPEC.md` is written in the second person singular and sometimes refers to
  `pyphackhub`; it is kept verbatim in the repository as the product reference.
- Hashes in imported requirements files are recognised but not enforced;
  `parse_requirements` flags a hash-pinned line for review instead of quietly
  applying it as an unpinned install. Enforcement arrives with the
  `--require-hashes` work in Phase 4.

## Section plan for Phase 1

| Section | Contents | Gate |
| --- | --- | --- |
| 0 | Scaffold: `pyproject.toml`, `src/` layout, lock files, bundled fonts, docs, CI and security workflows | Editable install works, every check passes |
| 1 | ✅ Security core: `runner`, `validation`, `platform_guard`, `secrets`/redaction, `net`, the security test-suite, the project's banned-pattern scanner | The Phase 1 subset of section 11.9 passes - see the note below |
| 2 | Core: settings, logging, environment detection and system info, installed-package listing, sizes, PyPI client, task queue, `pip` and `uv` backends | Unit tests with mocked subprocess output, plus a real run against this machine |
| 3 | ✅ UI foundation: design tokens and QSS themes, i18n and RTL direction management, window shell, settings persistence, console entry point | Offscreen smoke test, live theme and language switching |
| 4 | Packages view: table, search/filter/sort, detail panel, confirmation dialog, live terminal with cancel, install/uninstall/upgrade/downgrade/upgrade-all | Offscreen smoke test plus a manual run |
| 5 | Settings page, the PEP 668 "create a virtual environment" flow, workflow finalisation, README completion | The Phase 1 acceptance checklist from SPEC section 13 |

Phases 2 to 4 are split the same way once Phase 1 is done and reviewed.

## Notes on section 3

**The theme switch is one call, not a widget walk.** `WindowShell.apply_to()`
hands the whole generated stylesheet to the `QApplication`, and Qt re-resolves it
for every widget. That is what makes the switch live without a single widget
knowing a theme exists, and it is why the tokens are data rather than QSS
fragments.

**The stylesheet is built per layout direction, for two rules.** Qt mirrors widget
order and alignment when the application's direction is right-to-left, but a rule
that says `border-right` is not a logical edge - it stays on the right. So the
sidebar divider and the navigation label alignment are emitted for the direction
in force, which is the difference between "the text is Persian" and the layout
the spec asks for ("sidebar right, ... directional icons").

**The sidebar lists all ten destinations from section 3.** The navigation
structure is a property of the shell, not of the views, so it is complete on the
day the shell is written: the seven destinations whose views arrive later are
disabled and each opens a placeholder naming the phase that builds it.

**Two variants, one role set.** `tests/ui/test_design_tokens.py` asserts both
variants expose the same fields and that primary text clears a 4.5:1 contrast
ratio against every surface. A role added to one variant only is a test failure,
which is the cheapest possible time to notice.

**The Persian table is watched for two specific mistakes.** The first draft of
this table contained Thai and Chinese fragments; the test-suite now scans the
Persian column for characters from other scripts, checks that every English key
has a Persian string, and checks that the two languages use the same
placeholders. All three caught real bugs when they were written.

**"Follow system" is resolved by Qt, not by the environment.** `system_variant()`
asks `QGuiApplication.styleHints().colorScheme()`; with no running application it
falls back to light rather than guessing from `LANG`. The language setting,
meanwhile, intentionally does *not* guess: `SYSTEM` renders English, because the
UI is authored in English and a machine whose locale happens to match half a
translation table would otherwise show a half-translated window.

**The Persian UI is set in Vazirmatn and the Latin UI in Inter.** The font family
is switched with the layout direction instead of relying on glyph fallback, so
mixed Persian/English text picks the right face deliberately.

**One bandit `# nosec`.** Bandit's B604 heuristic flags any call with a keyword
argument named `shell=`; `MainWindow(shell=...)` is PyPackHub's window-shell
object, not a command shell. The suppression is on that single line, with the
reason, rather than disabling the check for the file.

**Three latent defects were found while building this section and fixed in the
same pass**, because a section gate is "every check passes" and none of them was
reachable without a window: `settings_data_directory()` and
`ensure_log_directory()` called `platformdirs.user_data_dir()` with keyword
arguments it does not accept (the first settings write would have crashed),
`pypi.py` used `datetime.monotonic` and caught a function as an exception type,
and `backends/pip.py`/`uv.py` swallowed every exception while looking for their
executable. `pypi.py` also now implements the context-manager protocol it was
already being used with by `pypi_info()`.
