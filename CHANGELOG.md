# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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

[Unreleased]: https://github.com/MY-Jafari/PyPackHub/commits/main
