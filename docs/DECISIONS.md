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
| Version | `0.1.1`, classifier `Development Status :: 2 - Pre-Alpha` | The number lives in `pyproject.toml` alone: the release workflow refuses a tag that disagrees with it, because the same number is baked into the executable's version resource and shown in the About box. |
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

The "unparsable is not evidence" rule is enforced by the project's own
``Version()`` parse, not by packaging's: ``SpecifierSet.contains`` used to
raise for an unparsable version, and the 26.x line answers ``False``
instead - a silent semantics change that would have turned every
odd-but-installed version string into a "conflict". The change was caught
because PyPackHub's own pipx bootstrap upgraded ``packaging`` on the
machine running the test-suite, which is either the best or the worst
possible way to find out.

**Disk usage is measured, not estimated, and inspection is all it does.**
The environment total walks the whole prefix (interpreter, metadata,
everything a package dropped outside its own ``RECORD``), the largest
consumers reuse the per-package measurements, and pip's and uv's caches are
located the way each tool documents - environment variables honoured, no
subprocess spawned to ask. Unreadable parts lower the total and are
remembered, so the number renders as a floor when it is one. Nothing on the
page deletes anything: clearing a cache is a change to the machine and
belongs behind a confirmation like every other change.

## Notes on phase 3: the five new managers

**One contract, seven adapters.** ``base.py`` named conda, poetry, pipx,
pipenv and pdm in its docstring from the day it was written; this phase made
the names real. Each manager is one small module that knows its command
vocabulary, its listing format and what it honestly cannot do, and everything
else - validation, confirmation, execution, snapshots, history - already
worked because it was written against the contract rather than against pip.

**A manager never runs where it cannot act.** The new
``Backend.supports_environment()`` hook is the guard the settings page
needed: conda refuses anything without ``conda-meta``, the project tools
refuse anything without their own marker files, and pip and uv refuse a
project folder - without it, a forced pip against a project would fall back
to a PATH pip belonging to some *other* prefix and install into the wrong
place while the confirmation showed an honest-looking command. The factory
asks before it hands a backend out; the listing path asks too.

**A project folder is a registered location, never a deletable one.**
``EnvironmentKind.PROJECT`` recognises poetry/pipenv/pdm projects by the
files their tools actually write (``Pipfile``, ``[tool.poetry]``,
``pdm.lock``), and the environments page can point the application at one.
Two guards follow from what a project *is*: ``ensure_deletable_environment``
refuses it - the "delete the environment" affordance must never destroy the
user's source code - and a plain ``pyproject.toml`` without a tool's markers
is nobody's project, because none of the tools would know what to do with it.
Registering is remembering: "Forget" edits the list, nothing on disk.

**Project tools run in the project, and the plan says so.** poetry, pipenv
and pdm find their target by directory, so their plans carry ``cwd`` - a new
field on ``CommandPlan``, travelled by the single execution path, so the
command confirmed is the command run *in the project it named*. ``poetry add``
also writes ``pyproject.toml`` and the lock file; the module docstring says
so plainly because that is what the word "install" means to a poetry user,
and the confirmation shows exactly that command.

**"Automatic" is the environment's own answer.** The preference order is
computed per location: a conda environment reaches for mamba then conda -
conda's listing is the only complete one there, since pip cannot see conda's
metadata - with the pip family behind for a conda env with no conda
installed; a project folder asks only its own tool; virtual environments
keep the documented uv-first order.

**pip's flags stay pip's.** ``--index-url`` and friends are carried only on
pip and uv plans. conda has channels, poetry and pdm have sources, and a
confirmed command the tool refuses to parse is worse than no command.

**The bootstrap tells the truth about what pip can install.** poetry, pipx,
pipenv and pdm are index packages, so the existing install-first flow covers
them. conda and mamba are not, and are refused with the sentence that says
where they come from instead of an offer that could never work. The batch's
elevation is decided per step: the install steps write the environment and
follow its blanket answer, while the action's own step is judged by the
backend it will run through. The report that taught the distinction: a pipx
forced on a locked all-users environment had its upgrade elevated with the
installation that preceded it, and the consent boundary - which carries no
pipes - turned the failure into "Failed:" with nothing after it. A pipx
action never needs the help; a uv action on the same environment keeps it.
The same run's empty failure line taught the log one more sentence: a
failure that captured nothing says why - elevated means no pipes, anything
else means the command died silent - instead of interpolating an empty
string.

**The bootstrap offer must survive the state it exists for.** The offer is
made precisely when no manager resolves, so nothing on the way to it may
resolve one either: the policy question falls back to the platform guard's
own answer for the prefix when there is no backend to ask, and the
elevation-refusal message names the *missing* manager rather than asking
the service which backend it fell back to. The report: with a forced pipx
that was not installed, the confirm path asked for the policy before
asking what was missing - the resolution raised, and the offer died before
the user ever saw it.

**The manager choice is checked where it is made.** Picking a manager in
the settings page is the moment the user commits to a tool, so that is the
moment the tool's state is reported: an installed one says so under the
combo, and a missing one is offered to be installed right there. The
offer is a question, never an action - the install itself runs through
the packages view's confirm-and-run path like every change (exact command,
log, history, snapshot), routed by signal rather than by the settings page
growing a private execution path of its own. The install-only batch
reuses the bootstrap machinery with the installing command as the plan the
dialog is built around, and its history row records the manager's name
under an ordinary "install" kind. The check itself is resolution only -
a PATH walk, no process spawned - which is why the settings page can ask
it on the GUI thread, and a language switch re-renders the status without
re-asking its question, because a switch is not a selection.

**Removal is the mirror of installation, and the row that asked is the
row that watches.** An installed manager that pip put there is offered
for removal with one confirmed `pip uninstall` - the same command shape,
log, history and snapshot as any uninstall - and the offer is narrowed
to exactly the managers pip installed, because the tool that put a
manager somewhere is the tool that can take it back out: conda and
mamba were not installed by pip, and pip is not offered at all, since
removing the baseline every bootstrap path starts from is a consequence
no settings row should promise. Both routes - install and remove - run
where the user already is, on the settings page that asked: the manager
row owns a small progress bar of its own, a few pixels under the combo,
which fills with the install's real percentages and turns the danger
colour whole when the run fails, and the window-wide log panel opens
below whichever page is showing. The earlier design moved the user to
the Packages page, because that was where a run was visible; the honest
fix turned out to be the other shape - give the row its own bar and let
the user stay where they made the decision. There is still exactly one
place that knows how a run is going (the one run path feeds both bars);
there are now two honest places to watch it from.

**A restored choice is reported, a selected one is asked.** The settings
page restores the stored manager into its combo on startup, and the
change handler originally guarded its *save* against the restore but not
its *prompt* - so a stored, uninstalled manager greeted the user with
"install it now?" before the window had appeared. Rendering the state
and asking the question are now separate: every restore (startup, a
save, a language switch) renders the honest one-line state without
asking, and only a selection - the user committing to a tool - may ask.

**A question this application cannot act on is not asked.** The second
dead-end in the same settings row: picking conda or mamba asked
"install it now?", the user said yes - and nothing happened, because the
only honest answer was a refusal ("it is not a pip package") that
arrived as a toast and a status line on a page the user was not looking
at. Asking was the bug: a dialog whose every "yes" ends in "no" is a
click wasted and a promise broken. The page now knows what the bootstrap
can install (`manager_installable`) and asks only there; a manager it
cannot install renders the sentence about where that manager comes from
- conda from docs.conda.io, mamba from its own docs - in the row itself,
where the question used to be. The install path keeps its refusal for
any other caller, and its own unreachable-silence branch now speaks.

**A real answer is an int, and the mock answered an enum.** The third
dead-end in the same row was the deepest: the question *was* honest
(poetry is pip-installable), the dialog *did* appear, the user *did*
click Yes - and the click vanished. PySide6's static
``QMessageBox.question`` answers with the button's *value* - a plain
``int`` - and the handler compared it with ``is`` against the enum
member, so the comparison was False for every real click ever made. The
tests never saw it because they mocked ``question`` to return the enum
member: the mock answered in a form the real dialog never sends, and a
test that mocks an API should mock the shape the platform actually
returns - the regression mock now returns ``int(StandardButton.Yes)``.
What found it was not a test at all but a real-platform driver: build
the real window, click the real modal buttons with armed timers, and
follow the trace - the offscreen suite reproduces wiring, but only the
real platform reproduces what the platform's own dialogs return.

**Out of scope is not missing, and must not be sold as either.** The
fourth report from the same settings row: poetry was forced, and every
upgrade said only "run with pip" - or offered to *install* the poetry
that was already installed. The service answered "poetry is missing"
because ``supports_environment`` refuses plain interpreters, and the UI
treated every refusal as absence. Two different truths were collapsed
into one: a manager that is not *installed* can be installed, but a
manager that is not the right *kind* for this environment never becomes
right by installing it. The service now answers the kind question on
its own (``manager_applies_to_environment``, judged from the
environment's markers, installed or not), the bootstrap offer only
fires for managers that could actually manage the environment once
installed, and the rest reach the pip fallback with the sentence that
is true: poetry manages project folders, conda manages conda
environments, and this is neither - with what to do about it (register
the project, open it there) instead of an explanation of a shadowing
that is not the reason. The settings hint stopped promising that a
forced name applies to every environment; it now says the manager is
used wherever it can manage, and that elsewhere the action says so and
offers pip.

**A focus border is part of the box it surrounds.** The last report from
the same row: the description text "went into" the selection box. The
layout audit showed no overlap anywhere - at any window size, the first
sentence sat a layout-spacing below the combo - but a *focused* combo
paints its accent border at the very edge of that spacing, and at high
DPI the 4px gap reads as 2, which reads as a text line drawn through
the box's own border. The fix is air: a real spacer between the
control and its sentence, so the two are separate at every scale the
OS knows how to render. The lesson for the audit that found "nothing
wrong": geometry proves no overlap, but perceived overlap is about the
distance between two inks, and a focus ring is ink.

**A group box is a panel, not a field.** The screenshot after that one
widened the complaint: the boxes themselves were too small, and the
description at the bottom of the Behaviour box read as sunk into the
frame. Two causes, both structural. The groups were added to the page's
form with ``addWidget`` - which puts a widget in the form's *field
column* - so each box was sized by its own contents' width and the
page showed four ragged columns of settings (399px, 505px, 673px,
1246px wide on the same page). And the group's insets lived in a
stylesheet ``padding`` on ``QGroupBox``, which Qt's box model lets
silently override the margins a layout sets - so the content met the
frame with almost nothing between them. ``addRow`` spans the row: every
box is now full-width and uniform, the insets live in the layout's own
contents margins where nothing overrides them, and the manager field
alone grew from 479px to 1064px of room for its sentences.

**A settings combo does not commit on a scroll nobody asked for.**
Qt's combos change their selection on a wheel event by default, so a
hover that drifted across the manager row could silently flip the
tool - and a manager flip opens install questions and re-probes the
machine. The three settings combos swallow wheel events while their
lists are closed; clicking first remains the only way to choose, which
is what a user means when they say the choice should "stay until I
click on it".

**A run that starts is a run that can be watched.** The log panel starts
hidden as a layout choice for an empty window, and a manager install
into a locked environment runs elevated - which, before the file-tail
below, meant no countable event could ever arrive. Together those made
an install invisible: a determinate bar stuck at zero (the honest
reading of which is "nothing is happening"), a log nobody could see, and
no word that anything had started. Three answers: the window reveals
the log the moment any confirmed run starts, an install announces
itself with a toast the instant it begins - before any output exists to
show - and the bar counts what the run actually reports, elevated or
not. The toast comes from both doors an install can enter by - the
settings page's question and the bootstrap offer at action time -
because the word "installing" belongs to the batch, not to the door.

**Elevated output crosses the consent boundary through a file.** The
one thing elevation on Windows never carried was output: no pipes cross
the UAC boundary, so an elevated install was silent by construction -
no percentage for a bar, no error line for a log, and a history whose
"why" was a guess. The boundary still carries no pipes, and that rule
is not circumvented; what crosses it is a file. The runner now starts
its own stateless helper elevated (`pypackhub.core.elevated_helper`):
the helper runs the one approved command, argv for argv, with its
output redirected to a log file the unprivileged process created and
follows while the command runs, then exits with the command's own exit
code. The rules survive the crossing unchanged - argument list only,
no shell anywhere, the executable already resolved before the line was
built, and the helper refuses anything but an absolute path, so no
lookup happens on the elevated side that this application does not
control. The helper is the second file the security scanner allows to
import subprocess, and that exception is the scanner's docstring to
explain: it is not a second spawn policy, it is the other half of the
same one. Two details earn their place in the same breath: the child
joins a Windows job object with kill-on-close, so cancelling the helper
takes the tree with it and a stopped run cannot leave a half-watched
install finishing in the dark; and the file the elevated side writes
is created by and owned by the unprivileged side, deleted when the run
ends, and never read by anything but the run that made it.

**One checked page button, and it is the page you are on.** The nav
buttons are checkable, so a click checks its own; unchecking the page
the user just left is the window's job, and for a stretch of this
session that job was missing - the loop that did it had been severed
into an unrelated method when the settings-routing methods were
inserted into the middle of `show_view`'s body, and every page ever
visited stayed checked. The window tests never caught it because none
of them asserted a checked state; now the page-switch tests do, and
the rule they pin is: exactly one checked page button, the one showing.
The log entry is the deliberate exception - it is a toggle for a panel,
not a page, so its checked state reports the panel's visibility across
every page switch, including the moment a run opens the log on its
own. A toggle's checked state is the one thing it can never lie about.

**History records the action, not the job.** A task whose work function
returns a result is a *finished* job even when the result says the command
failed - and the history badge keyed on the job's state, recording a failed
upgrade as a success. The badge now derives from the payload: a non-zero
exit is a failed action, a cancelled one is cancelled, and only a payload
that says otherwise is a success. A history that lies about outcomes would
be worse than none: it is the record a user checks the next morning.

**A test-suite is an environment, and it must not depend on the machine's
disk.** The window tests pinned the environments *page's* discovery but
not the switcher's, which reaches the same roots through the provider - so
every window test walked the machine's real home on a worker thread, and
a slow morning's disk meant workers outliving the windows that scheduled
them, and intermittent hard crashes mid-suite. The provider's scan is
pinned for those tests now, and the task manager logs a raising result
callback instead of letting it kill the worker thread: the callback runs
after the result is recorded, so there is nothing downstream left to
break, and a bug in a view's callback belongs in the log, not in an
unhandled threading traceback.

**pipx is scoped, not located.** Its applications live in its own home, so
its commands ignore the selected environment, its policy never demands the
OS's help (it only ever writes inside the user's own directories), its
listing reports the pipx home as the packages' location, its size column is
hidden rather than joining pipx's app names against another environment's
measurements, downgrade is refused because pipx has no command that
re-pins a version - a confirmation the tool cannot honour is not offered -
and the reachability question is answered as the vacuous one it is
(``targets_selected_environment=False``), so a user-site shadowing copy in
the *selected* environment never turns a pipx the user chose into a pip
fallback.

**Unparsable output is an error, not an empty project.** conda, pipx and
pipenv answer in JSON (pipenv deliberately through ``pipenv run pip list``,
one shape, one parser); poetry and pdm answer in text, parsed with guards -
a row counts only when its second token is a version - and output that reads
to nothing while the command succeeded is reported as an error, because
"nothing installed" and "could not read the answer" must never look the same.

**A mirrored control moves; the text under it does not.** The Persian pass
found a defect the English screenshots could never show: a spin box draws
its number left-aligned whatever the layout direction is, but its up/down
buttons mirror to the left in a right-to-left layout - so the number was
painted *underneath* the buttons and its first digit disappeared behind
them. The fix is not a style rule but an alignment: the text goes to the
far edge, away from wherever the buttons ended up. The general form of the
lesson: a widget that mirrors itself in RTL is only half of the story - the
plain text inside it has to be told where to stand, or the two collide.

**A language is reviewed in its own script, not in translation.** The
Persian strings are read as Persian - grammar, meaning, and the direction
the sentence actually points. Two defects survived an English-only review:
a dropped letter turned "conda" into "cond" inside a sentence, and a
message about elevated runs claimed their output could not be captured,
which stopped being true the moment the elevated helper began streaming it.
A sentence about a page names that page exactly as the interface names it
(«محیط‌ها», not an approximation), Latin tokens inside a Persian sentence
carry their direction marks, and the invisible ones are escapes rather than
raw characters - so the source stays reviewable and the rendering stays
right-to-left.

**Consciously deferred.** Channel search for conda (the search view stays
PyPI-based; conda lookups there are advisory the same way every listing-based
check is), conda environment *creation* in the environments view, binding a
project folder to its interpreter for the metadata-driven panels (a project
has no interpreter at its root, so the dependency graph and the detail panel
show the tool's own listing rather than import-time facts), per-manager
progress parsing (the busy bar stays honest where a manager emits no
countable events), pipx-aware requirements import (``pipx install`` is per
application, so a many-spec batch is not a thing pipx has), and pdm's /
pipenv's cache directories in the disk-usage panel (their documented
locations are less stable across versions than the ones reported today).


