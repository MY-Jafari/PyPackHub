"""Qt stylesheets, generated from the design tokens.

SPEC section 4 lists ``dark.qss`` and ``light.qss`` next to the design tokens.
This project keeps the single source of truth in
:mod:`pypackhub.ui.theme.design_tokens` and *builds* those stylesheets instead of
hand-maintaining two files that would drift from the tokens and from each other.
The generated sheets are the same two sheets the spec asks for; they are simply
produced by :func:`build_qss` rather than checked in.

The builder covers the widgets the application actually styles: the window and
its panels, the sidebar navigation, the top bar, buttons in their three
variants, inputs, tables, badges, the log console and the scrollbars. Every
colour, radius and spacing value comes from a token, so switching the variant is
one call to :func:`pypackhub.ui.theme.theme_manager.ThemeManager.apply`.
"""

from __future__ import annotations

from typing import Final

from pypackhub.ui.theme.design_tokens import (
    BODY_FONT_SIZE_PX,
    FONT_FAMILIES,
    MONOSPACE_FONT_FAMILY,
    TABLE_FONT_SIZE_PX,
    TITLE_FONT_SIZE_PX,
    AccentRole,
    DesignTokens,
)

__all__ = [
    "BADGE_OBJECT_NAMES",
    "build_qss",
]

#: Widget object names that render as a badge, mapped to their semantic role.
#: Kept next to the builder so the stylesheet and the widget layer agree on the
#: names, and so tests can assert every role has a rule.
BADGE_OBJECT_NAMES: Final[dict[str, AccentRole]] = {
    "badgeUpToDate": AccentRole.UP_TO_DATE,
    "badgeOutdated": AccentRole.OUTDATED,
    "badgeVulnerable": AccentRole.VULNERABLE,
    "badgeOrphan": AccentRole.ORPHAN,
    "badgePinned": AccentRole.PINNED,
    "badgeSuspicious": AccentRole.SUSPICIOUS,
    "badgeNeutral": AccentRole.NEUTRAL,
}


def build_qss(tokens: DesignTokens, *, rtl: bool = False) -> str:
    """Return the stylesheet for a token set and a layout direction.

    Qt mirrors widget order and alignment by itself when the application's layout
    direction is right-to-left, but it has no notion of a logical border: a rule
    that says ``border-right`` stays on the right. Those few rules - the sidebar
    divider and the alignment of the navigation labels - are therefore emitted per
    direction, which is what makes the Persian layout an actual mirror.

    The output is deterministic: the same tokens and direction always produce the
    same text, so a test can hash it and a diff between two themes stays readable.
    """
    return "\n".join(_sections(tokens, rtl=rtl))


def _sections(tokens: DesignTokens, *, rtl: bool) -> list[str]:
    sections: list[str] = [
        _base(tokens),
        _window(tokens),
        _sidebar(tokens, rtl=rtl),
        _topbar(tokens),
        _labels(tokens),
        _buttons(tokens),
        _inputs(tokens),
        _settings(tokens),
        _tables(tokens),
        _badges(tokens),
        _log_console(tokens),
        _scrollbars(tokens),
        _misc(tokens),
    ]
    return [section for section in sections if section]


def _base(tokens: DesignTokens) -> str:
    families = ", ".join(f'"{family}"' for family in FONT_FAMILIES)
    return f"""\
QWidget {{
    background-color: transparent;
    color: {tokens.text_primary};
    font-family: {families};
    font-size: {BODY_FONT_SIZE_PX}px;
}}

QMainWindow, QDialog {{
    background-color: {tokens.background};
}}

QToolTip {{
    background-color: {tokens.background_elevated};
    color: {tokens.text_primary};
    border: 1px solid {tokens.border};
    border-radius: {tokens.radius_sm};
    padding: 6px 8px;
}}"""


def _window(tokens: DesignTokens) -> str:
    return f"""\
#contentRoot {{
    background-color: {tokens.background};
}}

#contentPanel, #detailPanel {{
    background-color: {tokens.surface};
    border: 1px solid {tokens.border_subtle};
    border-radius: {tokens.radius_l};
}}

#emptyStateTitle {{
    font-size: {TITLE_FONT_SIZE_PX}px;
    font-weight: 600;
    color: {tokens.text_primary};
}}

#emptyStateBody {{
    color: {tokens.text_muted};
}}"""


def _sidebar(tokens: DesignTokens, *, rtl: bool) -> str:
    # The divider sits between the sidebar and the content, so it follows the
    # layout direction rather than the screen.
    divider_side = "left" if rtl else "right"
    # text-align is a *logical* value: Qt resolves "left" as AlignLeading,
    # which mirrors to the physical right under a right-to-left layout
    # direction - the reading edge, exactly where nav labels belong. Saying
    # "right" for Persian - the obvious-looking compensation - mirrors
    # twice and pins the text to the physical *left* of each button, which
    # is precisely the bug this rule used to carry: the sidebar moved to
    # the right and its labels stayed on the far left. The value therefore
    # stays "left" in both directions and lets Qt do the mirroring; the
    # border above is a physical edge and keeps its per-direction flip.
    label_alignment = "left"

    return f"""\
#sidebar {{
    background-color: {tokens.background_secondary};
    border-{divider_side}: 1px solid {tokens.border_subtle};
}}

#sidebarTitle {{
    font-size: {TITLE_FONT_SIZE_PX}px;
    font-weight: 700;
    color: {tokens.text_primary};
    padding: 12px 16px 6px 16px;
}}

#sidebarSubtitle {{
    color: {tokens.text_muted};
    padding: 0 16px 14px 16px;
}}

#navButton {{
    background-color: transparent;
    border: none;
    border-radius: {tokens.radius_m};
    color: {tokens.text_secondary};
    padding: 7px 14px;
    text-align: {label_alignment};
}}

#navButton:hover {{
    background-color: {tokens.surface_hovered};
    color: {tokens.text_primary};
}}

#navButton:checked {{
    background-color: {tokens.surface_selected};
    color: {tokens.accent_primary};
    font-weight: 600;
}}

#navButton:disabled {{
    color: {tokens.text_muted};
}}"""


def _topbar(tokens: DesignTokens) -> str:
    return f"""\
#topBar {{
    background-color: {tokens.background_secondary};
    border-bottom: 1px solid {tokens.border_subtle};
}}

#environmentLabel {{
    color: {tokens.text_muted};
}}

#environmentSwitcher {{
    background-color: {tokens.surface};
    border: 1px solid {tokens.border};
    border-radius: {tokens.radius_m};
    padding: 6px 10px;
    min-height: 24px;
}}"""


def _labels(tokens: DesignTokens) -> str:
    return f"""\
#pageTitle {{
    font-size: {TITLE_FONT_SIZE_PX}px;
    font-weight: 600;
}}

#pageSubtitle, #secondaryText {{
    color: {tokens.text_secondary};
}}

#mutedText {{
    color: {tokens.text_muted};
}}

#errorText {{
    color: {tokens.text_on_accent};
    background-color: {tokens.danger};
    border: 1px solid {tokens.danger};
    border-radius: {tokens.radius_sm};
    padding: 8px 10px;
    font-weight: 500;
}}

#safetyNotice {{
    background-color: {tokens.background_elevated};
    border: 1px solid {tokens.warning};
    border-radius: {tokens.radius_m};
    color: {tokens.text_primary};
    padding: 10px 12px;
}}"""


def _buttons(tokens: DesignTokens) -> str:
    return f"""\
QPushButton, QToolButton {{
    background-color: {tokens.surface};
    border: 1px solid {tokens.border};
    border-radius: {tokens.radius_m};
    color: {tokens.text_primary};
    min-height: 26px;
    padding: 7px 14px;
}}

QPushButton:hover, QToolButton:hover {{
    background-color: {tokens.surface_hovered};
}}

QPushButton:pressed, QToolButton:pressed {{
    background-color: {tokens.surface_pressed};
}}

QPushButton:disabled, QToolButton:disabled {{
    color: {tokens.text_muted};
    border-color: {tokens.border_subtle};
}}

QPushButton#primaryButton, QToolButton#primaryButton {{
    background-color: {tokens.accent_primary};
    border-color: {tokens.accent_primary};
    color: {tokens.text_on_accent};
    font-weight: 600;
}}

QPushButton#primaryButton:hover, QToolButton#primaryButton:hover {{
    background-color: {tokens.accent_primary_hover};
    border-color: {tokens.accent_primary_hover};
}}

QPushButton#primaryButton:pressed, QToolButton#primaryButton:pressed {{
    background-color: {tokens.accent_primary_pressed};
}}

QPushButton#dangerButton, QToolButton#dangerButton {{
    background-color: {tokens.danger};
    border-color: {tokens.danger};
    color: {tokens.text_on_accent};
    font-weight: 600;
}}

QPushButton#ghostButton, QToolButton#ghostButton {{
    background-color: transparent;
    border-color: transparent;
    color: {tokens.accent_primary};
    padding: 4px 8px;
}}

QPushButton#ghostButton:hover, QToolButton#ghostButton:hover {{
    background-color: {tokens.surface_hovered};
}}"""


def _inputs(tokens: DesignTokens) -> str:
    return f"""\
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background-color: {tokens.surface};
    border: 1px solid {tokens.border};
    border-radius: {tokens.radius_m};
    padding: 6px 10px;
    selection-background-color: {tokens.accent_primary};
    selection-color: {tokens.text_on_accent};
}}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {tokens.accent_primary};
}}

QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    color: {tokens.text_muted};
    background-color: {tokens.background_secondary};
}}

/* A styled QComboBox takes its height from the style sheet, and with no
   min-height that is a few pixels: the control renders as a thin bar that is
   impossible to aim at. Every combo in the application - the environment
   switcher, the settings fields - is a real control because of this rule. */
QComboBox {{
    min-height: 26px;
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}

QComboBox QAbstractItemView {{
    background-color: {tokens.background_elevated};
    border: 1px solid {tokens.border};
    selection-background-color: {tokens.surface_selected};
    selection-color: {tokens.text_primary};
}}

QCheckBox, QRadioButton {{
    spacing: 8px;
}}

QCheckBox::indicator, QRadioButton::indicator {{
    border: 1px solid {tokens.border};
    height: 15px;
    width: 15px;
}}

QCheckBox::indicator:checked {{
    background-color: {tokens.accent_primary};
    border-color: {tokens.accent_primary};
}}

#searchInput {{
    min-height: 26px;
}}"""


def _settings(tokens: DesignTokens) -> str:
    """The settings page: cards, aligned labels and readable hints.

    The page is the one screen made almost entirely of form fields, so it is the
    one place where a control with no width or height of its own is obvious: the
    package-manager choice looked like an empty slot because nothing gave it a
    shape. The field rules below give every settings control the same floor, so
    the page reads as one column of fields instead of a column of accidents.
    """
    return f"""\
#settingsScroll, #settingsBody {{
    background-color: {tokens.background};
    border: none;
}}

QGroupBox#settingsGroup {{
    background-color: {tokens.surface};
    border: 1px solid {tokens.border_subtle};
    border-radius: {tokens.radius_l};
    margin-top: 14px;
    padding: 16px 14px 14px 14px;
}}

QGroupBox#settingsGroup::title {{
    color: {tokens.text_secondary};
    font-weight: 600;
    left: 12px;
    padding: 0 6px;
}}

#settingsLabel {{
    color: {tokens.text_secondary};
}}

#settingsHint {{
    color: {tokens.text_muted};
    font-size: {TABLE_FONT_SIZE_PX}px;
}}

#settingsCombo, #settingsLineEdit, #settingsSpinBox {{
    min-height: 26px;
    min-width: 220px;
}}

#settingsCheckBox {{
    spacing: 8px;
}}"""


def _tables(tokens: DesignTokens) -> str:
    return f"""\
QTableView, QTreeView, QListView {{
    background-color: {tokens.surface};
    alternate-background-color: {tokens.background_secondary};
    border: 1px solid {tokens.border_subtle};
    border-radius: {tokens.radius_m};
    font-size: {TABLE_FONT_SIZE_PX}px;
    gridline-color: {tokens.divider};
    selection-background-color: {tokens.surface_selected};
    selection-color: {tokens.text_primary};
}}

QTableView::item, QTreeView::item, QListView::item {{
    padding: 5px 8px;
}}

QTableView::item:hover, QTreeView::item:hover, QListView::item:hover {{
    background-color: {tokens.surface_hovered};
}}

QHeaderView::section {{
    background-color: {tokens.background_secondary};
    border: none;
    border-bottom: 1px solid {tokens.border};
    color: {tokens.text_secondary};
    font-size: {TABLE_FONT_SIZE_PX}px;
    font-weight: 600;
    padding: 6px 8px;
}}

QTableCornerButton::section {{
    background-color: {tokens.background_secondary};
    border: none;
}}"""


def _badges(tokens: DesignTokens) -> str:
    rules: list[str] = []
    for object_name, role in BADGE_OBJECT_NAMES.items():
        colour = tokens.color_for_role(role)
        rules.append(f"""#{object_name} {{
    background-color: {colour};
    border: none;
    border-radius: {tokens.radius_sm};
    color: {tokens.text_on_accent};
    font-size: {TABLE_FONT_SIZE_PX}px;
    font-weight: 600;
    padding: 2px 8px;
}}""")
    return "\n\n".join(rules)


def _log_console(tokens: DesignTokens) -> str:
    return f"""\
#logConsole {{
    background-color: {tokens.background};
    border: 1px solid {tokens.border_subtle};
    border-radius: {tokens.radius_m};
    color: {tokens.text_secondary};
    font-family: "{MONOSPACE_FONT_FAMILY}", monospace;
    font-size: {TABLE_FONT_SIZE_PX}px;
}}

#versionLabel, #commandPreview {{
    font-family: "{MONOSPACE_FONT_FAMILY}", monospace;
    font-size: {TABLE_FONT_SIZE_PX}px;
    background-color: {tokens.background};
    border: 1px solid {tokens.border};
    border-radius: {tokens.radius_sm};
    padding: 8px 10px;
}}

#logHeader {{
    color: {tokens.text_muted};
    font-size: {TABLE_FONT_SIZE_PX}px;
}}"""


def _scrollbars(tokens: DesignTokens) -> str:
    return f"""\
QScrollBar:vertical {{
    background: transparent;
    margin: 0;
    width: 10px;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}

QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {tokens.border};
    border-radius: {tokens.radius_sm};
    min-height: 28px;
    min-width: 28px;
}}

QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
    background: {tokens.text_muted};
}}

QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}

QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}"""


def _misc(tokens: DesignTokens) -> str:
    return f"""\
QSplitter::handle {{
    background-color: {tokens.divider};
}}

QSplitter::handle:hover {{
    background-color: {tokens.accent_primary};
}}

QProgressBar {{
    background-color: {tokens.background_secondary};
    border: none;
    border-radius: {tokens.radius_sm};
    color: {tokens.text_secondary};
    text-align: center;
}}

QProgressBar::chunk {{
    background-color: {tokens.accent_primary};
    border-radius: {tokens.radius_sm};
}}

QStatusBar {{
    background-color: {tokens.background_secondary};
    border-top: 1px solid {tokens.border_subtle};
    color: {tokens.text_muted};
}}

QGroupBox {{
    border: 1px solid {tokens.border_subtle};
    border-radius: {tokens.radius_m};
    margin-top: 12px;
    padding: 12px;
}}

QGroupBox::title {{
    color: {tokens.text_secondary};
    left: 10px;
    padding: 0 4px;
}}

QMenuBar, QMenu {{
    background-color: {tokens.background_secondary};
    color: {tokens.text_primary};
}}

QMenuBar::item:selected, QMenu::item:selected {{
    background-color: {tokens.surface_selected};
}}

QMenu {{
    border: 1px solid {tokens.border};
    border-radius: {tokens.radius_sm};
    padding: 4px;
}}"""
