"""PySide6 (Qt 6) presentation layer of PyPackHub.

Built so far (section 3 of the Phase 1 plan, see ``docs/DECISIONS.md``):

``theme/``
    Design tokens, the generated light and dark stylesheets, and the theme
    manager that resolves "follow the system" and applies a theme to a live
    application.
``i18n/``
    The English and Persian translation tables, the translator, and the layout
    direction manager that mirrors the UI for Persian.
``views/``
    The window shell (settings, theme and language state) and the main window:
    sidebar, top bar, content stack, status bar.
``app.py``
    The bootstrap behind the ``pypackhub`` console script and
    ``python -m pypackhub``.

Not built yet, and deliberately not stubbed: the package table and its detail
panel, the confirmation dialogs and the live log (section 4), and the settings
page with the PEP 668 flow (section 5). Their navigation entries are visible but
disabled, and each opens a placeholder page that names the phase which builds it.

The rule the core enforces also holds in reverse here: the UI never talks to pip,
uv or conda. It asks a backend through the core's task layer, so nothing in this
package spawns a process of its own.
"""

from __future__ import annotations
