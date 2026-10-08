"""Tests for the section 3 UI foundation: tokens, stylesheets, i18n and the shell.

Everything that can be tested without a display server lives in the modules that
do not import Qt. The one module that drives a real window is marked ``gui`` and
skips itself when PySide6 is not installed, which is what lets the security job
in CI run the core test-suite without Qt.
"""

from __future__ import annotations
