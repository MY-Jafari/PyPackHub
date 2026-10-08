"""Headless core of PyPackHub.

Architectural rules enforced by ``tests/test_architecture.py``:

* Nothing in this package may import a GUI toolkit (PySide6/Qt) or anything
  from :mod:`pypackhub.ui`. The core must stay importable by the test-suite,
  by CI, and by the CLI companion without a display server. The security job in
  CI installs the project *without* Qt and runs the security test-suite, so
  this is checked rather than assumed.
* Every subprocess is spawned from :mod:`pypackhub.core.runner` and every piece
  of external input is validated in :mod:`pypackhub.core.validation`.
  ``scripts/security_scan.py`` fails the build when a module imports
  :mod:`subprocess` anywhere else.

Implemented so far (section 1 of the Phase 1 plan):

``runner``
    Hardened process execution: argument lists, a sanitized environment,
    timeouts, cancellation that kills the whole process tree.
``validation``
    Package specs, environment names, paths, index URLs, and the line-by-line
    parser for imported requirements files.
``platform_guard``
    What a Python prefix on disk is, and whether it may be modified.
``secrets``
    Redaction for logs and diagnostics, plus the OS-keychain wrapper.
``net``
    The HTTPS-only HTTP client with size limits and safe JSON parsing.

Built in section 2: ``settings``, ``logging_setup``, ``environments``,
``sizes``, ``pypi``, ``tasks``, ``backends/`` (pip and uv), ``report``.

Still ahead, in the order ``docs/DECISIONS.md`` records:

* section 4: the packages view - install, uninstall, upgrade and downgrade
  through the backends, the live log, and the confirmation dialogs;
* section 5: the settings page and the PEP 668 "create a virtual environment"
  flow;
* later phases: ``deps``, ``security``, ``trust``, ``snapshots``, ``exporter``,
  ``binaries``, ``tags``, the conda/poetry/pipx/pipenv/pdm backends.
"""

from __future__ import annotations
