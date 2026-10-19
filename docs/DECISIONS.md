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
| 2 | ✅ Core: settings, logging, environment detection and system info, installed-package listing, sizes, PyPI client, task queue, `pip` and `uv` backends | Unit tests with mocked subprocess output, plus a real run against this machine |
| 3 | ✅ UI foundation: design tokens and QSS themes, i18n and RTL direction management, window shell, settings persistence, console entry point | Offscreen smoke test, live theme and language switching |
| 4 | ✅ Packages view: table, search/filter/sort, detail panel, confirmation dialog, live terminal with cancel, install/uninstall/upgrade/downgrade/upgrade-all | Offscreen smoke test plus a manual run |
| 5 | ✅ Settings page, the PEP 668 "create a virtual environment" flow, workflow finalisation, README completion | The Phase 1 acceptance checklist from SPEC section 13 |

Phases 2 to 4 are split the same way once Phase 1 is done and reviewed.

Phase 1 is complete and was committed before phase 2 started, so the same
section-by-section approach continues. Phase 2's split follows the README roadmap,
ordered so each section stands on the one before it:

| Section | Contents | Gate |
| --- | --- | --- |
| 6 | ✅ Dependency graph: `core/deps.py` (transitive requires and reverse deps, cycles, why-installed chains, orphan detection from the installer's `REQUESTED` markers, removal preview) and the Dependencies view with the preview-removal flow | Graph unit tests plus an offscreen smoke test of the view |
| 7 | ✅ PyPI browsing: the Search view (a package index search, release history) and import/export of requirements files | Mocked index plus an offscreen smoke test |
| 8 | ✅ Trust and supply chain: `trust.py` typosquatting against the bundled popular-package list, the trust panel, and conflict/constraint checks before an install | The Phase 1 typosquatting cases (`reqeusts`, `numpyy`, `pandsa`) warn |
| 9 | ✅ Disk-usage analysis: per-package and per-environment usage, the largest consumers, and cache inspection | Unit tests over a synthetic environment |
| 10 | Dashboard: the landing view, with environment and package summaries drawn from sections 2-9 | Offscreen smoke test |

## Notes on section 3

**The theme switch is one call, not a widget walk.**`WindowShell.apply_to()`
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

**Three bandit `# nosec` lines, all B604.** Bandit's B604 heuristic flags any call
with a keyword argument named `shell=`; `MainWindow(shell=...)`,
`PackagesView(shell=...)` and `SettingsView(shell=...)` all pass PyPackHub's own
window-shell object, not a command shell. Each suppression is on the single
offending line with the reason, rather than disabling the check for the file or
for the project.

**Three latent defects were found while building this section and fixed in the
same pass**, because a section gate is "every check passes" and none of them was
reachable without a window: `settings_data_directory()` and
`ensure_log_directory()` called `platformdirs.user_data_dir()` with keyword
arguments it does not accept (the first settings write would have crashed),
`pypi.py` used `datetime.monotonic` and caught a function as an exception type,
and `backends/pip.py`/`uv.py` swallowed every exception while looking for their
executable. `pypi.py` also now implements the context-manager protocol it was
already being used with by `pypi_info()`.

## Notes on section 5

**The settings page writes through the shell, not through the store.** The page
never touches `QSettingsStore` and never calls `apply_theme()` or the translator:
it builds an `AppSettings`, hands it to `WindowShell.apply_settings()`, and the
shell is what validates it, saves it, re-themes the application and re-translates
the window. One write path means a settings change cannot half-apply, and the page
stays ignorant of every other part of the window.

**Populating a form must not look like editing it.** `load_settings()` sets the
combo indices, and a `QComboBox.setCurrentIndex()` on a changed index emits
`currentIndexChanged` - so the first build of the window ran the change handler
while the fields further down the form were still empty, and the validator refused
the (temporarily) empty PyPI index. The page now sets a `_loading` guard around the
population and the handler returns early while it is set. The existing
"second window restores the stored theme" test caught this, which is the argument
for keeping a test that builds a real window twice.

**The way out of a PEP 668 refusal is a virtual environment, not an override.**
The `BlockedDialog` never offers `--break-system-packages`; when the refusal is
one a virtual environment would actually solve - `would_need_installer_override`,
i.e. an externally-managed interpreter - it adds a *Create a virtual environment*
button, and that is the only refusal that gets one. An App Execution Alias stub is
a dead end because there is no interpreter behind it to build an environment with,
and inventing a second button for it would only invite the user to think the
refusal was negotiable.

**Creating an environment runs on the task queue, and switches the window to
it.** `venv` is a real child process, so it goes through the runner on a worker
thread like every other long job: progress streams into the log panel, the shared
Cancel button stops it (the process tree, not just the process), and on success
the new prefix becomes the window's environment and the table refreshes. Blocking
the window behind a modal until `venv` finished would have been the easy version
of this and the wrong one.

**`register_view` removes the placeholder it replaces.** Section 4's
`show_view()` preferred the registered view, so nothing *looked* wrong while the
orphan placeholder stayed in the `QStackedWidget` - but the destination then had
two pages, and the re-translation pass walked a page the user could never reach.
The stack-count test was written against the old count and has been rewritten to
assert one page per page-kind destination and no orphan placeholders, which is
what it was always meant to check.

**The section-5 "minimal build job" is a real packaging gate.** CI builds the
sdist and the wheel, installs the wheel with `--no-deps`, asserts the
`pypackhub = pypackhub.ui.app:run` console script is declared, asserts the bundled
font files actually shipped (the Persian UI is set in Vazirmatn and the Latin UI in
Inter; neither is assumed to be installed), and rebuilds a wheel *from the sdist*
to prove the sdist is complete. Full release and signing stay in Phase 4, as the
CI strategy table says.

**Three of the ten destinations are real now.** Packages and Settings are
registered views; Logs reveals the shared log panel. The other seven are still
disabled placeholders naming their phase, and the README says so.

## Two CI defects found after Phase 1 shipped

**The Qt-free security job repeated the dependency list by hand, and the list
gone stale.** The job installs the project with `--no-deps` on purpose, so a
missing runtime dependency cannot be hidden by the GUI toolkit being present. It
then re-listed the dependencies it wanted - and `platformdirs` was not on the
list, so `pypackhub.core.settings` could not be imported and two Qt-free test
modules failed to collect. The list is now derived from `pyproject.toml` with
`PySide6` filtered out, so it cannot drift from the package again, and the job
asserts that Qt really is absent: otherwise it could start passing while proving
nothing.

**A moving major tag on a third-party action no longer resolves.** `setup-uv`
stopped publishing moving major tags after v8, so `astral-sh/setup-uv@v10` does
not exist and both jobs using it failed before running a step. The version is now
pinned to an exact release. Action versions in this project are pinned rather than
tracked for the same reason the dependency locks are: a workflow that silently
changes underneath the repository is not reproducible.

## Notes on section 6

**An edge is evidence, not a guess.** The graph is built only from what the
installed distributions declare about themselves and from the installers' own
markers. A requirement that is declared but not installed is *not* an edge - an
edge to something that is not there is not a relationship between two installed
packages - so it is reported separately as missing. That distinction is what turns
"this environment is broken" from a silent non-fact into a line in the view.

**Orphans need two absences, not one.** ``pip install X`` writes a ``REQUESTED``
marker into ``X``'s ``.dist-info`` directory, and only for the packages named on a
command line. So a package is only unneeded when nothing installed requires it
*and* there is no marker. Without the second half every package the user installed
on purpose but that nothing else uses would be listed as rubbish, which is the
fastest way to make a clean-up feature dangerous. The markers give the page a
second, smaller list - required by nothing, asked for by name - which is reported
so the count is not a surprise and never offered for removal by default.

**Reverse edges are derived, never read.** ``InstalledMetadataIndex`` computes its
own ``required_by``, but the graph inverts its forward edges instead. Two
directions of the same relationship cannot then disagree, reading either one is
consistent, and a package whose own metadata was unreadable still shows what
requires it rather than the reverse view going blind because one file failed to
parse.

**"Why is this installed?" is the feature, not the tree.** A dependency tree is a
lot of lines to answer a question whose answer is usually one short chain, so the
chain is stated first and in a sentence a person can read: *httpie → requests →
urllib3*. The traversal is breadth-first from every package the user asked for at
once, so the chain that arrives first is a shortest one, and a package with no
such chain is exactly an orphan - the two answers agree by construction rather
than by two implementations agreeing by luck.

**The dry run is the preview, and it is exact about what it can see.**
``preview_removal`` computes what a removal would do from the graph: what goes,
what would be left installed but unused, what is still required by something that
is staying, and what would be left with a missing requirement. It is deliberately
*not* the confirmation - the preview ends in Cancel or Continue, and continuing
hands the same package names to the packages view's confirm-and-run path, which
shows the exact command and asks separately. Two questions, two answers, because
merging them is how someone uninstalls an environment.

**The dependencies page cannot change anything.** It emits a
``PackageAction`` and the window gives it to the packages view. That keeps the
spec's "one path that changes an environment" true even though a second page now
has a button that looks like it uninstalls things, and it means the confirmation,
the policy check and the log panel are shared rather than reimplemented.

**The graph is read when someone looks at it.** ``show_view`` refreshes the page
rather than the window scanning at startup, and ``refresh`` refuses to start a
second scan while one is running, so switching back and forth does not queue work.
Reading is a worker-thread job like every other long pass, and the result arrives
as a signal - including for a *cyclic* environment, which renders as a finite tree
with one marked edge instead of hanging the UI thread.

## Notes on sections 7-9

**Typosquatting is one edit away, not "similar".** A check that flags every
name resembling a popular one flags half the index and teaches nobody
anything, so the bar is exact: Damerau-Levenshtein distance at most one
against the bundled catalogue (which makes a swapped, missing or doubled
letter one edit each - the three shapes real typos take), plus ASCII
homoglyph folding on both sides so ``1nstall`` reads as ``install`` and a
folded-exact match (``request5`` for ``requests``) warns hardest of all.
Names shorter than five characters are never the answer to a near-miss, an
exact catalogue name is the package itself rather than a warning, and pip
and uv are never translated so matching their English output is stable.

**The trust panel reports facts, never a verdict.** First release date,
release count, latest release date, wheel-versus-sdist and homepage all come
from one index request, and the flags (brand-new, dormant, sdist-only, no
home page) are computed from those facts - each dated fact that cannot be
parsed is simply not claimed. A lookup that failed renders as *unknown*,
deliberately distinct from *clean*, because "the index could not be asked"
shown as "nothing suspicious" is how a panel teaches a user to ignore it.

**The supply-chain warnings ride the confirmation, and turning
confirmations off does not silence them.** The confirmations-off setting is
a convenience about ordinary actions; a name one letter from a popular
package, or a pinned version that breaks a range an installed package
declares, is information the user has no other channel for, so either
warning forces the dialog open. The refusal is never the point - both are
advisory, and the button beside them still works.

**Import ratifies; it does not re-decide.** The requirements parser already
classified every line of an untrusted file, so the preview starts from
those decisions: plain packages checked, review-flagged lines unchecked
with their reasons as tooltips, refused lines locked. Installer option
lines (``-r``, ``--index-url``, ``--trusted-host``) are shown but inert -
this flow installs specs and cannot honour an option, and a checkbox that
changes nothing is a lie. What the user ratifies leaves as one install
action, one command, one confirmation. The row-to-line mapping goes through
the rows the list actually shows, not the parsed file's indices: a comment
anywhere above a package used to shift every selection onto the wrong line,
which is the bug the dialog's tests pin.

**Conflict checks keep "missing" apart from "unsatisfied".** The graph
already reports a declared-but-not-installed requirement as *missing*;
calling the same thing a conflict would show one problem twice in two
words. ``find_conflicts`` answers only the other half - declared range,
installed version, unsatisfied - with the evidence (who declared what,
which version) travelling to the dialog so each language can sentence it
its own way. An unparsable version is never evidence of anything, and a
requirement with no range constrains nothing. Before a pinned install or a
downgrade the same arithmetic runs against the *incoming* version, which is
the "impact warnings before any change" the spec asks for.

**Disk usage is measured, not estimated, and inspection is all it does.**
The environment total walks the whole prefix (interpreter, metadata,
everything a package dropped outside its own ``RECORD``), the largest
consumers reuse the per-package measurements, and pip's and uv's caches are
located the way each tool documents - environment variables honoured, no
subprocess spawned to ask. Unreadable parts lower the total and are
remembered, so the number renders as a floor when it is one. Nothing on the
page deletes anything: clearing a cache is a change to the machine and
belongs behind a confirmation like every other change.

