"""Shared pytest configuration for the PyPackHub test-suite."""

from __future__ import annotations

import os

# Pick Qt's offscreen platform plugin before anything imports Qt, so the suite
# runs unchanged on CI runners (no display server) and on developer machines.
# `setdefault` keeps a developer's explicit override intact.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
