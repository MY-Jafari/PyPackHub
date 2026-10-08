"""Internationalization for PyPackHub.

Bilingual support for English and Persian, with right-to-left layout handling for
Persian. Nothing in this package imports Qt, so the tables, the translator and
the direction rules can all be tested without a display server.

* :mod:`pypackhub.ui.i18n.translations` holds the two tables and the key enum;
* :mod:`pypackhub.ui.i18n.translator` looks strings up, formats placeholders and
  falls back to English (never to a raw key);
* :mod:`pypackhub.ui.i18n.direction` answers whether the UI should mirror itself.

Adding a language means adding one table here and one entry to
``pypackhub.core.settings.Language`` (see CONTRIBUTING.md).
"""

from __future__ import annotations
