"""Package-manager backends declared by this project.

Each backend is a small, audited adapter that knows how to talk to one package
manager through :mod:`pypackhub.core.runner` instead of spawning things on its
own. The interface is defined here so that the rest of the application can talk
about "a backend" without importing a specific manager.

Still to come in section 2: a shared base class, backends/pip.py and
backends/uv.py. This package may not import a GUI toolkit or pypackhub.ui.
"""

from __future__ import annotations
