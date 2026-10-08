"""The design tokens: one role set, two variants, legible colours.

SPEC section 7 names the palette and both variants are supposed to share one
structure. These tests hold that down: a role added to one variant but not the
other, or a text colour that becomes unreadable, fails here rather than in a
screenshot.
"""

from __future__ import annotations

import dataclasses

import pytest

from pypackhub.ui.theme.design_tokens import (
    ACCENT_ROLE_TOKENS,
    CORNER_RADIUS_PX,
    FONT_FAMILIES,
    SPACER_PX,
    AccentRole,
    DesignTokens,
    ThemeVariant,
    default_dark_tokens,
    default_light_tokens,
    default_tokens,
)

VARIANTS = [default_light_tokens(), default_dark_tokens()]


def _token_fields() -> list[str]:
    return [field.name for field in dataclasses.fields(DesignTokens)]


def _colour_fields() -> list[str]:
    return [name for name in _token_fields() if not name.startswith("radius")]


@pytest.mark.parametrize("tokens", VARIANTS, ids=lambda tokens: tokens.variant.value)
def test_both_variants_expose_the_same_roles(tokens: DesignTokens) -> None:
    light_fields = set(_token_fields())
    assert all(getattr(tokens, field) for field in light_fields)


def test_variants_have_identical_field_sets() -> None:
    light = dataclasses.asdict(default_light_tokens())
    dark = dataclasses.asdict(default_dark_tokens())
    assert light.keys() == dark.keys()


def test_colour_values_use_the_spec_palette() -> None:
    light = default_light_tokens()
    dark = default_dark_tokens()

    assert light.background == "#F5F7FB"
    assert light.surface == "#FFFFFF"
    assert light.accent_primary == "#3B6EF5"
    assert light.danger == "#DC2626"

    assert dark.background == "#0F1218"
    assert dark.surface == "#171B24"
    assert dark.background_elevated == "#1F2430"
    assert dark.border == "#2A3140"
    assert dark.accent_primary == "#5B8CFF"
    assert dark.success == "#34D399"
    assert dark.warning == "#FBBF24"
    assert dark.danger == "#F87171"


@pytest.mark.parametrize("tokens", VARIANTS, ids=lambda tokens: tokens.variant.value)
def test_colours_are_hex_or_rgba(tokens: DesignTokens) -> None:
    for field in _colour_fields():
        if field == "variant":
            continue
        value = getattr(tokens, field)
        if field.startswith("shadow"):
            assert value.startswith("rgba(")
            continue
        assert value.startswith("#") and len(value) == 7, f"{field}={value!r}"


@pytest.mark.parametrize("tokens", VARIANTS, ids=lambda tokens: tokens.variant.value)
def test_primary_and_secondary_text_stay_legible(tokens: DesignTokens) -> None:
    """WCAG AA for large text is 3:1; body text should clear it comfortably."""
    for background in (tokens.background, tokens.surface, tokens.background_elevated):
        assert tokens.contrast_ratio(tokens.text_primary, background) >= 4.5
    for background in (tokens.background, tokens.surface):
        assert tokens.contrast_ratio(tokens.text_secondary, background) >= 3.0


@pytest.mark.parametrize("tokens", VARIANTS, ids=lambda tokens: tokens.variant.value)
def test_accent_contrast_pair_is_legible(tokens: DesignTokens) -> None:
    assert tokens.contrast_ratio(tokens.text_on_accent, tokens.accent_primary) >= 3.0


def test_every_badge_role_resolves_to_a_colour() -> None:
    for role in AccentRole:
        for tokens in VARIANTS:
            colour = tokens.color_for_role(role)
            assert colour.startswith("#")


def test_badge_role_map_only_names_real_tokens() -> None:
    fields = set(_token_fields())
    assert set(ACCENT_ROLE_TOKENS.values()) <= fields


def test_variant_exposes_the_radius_and_spacing_scale() -> None:
    tokens = default_light_tokens()
    assert set(CORNER_RADIUS_PX) == {"sm", "m", "l", "full"}
    assert set(SPACER_PX) == {"xs", "s", "m", "l", "xl"}
    assert SPACER_PX["xs"] < SPACER_PX["xl"]
    assert tokens.radius_sm.endswith("px")


def test_default_tokens_follows_the_requested_variant() -> None:
    assert default_tokens().variant is ThemeVariant.LIGHT
    assert default_tokens(ThemeVariant.DARK).variant is ThemeVariant.DARK


def test_for_theme_returns_the_matching_variant() -> None:
    light = default_light_tokens()
    assert light.for_theme(ThemeVariant.DARK).variant is ThemeVariant.DARK
    assert light.for_theme(ThemeVariant.LIGHT).variant is ThemeVariant.LIGHT


def test_bundled_font_families_are_named() -> None:
    assert FONT_FAMILIES == ("Inter", "Vazirmatn")


def test_contrast_ratio_handles_a_broken_colour() -> None:
    tokens = default_light_tokens()
    assert tokens.contrast_ratio("#zzz", tokens.background) == 0.0
    assert tokens.contrast_ratio(tokens.text_primary, "not a colour") == 0.0


def test_contrast_ratio_is_symmetric() -> None:
    tokens = default_dark_tokens()
    forward = tokens.contrast_ratio(tokens.text_on_accent, tokens.accent_primary)
    backward = tokens.contrast_ratio(tokens.accent_primary, tokens.text_on_accent)
    assert forward == pytest.approx(backward)
