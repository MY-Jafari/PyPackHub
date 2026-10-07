# Security policy

PyPackHub installs, removes and inspects Python packages, which means it runs
code and third-party tools on your machine. Security is a first-class feature
here, not an afterthought, so this document describes both the model we build
against and how to report a problem in it.

## Reporting a vulnerability

Please **do not** open a public issue for a security problem.

Use GitHub's private reporting flow:
<https://github.com/MY-Jafari/PyPackHub/security/advisories/new>

Include, as far as you can:

- the affected version or commit,
- the platform (Windows, macOS, Linux) and the Python version,
- a minimal reproduction — a package name, an imported requirements file, a
  crafted path, or the exact UI steps,
- the impact you believe it has.

We aim to acknowledge a report within 7 days and to publish a fix or a
mitigation together with an advisory. Please give us a reasonable window before
disclosing publicly.

## Security model

The rules below are binding for the whole codebase. They come from section 11 of
`SPEC.md` and each one is covered by tests in `tests/security/`.

### Command execution

- Every subprocess is spawned from `pypackhub.core.runner`. There is no other
  call site in the project.
- `shell=True`, `os.system` and string-concatenated command lines are banned; a
  static scan in CI fails the build when they appear.
- Executables are resolved explicitly (the environment's own interpreter via
  `shutil.which`), never from an untrusted working directory or a `PATH` entry
  that a project folder can shadow.
- Child processes get a sanitized environment (an explicit allow-list, no
  inherited secrets), an optional timeout, and are killed as a whole process
  tree on cancel.
- Options are separated from positional arguments with `--` wherever the tool
  supports it.

### Input validation

- Package specs must satisfy `packaging.requirements.Requirement` **and** a
  strict allow-list pattern. Shell metacharacters, whitespace tricks, URLs and
  VCS references, and anything starting with `-` are rejected — the last one
  blocks option injection such as `--index-url=...` through a name field.
- Paths are normalized and resolved, then checked to be inside a discovered,
  verified environment (it must carry its expected markers, and it must not be a
  system or home root) before anything is modified or deleted.
- Imported `requirements.txt`, `pyproject.toml`, `environment.yml` and JSON
  snapshots are untrusted input: parsed without `eval`/`pickle` and with
  `yaml.safe_load` only, validated line by line, and any line carrying an
  installer option (`-e`, `-r`, `--index-url`, `--extra-index-url`,
  `--trusted-host`) is held back until the user explicitly approves it.
- Custom index URLs must be HTTPS. Plain HTTP needs an explicit, repeated
  warning, a persistent badge is shown while a non-default index is active, and
  `--extra-index-url` carries a dependency-confusion warning.

### Supply chain

- Typosquatting detection compares a requested name (normalized per PEP 503)
  against a bundled list of popular PyPI packages and warns with "did you mean
  X?" before anything is installed.
- A pre-install trust panel shows first-release date, release count, latest
  release date, homepage/repo presence, license, and whether only an sdist is
  available (an sdist can execute arbitrary code at install time).
- Nothing is ever installed without an explicit user action, and optional or
  suggested packages are never pulled in automatically.

### Privileges

- The application runs as a normal user and never asks for administrator rights
  at start-up. Elevation, if ever needed for one specific action, goes through
  the operating system's own prompt, is explained first, and is never persisted.
- Modifying a system or externally-managed (PEP 668) interpreter is blocked by
  default with a suggestion to create a virtual environment instead.
- Environment deletion and system-interpreter changes always require
  confirmation, even when the user enabled the fast, confirmation-free mode.

### Network and data

- HTTPS only, with certificate validation always on (no `verify=False`), plus
  timeouts and response-size limits.
- Credentials for private indexes live in the OS keychain via `keyring`, never
  in configuration files, logs or command lines, and are masked in the UI.
- Logs, diagnostics and crash reports are passed through a redaction layer that
  strips tokens, passwords and `https://user:pass@host` credentials.
- No telemetry by default. Any future usage or crash reporting is opt-in,
  documented, and anonymized.

### Distribution

- Release artifacts are built only by GitHub Actions from tagged commits, and
  every artifact is published together with its SHA256 checksum.
- The in-app update checker verifies that checksum, never updates silently, and
  never executes downloaded code without user confirmation.

## What is implemented today

The project is at the very beginning of its roadmap, so please treat the model
above as the specification we build towards:

| Area | State |
| --- | --- |
| Hardened process execution (`core/runner.py`) | implemented — argument lists, sanitized environment, timeouts, whole-tree kill |
| Input validation (`core/validation.py`) | implemented — package specs, names, paths, index URLs, imported requirements files |
| Log and diagnostic redaction (`core/secrets.py`) | implemented, with a filter attached to every log record |
| Credentials in the OS keychain (`core/secrets.py`) | implemented; nothing is ever written to a config file |
| HTTPS-only networking (`core/net.py`) | implemented — no `verify` parameter exists, redirects to `http` are refused before they are sent |
| System-Python and PEP 668 protection (`core/platform_guard.py`) | implemented |
| Static security scan in CI | implemented — bandit, plus the project's own banned-pattern scanner |
| Dependency audit in CI | `pip-audit` runs against the committed, hash-pinned lock file |
| Typosquatting and trust signals | Phase 2 |
| Vulnerability scanning with snapshots | Phase 4 |
| Application update verification | Phase 4 |

`docs/DECISIONS.md` tracks which section delivers which part.
