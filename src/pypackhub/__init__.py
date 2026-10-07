"""PyPackHub - a unified, cross-platform manager for Python packages and environments.

This top-level package deliberately contains almost nothing. All logic lives in
:mod:`pypackhub.core` (headless, no GUI toolkit imports) and the PySide6
presentation layer lives in :mod:`pypackhub.ui`. See ``SPEC.md`` section 4 and
``docs/DECISIONS.md``.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

__all__ = ["APP_DISPLAY_NAME", "APP_ID", "__version__"]

#: Human readable application name, used for window titles and the about dialog.
APP_DISPLAY_NAME = "PyPackHub"

#: Stable machine identifier: distribution name, platformdirs app name,
#: QSettings organization/application key and keyring service name.
APP_ID = "pypackhub"

try:
    __version__ = version(APP_ID)
except PackageNotFoundError:  # pragma: no cover - only when imported from a bare source tree
    __version__ = "0.0.0+unknown"
