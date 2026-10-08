"""Views for PyPackHub.

This package owns the application shell: :mod:`pypackhub.ui.views.main_window`
draws the window, :mod:`pypackhub.ui.views.window_shell` holds the settings,
theme and language state behind it. :class:`ViewName` names every destination the
application knows about, including the ones whose views arrive in later phases.

Views are plain PySide6 widgets that reach the core only through the interfaces
defined in :mod:`pypackhub.core`. The UI layer never calls pip, uv or conda
directly: it asks a backend through the core task layer.

Importing this package does not import Qt. ``main_window`` and ``window_shell``
do, so tests that must run without a display server import the modules they need
individually.
"""

from __future__ import annotations

__all__ = [
    "ViewName",
]

from enum import Enum


class ViewName(str, Enum):
    """The views the application knows about, even before they are built.

    Every destination SPEC section 7 puts in the sidebar has a member here, so
    the navigation structure is complete from section 3 onward; the ones whose
    views arrive later are disabled until their phase builds them.
    """

    DASHBOARD = "dashboard"
    PACKAGES = "packages"
    ENVIRONMENTS = "environments"
    DEPENDENCIES = "dependencies"
    SEARCH = "search"
    SECURITY = "security"
    SNAPSHOTS = "snapshots"
    HISTORY = "history"
    LOGS = "logs"
    SETTINGS = "settings"
