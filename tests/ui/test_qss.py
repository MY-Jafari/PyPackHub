"""The generated stylesheets.

The stylesheet is the only thing that actually paints the window, so these tests
check the parts a human eye would otherwise catch: every token reaches the sheet,
every badge role has a rule, the two variants differ, and the output is
deterministic.
"""

from __future__ import annotations

import dataclasses

import pytest

from pypackhub.ui.theme.design_tokens import (
    AccentRole,
    DesignTokens,
    default_dark_tokens,
    default_light_tokens,
)
from pypackhub.ui.theme.qss import BADGE_OBJECT_NAMES, build_qss

LIGHT = default_light_tokens()
DARK = default_dark_tokens()

HEX_FIELDS = [
    field.name
    for field in dataclasses.fields(DesignTokens)
    if not field.name.startswith(("radius", "shadow", "variant"))
]


@pytest.mark.parametrize("tokens", [LIGHT, DARK], ids=lambda tokens: tokens.variant.value)
def test_every_colour_token_reaches_the_stylesheet(tokens: DesignTokens) -> None:
    stylesheet = build_qss(tokens)
    for field in HEX_FIELDS:
        assert getattr(tokens, field) in stylesheet, f"{field} is missing from the sheet"


@pytest.mark.parametrize("tokens", [LIGHT, DARK], ids=lambda tokens: tokens.variant.value)
def test_radius_tokens_reach_the_stylesheet(tokens: DesignTokens) -> None:
    stylesheet = build_qss(tokens)
    assert tokens.radius_m in stylesheet
    assert tokens.radius_l in stylesheet


def test_no_unresolved_token_reference_leaks_into_the_sheet() -> None:
    stylesheet = build_qss(LIGHT)
    assert "tokens." not in stylesheet
    # An f-string escape that was never resolved would show up as a doubled brace.
    assert "{{" not in stylesheet
    assert "}}" not in stylesheet


def test_sheet_is_deterministic() -> None:
    assert build_qss(DARK) == build_qss(default_dark_tokens())


def test_light_and_dark_sheets_differ() -> None:
    assert build_qss(LIGHT) != build_qss(DARK)


def test_every_badge_role_gets_a_rule() -> None:
    stylesheet = build_qss(DARK)
    for object_name, role in BADGE_OBJECT_NAMES.items():
        assert f"#{object_name}" in stylesheet
        assert DARK.color_for_role(role) in stylesheet


def test_all_badge_roles_are_covered() -> None:
    assert set(BADGE_OBJECT_NAMES.values()) == set(AccentRole)


def test_core_surfaces_are_styled() -> None:
    stylesheet = build_qss(LIGHT)
    for selector in (
        "QMainWindow",
        "#sidebar",
        "#navButton",
        "#topBar",
        "#logConsole",
        "QTableView",
        "QScrollBar:vertical",
        "QStatusBar",
    ):
        assert selector in stylesheet, f"{selector} has no rule"


def test_the_settings_page_and_its_fields_are_styled() -> None:
    """A styled QComboBox takes its height from the sheet, so it needs a floor.

    Without a ``min-height`` the settings combos rendered as thin bars - the
    package-manager choice was the one that made it obvious - so the rule is part
    of the contract rather than a detail of one page.
    """
    sheet = build_qss(LIGHT)

    assert "QComboBox {" in sheet
    assert "min-height: 26px" in sheet
    for selector in (
        "#settingsScroll",
        "#settingsBody",
        "QGroupBox#settingsGroup",
        "QGroupBox#settingsGroup::title",
        "#settingsLabel",
        "#settingsHint",
        "#settingsCombo",
        "#settingsLineEdit",
        "#settingsSpinBox",
    ):
        assert selector in sheet, f"{selector} has no rule"


def test_fonts_are_named_in_the_stylesheet() -> None:
    stylesheet = build_qss(LIGHT)
    assert '"Inter"' in stylesheet
    assert '"JetBrains Mono"' in stylesheet


def test_the_sidebar_divider_follows_the_layout_direction() -> None:
    # Qt mirrors widget order by itself, but a rule that says "border-right" is
    # not a logical edge, so the sheet has to be built per direction.
    ltr = build_qss(LIGHT)
    rtl = build_qss(LIGHT, rtl=True)
    assert "border-right: 1px solid" in ltr
    assert "border-left: 1px solid" in rtl
    assert "text-align: left" in ltr
    assert "text-align: right" in rtl
    assert ltr != rtl


def test_direction_only_changes_the_edges_it_must() -> None:
    ltr = build_qss(DARK).splitlines()
    rtl = build_qss(DARK, rtl=True).splitlines()
    assert len(ltr) == len(rtl)
    differing = [line for line in ltr if line not in rtl]
    assert all("border-" in line or "text-align" in line for line in differing)
