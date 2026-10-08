"""Internationalisation: two complete tables, safe lookups, correct direction.

The Persian table is checked for the two mistakes that are easy to make and hard
to notice: a string that was never translated (so the UI silently falls back to
English in the middle of a Persian sentence) and a string that came from the wrong
script entirely - the earlier draft of this table contained Thai and Chinese
fragments, which is exactly what this test now refuses.
"""

from __future__ import annotations

import pytest

from pypackhub.core.settings import Language
from pypackhub.ui.i18n.direction import Direction, resolved_direction
from pypackhub.ui.i18n.translations import (
    EN_TABLE,
    FA_TABLE,
    TranslationKey,
    get_translation_table,
    missing_keys,
    placeholders_in,
)
from pypackhub.ui.i18n.translator import Translator, resolve_language

#: Strings that are legitimately the same in both languages.
IDENTICAL_ALLOWED = {
    TranslationKey.APP_TITLE,
    TranslationKey.BUTTON_OK,
}

#: Scripts that must never appear in the Persian table.
WRONG_SCRIPTS = (
    ("Thai", "\u0e00", "\u0e7f"),
    ("CJK", "\u4e00", "\u9fff"),
    ("Hiragana", "\u3040", "\u30ff"),
)


def test_every_referenced_key_exists_in_english() -> None:
    missing = sorted(key.value for key in TranslationKey if key.value not in EN_TABLE)
    assert not missing, f"keys without an English string: {missing}"


def test_persian_table_is_complete() -> None:
    assert missing_keys(Language.FA) == set()


def test_persian_table_translates_everything_it_claims_to() -> None:
    identical = {
        key.value
        for key, value in FA_TABLE.items()
        if value == EN_TABLE.get(key) and value != "PyPackHub"
    }
    allowed = {key.value for key in IDENTICAL_ALLOWED}
    unexpected = sorted(identical - allowed)
    assert not unexpected, f"strings left in English: {unexpected}"


@pytest.mark.parametrize(("script", "start", "end"), WRONG_SCRIPTS)
def test_persian_table_has_no_text_from_another_script(script: str, start: str, end: str) -> None:
    offenders = [
        key.value
        for key, value in FA_TABLE.items()
        if any(start <= character <= end for character in value)
    ]
    assert not offenders, f"{script} text found in Persian strings: {offenders}"


def test_placeholder_names_match_between_languages() -> None:
    for key, english in EN_TABLE.items():
        persian = FA_TABLE.get(key)
        if persian is None:
            continue
        assert placeholders_in(persian) == placeholders_in(
            english
        ), f"placeholder mismatch for {key}"


@pytest.mark.parametrize("language", [Language.EN, Language.FA])
def test_every_english_placeholder_is_a_real_field(language: Language) -> None:
    table = get_translation_table(language)
    for value in table.values():
        for name in placeholders_in(value):
            assert name.isidentifier()


def test_system_language_resolves_to_english() -> None:
    assert resolve_language(Language.SYSTEM) is Language.EN
    assert resolve_language(Language.FA) is Language.FA


def test_translator_formats_named_parameters() -> None:
    translator = Translator(language=Language.FA)
    assert translator.translate(TranslationKey.TOAST_INSTALLED, package="requests") == (
        "requests نصب شد"
    )


def test_translator_ignores_extra_parameters_and_unknown_placeholders() -> None:
    translator = Translator(language=Language.EN)
    # An extra parameter must not raise, and an unknown placeholder must not either.
    assert translator.translate(TranslationKey.BUTTON_OK, nonsense="x") == "OK"
    assert translator.translate("custom.key", fallback="Hello %(name)s", name="there") == (
        "Hello there"
    )


def test_missing_persian_string_falls_back_to_english_not_to_the_key() -> None:
    translator = Translator(language=Language.FA, table={})
    assert translator.translate(TranslationKey.NAV_PACKAGES) == "Packages"


def test_unknown_key_returns_the_key_as_a_last_resort() -> None:
    translator = Translator(language=Language.FA)
    assert translator.translate("definitely.not.a.key") == "definitely.not.a.key"


def test_plural_helper_passes_the_count_through() -> None:
    translator = Translator(
        language=Language.EN,
        table={"items": "%(count)s packages"},
    )
    assert translator.translate_plural("items", 3) == "3 packages"


def test_with_language_returns_a_new_translator() -> None:
    english = Translator(language=Language.EN)
    persian = english.with_language(Language.FA)
    assert persian.resolved_language is Language.FA
    assert english.resolved_language is Language.EN
    assert persian.translate(TranslationKey.NAV_PACKAGES) == FA_TABLE["nav.packages"]


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        (Language.EN, Direction.LEFT_TO_RIGHT),
        (Language.FA, Direction.RIGHT_TO_LEFT),
    ],
)
def test_direction_follows_language(language: Language, expected: Direction) -> None:
    assert resolved_direction(language).direction is expected


def test_persian_direction_is_explicit_not_heuristic() -> None:
    state = resolved_direction(Language.FA)
    assert state.explicit_rtl is True
    assert state.heuristic == "language"


def test_rtl_flag_on_the_translator_matches_the_direction() -> None:
    assert Translator(language=Language.FA).is_rtl is True
    assert Translator(language=Language.EN).is_rtl is False
    assert Translator(language=Language.SYSTEM).is_rtl is False
