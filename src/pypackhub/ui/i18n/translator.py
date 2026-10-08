"""Translator for PyPackHub's bilingual UI.

This module interprets the tables in :mod:`pypackhub.ui.i18n.translations` and
exposes the small API the rest of the UI calls. It owns translation tables, not
Qt state; the window shell owns the side that installs a Qt translator and flips
the layout direction.

The translator is pure and stateless per language: switching language means
building a new one, which is what makes the switch testable without a display
server.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pypackhub.core.settings import Language
from pypackhub.ui.i18n.translations import (
    EN_TABLE,
    get_translation_table,
    placeholders_in,
)

__all__ = [
    "Translator",
    "resolve_language",
    "translate",
    "translate_with_fallback",
]

#: Languages the UI is authored in. ``SYSTEM`` has no table of its own and is
#: resolved to one of these.
AUTHORED_LANGUAGES: frozenset[Language] = frozenset({Language.EN, Language.FA})


def resolve_language(language: Language) -> Language:
    """Resolve ``SYSTEM`` to the language the UI actually renders in.

    The UI has no operating-system locale detection of its own yet: it is
    authored in English and translated into Persian, so an unspecified language
    renders English. That is a decision, not an accident - guessing from
    ``LANG``/``LC_ALL`` would produce a half-translated window on machines whose
    locale happens to match one of the two tables by accident.
    """
    if language in AUTHORED_LANGUAGES:
        return language
    return Language.EN


@dataclass(slots=True)
class Translator:
    """A translator bound to one language and one translation table."""

    language: Language = Language.SYSTEM
    table: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if self.table is None:
            self.table = get_translation_table(self.language)

    @property
    def resolved_language(self) -> Language:
        return resolve_language(self.language)

    @property
    def is_rtl(self) -> bool:
        return self.resolved_language is Language.FA

    def translate(self, key: str, *, fallback: str = "", **params: Any) -> str:
        """Return the translation for *key*, formatted with *params*.

        Lookup order is: the current table, then the English table, then
        *fallback*, then the key itself. Falling back to English (rather than
        straight to the key) is what keeps a missing Persian string readable
        instead of turning the UI into ``settings.theme``.
        """
        template = self.table.get(key) if self.table else None
        if not template:
            template = EN_TABLE.get(key)
        if not template:
            template = fallback or key

        return self._format(template, params)

    def translate_plural(self, key: str, count: int, **params: Any) -> str:
        """Translate *key* for a count, with the count made available.

        Persian and English both use the singular form for every count in the
        strings this UI ships ("1 package", "3 packages" is handled by the
        template carrying ``%(count)s``), so this is a lookup plus the count and
        not a plural-rule engine. It exists so call sites do not invent their own
        convention before the plural rules are needed.
        """
        return self.translate(key, count=count, **params)

    def with_language(self, language: Language) -> Translator:
        """Return a new translator for the requested language."""
        return Translator(language=language, table=get_translation_table(language))

    def missing_keys(self) -> set[str]:
        """Return the English keys the current table does not translate."""
        table = self.table or {}
        return {key for key in EN_TABLE if not table.get(key)}

    @staticmethod
    def _format(template: str, params: dict[str, object]) -> str:
        """Apply ``%(name)s`` parameters, ignoring ones the template lacks.

        ``str % params`` would raise ``KeyError`` for an extra parameter and
        ``ValueError`` for an unknown placeholder. Metadata from PyPI is untrusted
        input (SPEC section 11.7), so a mismatch between a template and its
        values must render as best it can rather than crash the UI.
        """
        if not params:
            return template

        needed = placeholders_in(template)
        if not needed:
            return template

        return template % {name: params.get(name, "") for name in needed}


def translate(
    key: str,
    *,
    language: Language,
    table: dict[str, str] | None = None,
    fallback: str = "",
    **params: Any,
) -> str:
    """Translate one key for a language, with an optional table override."""
    return Translator(language=language, table=table).translate(key, fallback=fallback, **params)


def translate_with_fallback(
    key: str,
    *,
    language: Language,
    table: dict[str, str] | None = None,
    fallback: str = "",
    **params: Any,
) -> str:
    """Translate one key, preferring the table and falling back to *fallback*."""
    return Translator(language=language, table=table).translate(key, fallback=fallback, **params)
