"""Headless core of PyPackHub.

Architectural rules enforced by ``tests/test_architecture.py``:

* Nothing in this package may import a GUI toolkit (PySide6/Qt) or anything
  from :mod:`pypackhub.ui`. The core must stay importable by the test-suite,
  by CI, and by the CLI companion without a display server.
* Every subprocess is spawned from :mod:`pypackhub.core.runner` and every piece
  of external input is validated in :mod:`pypackhub.core.validation`.

The modules below are filled in section by section, following ``SPEC.md``
section 5 and the section plan recorded in ``docs/DECISIONS.md``:

* section 1: ``runner``, ``validation``, ``secrets``
* section 2: ``settings``, ``logging_setup``, ``platform_guard``,
  ``environments``, ``package_index``, ``sizes``, ``pypi``, ``tasks``,
  ``backends/``, ``report``
"""

from __future__ import annotations
