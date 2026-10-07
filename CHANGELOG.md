# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
