# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- The dependency graph and the Dependencies view (section 6, the first Phase 2
  section), which answer "why is this installed" and "what can go":
  - `core/deps.py` builds a graph of the installed distributions from their own
    metadata: direct and transitive requirements, reverse dependencies, cycles,
    and the shortest chain from a package the user asked for down to any other
    ("httpie \u2192 requests \u2192 urllib3"). A requirement that is declared but not
    installed is reported as missing rather than drawn as an edge.
  - Orphan detection uses the installers' own `REQUESTED` markers as well as the
    graph, so a package is only unneeded when nothing requires it *and* it was not
    installed by name. Packages required by nothing but asked for by the user are
    reported separately and never offered for removal by default.
  - `preview_removal` is the dry run: what would go, what would be left installed
    but unused, what is still required by something that is staying, and what
    would be left with a missing requirement.
  - The Dependencies view: a filterable package list, the why-installed chain, the
    requires and required-by trees, missing requirements, the unneeded-package
    list with its measured size, and a preview dialog before any removal. The page
    never changes an environment itself - it hands the removal to the packages
    view, so the exact command and the confirmation stay in one place.
- Multi-package removal in one plan (`InventoryService.action_for_uninstall_many`),
  so an unneeded-package clean-up is one command the user approves once.
- The packages view and the settings page (sections 4 and 5), which together make
  the Phase 1 window usable end to end:
  - The package table (`ui/views/packages.py`): installed packages with their
    status and version, search, an outdated filter, sortable columns, progressive
    refresh, the sizes toggle, and a detail panel showing description, links,
    license, author, location and the dependency relationships of the selected
    package. Package metadata is rendered as plain text; a URL becomes an action
    only on click, and only over HTTPS.
  - The five changing actions - install, uninstall, upgrade, downgrade, upgrade
    all - all go through one path that builds the plan, asks the platform guard
    whether the environment may be changed, shows the plan's own command text in a
    confirmation dialog, and only then runs the *same* plan on a worker thread.
  - A live log panel that streams a running command's output and cancels the
    process tree, not just the process.
  - The settings page (`ui/views/settings.py`): theme, language, table page size,
    sidebar width, PyPI index, extra indexes, trusted hosts, and which actions ask
    for confirmation. Values are validated as they change, written through the
    window shell (one place validates, saves, re-themes and re-translates), and
    the whole page re-renders on a language switch, including the RTL flip.
  - The PEP 668 "create a virtual environment" flow: a refusal on an
    externally-managed interpreter now offers the way out instead of only
    explaining itself. Creation goes through the hardened runner on the task
    queue with streamed progress, cancellation, and a switch to the new
    environment when it succeeds (`core/environments.py`, `ui/widgets/dialogs.py`).
  - CI now builds the sdist and the wheel, installs the wheel without its
    dependencies, checks the console entry point is declared, checks the bundled
    fonts shipped, and rebuilds a wheel from the sdist to prove the sdist is
    complete.
- The UI foundation (section 3): a dark and a light theme built from one set of
  design tokens, English and Persian with a full right-to-left flip, and the
  window they are rendered in.
  - Design tokens as plain data (`ui/theme/design_tokens.py`) with the palette
    SPEC section 7 names, plus typography, spacing and radius scales. Both
    variants expose the same roles, and the test-suite asserts it.
  - The light and dark stylesheets are *generated* from those tokens
    (`ui/theme/qss.py`) instead of being maintained by hand, so a colour is
    changed in exactly one place. `ThemeManager.apply()` hands the sheet to the
    `QApplication`, which is what makes the theme switch live.
  - "Follow the system theme" is resolved through Qt's colour-scheme hint, with
    a light fallback when no application is running.
  - Bilingual translation tables with English as the source of truth, a
    translator that formats `%(name)s` parameters without crashing on untrusted
    metadata, and a direction manager that flips the layout for Persian
    (`ui/i18n/`). The test-suite checks both tables are complete, that the
    placeholders match, and that no string in the Persian column comes from
    another script.
  - The main window (`ui/views/main_window.py`): collapsible sidebar, top bar
    with the environment switcher, the search box and the theme/language
    toggles, a stacked content area, a menu with shortcuts (Ctrl+T, Ctrl+L,
    Ctrl+B) and a status bar. Destinations whose views are not built yet are
    disabled and explain which phase builds them.
  - Settings persistence through `QSettings` in the platformdirs data directory,
    including the window geometry and state (section 3's `WindowShell`).
  - Application bootstrap (`ui/app.py`) with the bundled fonts registered, and
    the `pypackhub` console script plus `python -m pypackhub`.
  - 80 UI tests covering the tokens, the stylesheet, both languages, the
    settings bridge and an offscreen smoke test of the window with live theme
    and language switching.
- Security core, the part of Phase 1 that everything else depends on:
  - a hardened process runner that is the only place in the project able to
    spawn a process: argument lists only, a sanitized environment with
    `PYTHONPATH`/`PIP_*`/`UV_*` stripped, enforced timeouts, cancellable, and a
    kill that takes down the whole process tree;
  - validation for package specs (option injection, shell metacharacters and
    non-ASCII lookalike names are refused), environment names, paths, index
    URLs, and a line-by-line parser that holds back every installer option in
    an imported `requirements.txt` for explicit review;
  - protection for interpreters that should not be modified: system and
    PEP 668 managed installations are refused with an explanation and a
    suggestion, Windows App Execution Alias stubs are never treated as Python;
  - redaction of credentials out of logs and diagnostics, applied by a logging
    filter so a call site cannot forget it, plus a keychain-backed credential
    store;
  - an HTTPS-only HTTP client: no way to disable certificate verification,
    response size limits, timeouts, and a transport that refuses a redirect to
    plain `http` before the downgraded request is sent;
  - the project's own banned-pattern scanner (`scripts/security_scan.py`),
    which resolves import aliases and fails the build on `subprocess` outside
    the runner, `shell=True`, `os.system`, `eval`, `exec`, `pickle.loads`,
    `yaml.load` and `verify=False`;
  - a security test-suite covering every case of section 11.9 that Phase 1
    owns, run in CI against an installation without PySide6 so the core is
    proven headless rather than assumed to be.
- Project scaffold: `pyproject.toml` with runtime and development dependencies,
  `src/` layout, and configuration for ruff, black, mypy, pytest, coverage and
  bandit (`docs/DECISIONS.md`, section 0 of the Phase 1 plan).
- Bundled OFL fonts (Inter, JetBrains Mono, Vazirmatn) for a consistent UI in
  both LTR and RTL modes, with their license files.
- Hash-pinned dependency locks (`requirements.lock`, `requirements-dev.lock`) for
  every supported platform and interpreter version, regenerated from
  `pyproject.toml` and verified for freshness in CI.
- Continuous integration: lint/format/type-check, tests on Python 3.10-3.13 for
  Linux on every push and a full Windows/macOS matrix on pull requests, tags and
  manual runs, plus a dependency-lock freshness check.
- Security workflow: bandit static analysis and a `pip-audit` run against the
  committed lock file.
- Repository hygiene: contributing guide, security policy, issue and pull
  request templates, and `docs/DECISIONS.md` recording every design decision
  taken before the first line of code.

### Fixed

- The Qt-free security job could not collect two of its test modules: it installed
  the project's dependencies from a hand-written list that had gone stale and was
  missing `platformdirs`, so importing `pypackhub.core.settings` failed. The list
  is now derived from `pyproject.toml` with the GUI toolkit filtered out, and the
  job asserts that Qt is really absent so it cannot pass while proving nothing.
- `astral-sh/setup-uv@v10` does not resolve: the action stopped publishing moving
  major tags after v8, so the dependency-audit and lock-freshness jobs failed
  before running a step. Both now pin an exact release.
- Registering a real view over a destination left its placeholder page in the
  content stack, so the destination had two pages and the re-translation pass
  walked a page the user could never reach. `register_view()` now removes the
  placeholder, and the stack test asserts one page per page-kind destination with
  no orphans.
- The settings page applied settings while it was still loading them: populating a
  combo box emits its change signal, so the change handler validated a form whose
  other fields were still empty (and the empty PyPI index was refused). Loading is
  now guarded, so showing the stored settings does not look like editing them.
- `settings_data_directory()` and `ensure_log_directory()` called
  `platformdirs.user_data_dir()` with keyword arguments it does not accept, so the
  first settings or log write would have raised `TypeError`. Both directories are
  now created and restricted to the user where the operating system supports it.
- `core/pypi.py`: `monotonic` was imported from `datetime` (it lives in `time`),
  a function was used as an exception type in three `except` clauses instead of
  being checked on the caught error, `PackageMeta` was constructed without its
  required `versions` field, and `pypi_info()` used `PyPIClient` as a context
  manager before it implemented one.
- `backends/pip.py` and `backends/uv.py` swallowed every exception while looking
  for their executable; a missing tool now logs the attempt and reports an
  unsupported backend instead of retrying silently.
- `core/sizes.py`, `core/environments.py` and `core/tasks.py`: the type errors
  `mypy` reported, including `Event.set()` being treated as returning a value by
  the cancel-all counter.
- The remaining `S112` bandit finding, and the `B604` false positive on
  `MainWindow(shell=...)` (suppressed on that line with the reason). `ruff`,
  `black`, `mypy`, `bandit` and the banned-pattern scan are all clean again.

[Unreleased]: https://github.com/MY-Jafari/PyPackHub/commits/main
