"""Design tokens for PyPackHub's light and dark themes.

These tokens are the single source of truth for the application's visual style.
They are plain Python data, not Qt objects, so they can be inspected by the
core, by tests, and by the theme manager without importing PySide6.

The colours are the ones named in ``SPEC.md`` section 7. Both variants expose the
same set of role names, so the QSS builder and the widgets never branch on the
theme: they ask for a role and get a colour. That is what makes the live theme
switch a single re-application of a stylesheet rather than a walk over the widget
tree.

Typography, spacing and radii live here too. They are the values the QSS builder
emits, and the ones the widget layer will read when it needs a pixel number
rather than a stylesheet rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

__all__ = [
    "ACCENT_ROLE_TOKENS",
    "BODY_FONT_SIZE_PX",
    "CORNER_RADIUS_PX",
    "FONT_FAMILIES",
    "MONOSPACE_FONT_FAMILY",
    "SPACER_PX",
    "TABLE_FONT_SIZE_PX",
    "TITLE_FONT_SIZE_PX",
    "AccentRole",
    "CornerRadius",
    "DesignTokens",
    "FontSize",
    "Spacer",
    "ThemeVariant",
    "default_dark_tokens",
    "default_light_tokens",
    "default_tokens",
]


class ThemeVariant(str, Enum):
    LIGHT = "light"
    DARK = "dark"


class AccentRole(str, Enum):
    """Semantic roles a widget can ask a theme to colour.

    Badges in SPEC section 7 are defined in terms of these roles
    (up-to-date=success, outdated=warning, vulnerable=danger, orphan=info,
    pinned=accent), so the widget layer resolves a badge's colour through this
    enum instead of picking hex values itself.
    """

    UP_TO_DATE = "success"
    OUTDATED = "warning"
    VULNERABLE = "danger"
    ORPHAN = "info"
    PINNED = "accent"
    SUSPICIOUS = "warning"
    NEUTRAL = "muted"


class FontSize(str, Enum):
    XS = "xs"
    S = "s"
    M = "m"
    L = "l"
    XL = "xl"
    DISPLAY = "display"


class Spacer(str, Enum):
    XS = "xs"
    S = "s"
    M = "m"
    L = "l"
    XL = "xl"


class CornerRadius(str, Enum):
    SM = "sm"
    M = "m"
    L = "l"
    FULL = "full"


#: UI font family, then the Persian family, then the log/version family.
#: All three fonts are bundled in ``pypackhub/assets/fonts`` and registered by
#: :mod:`pypackhub.ui.app`.
FONT_FAMILIES: Final[tuple[str, ...]] = ("Inter", "Vazirmatn")
MONOSPACE_FONT_FAMILY: Final[str] = "JetBrains Mono"

#: Typography scale from SPEC section 7: 13px body, 12px tables, 20px titles.
BODY_FONT_SIZE_PX: Final[int] = 13
TABLE_FONT_SIZE_PX: Final[int] = 12
TITLE_FONT_SIZE_PX: Final[int] = 20

SPACER_PX: Final[dict[str, int]] = {
    Spacer.XS: 4,
    Spacer.S: 8,
    Spacer.M: 12,
    Spacer.L: 16,
    Spacer.XL: 24,
}

CORNER_RADIUS_PX: Final[dict[str, int]] = {
    CornerRadius.SM: 6,
    CornerRadius.M: 10,
    CornerRadius.L: 14,
    CornerRadius.FULL: 999,
}

#: Badge role -> design token attribute name. Used by the widget layer and the
#: QSS builder so a badge never hard-codes a colour.
ACCENT_ROLE_TOKENS: Final[dict[AccentRole, str]] = {
    AccentRole.UP_TO_DATE: "success",
    AccentRole.OUTDATED: "warning",
    AccentRole.VULNERABLE: "danger",
    AccentRole.ORPHAN: "info",
    AccentRole.PINNED: "accent_primary",
    AccentRole.SUSPICIOUS: "warning",
    AccentRole.NEUTRAL: "text_muted",
}


@dataclass(slots=True)
class DesignTokens:
    """One complete theme variant as a set of design tokens.

    Field names are stable across both variants: the QSS builder, the widgets and
    the tests all rely on that, and ``tests/ui`` checks the two variants expose
    the same set of roles.
    """

    variant: ThemeVariant

    # Surfaces, from the window background up to the elevated popups.
    background: str
    background_secondary: str
    background_elevated: str
    surface: str
    surface_hovered: str
    surface_pressed: str
    surface_selected: str

    # Text.
    text_primary: str
    text_secondary: str
    text_muted: str
    text_on_accent: str

    # Lines.
    border: str
    border_subtle: str
    divider: str

    # Accents and semantic colours.
    accent_primary: str
    accent_primary_hover: str
    accent_primary_pressed: str
    accent_secondary: str
    success: str
    warning: str
    danger: str
    info: str

    # Elevation. Rendered as a border/underline in Qt stylesheets; the blur the
    # spec asks for is drawn by the child widgets that paint a shadow.
    shadow_sm: str
    shadow_md: str

    # Geometry.
    radius_sm: str
    radius_m: str
    radius_l: str

    def color_for_role(self, role: AccentRole) -> str:
        """Return the colour token that backs a semantic badge role."""
        return getattr(self, ACCENT_ROLE_TOKENS[role])

    def for_theme(self, variant: ThemeVariant) -> DesignTokens:
        """Return the token set for the requested variant."""
        if variant is ThemeVariant.DARK:
            return default_dark_tokens()
        return default_light_tokens()

    def contrast_ratio(self, foreground: str, background: str) -> float:
        """Approximate WCAG contrast ratio for two hex colours.

        The ratio is symmetric - it does not matter which of the two is the text -
        so the lighter luminance is always the numerator. An unparseable colour
        returns ``0.0``: no contrast can be claimed for a colour that is not a
        colour, and a test asserting unreadability should pass rather than get a
        meaningless number.

        This is a best-effort helper: it is good enough to let the test-suite
        assert that primary text on a surface stays legible in both variants, and
        to catch the most obvious contrast mistakes. It is not a full
        accessibility audit, and it does not pretend to be one.
        """
        fg = _relative_luminance(foreground)
        bg = _relative_luminance(background)
        if fg is None or bg is None:
            return 0.0

        lighter, darker = max(fg, bg), min(fg, bg)
        return (lighter + 0.05) / (darker + 0.05)


def default_light_tokens() -> DesignTokens:
    """The light theme, with the palette SPEC section 7 names."""
    return DesignTokens(
        variant=ThemeVariant.LIGHT,
        background="#F5F7FB",
        background_secondary="#EEF2F8",
        background_elevated="#FFFFFF",
        surface="#FFFFFF",
        surface_hovered="#EEF2F8",
        surface_pressed="#E3E9F4",
        surface_selected="#DCE6FF",
        text_primary="#1B2230",
        text_secondary="#566074",
        text_muted="#8A94A8",
        text_on_accent="#FFFFFF",
        border="#DDE3EE",
        border_subtle="#E8EDF5",
        divider="#E8EDF5",
        accent_primary="#3B6EF5",
        accent_primary_hover="#2F5AD1",
        accent_primary_pressed="#274BAB",
        accent_secondary="#566074",
        success="#16A34A",
        warning="#D97706",
        danger="#DC2626",
        info="#0284C7",
        shadow_sm="rgba(27, 34, 48, 0.06)",
        shadow_md="rgba(27, 34, 48, 0.10)",
        radius_sm="6px",
        radius_m="10px",
        radius_l="14px",
    )


def default_dark_tokens() -> DesignTokens:
    """The dark theme, with the palette SPEC section 7 names."""
    return DesignTokens(
        variant=ThemeVariant.DARK,
        background="#0F1218",
        background_secondary="#171B24",
        background_elevated="#1F2430",
        surface="#171B24",
        surface_hovered="#1F2430",
        surface_pressed="#12161D",
        surface_selected="#24304C",
        text_primary="#E6EAF2",
        text_secondary="#9AA4B8",
        text_muted="#667085",
        text_on_accent="#0F1218",
        border="#2A3140",
        border_subtle="#222836",
        divider="#222836",
        accent_primary="#5B8CFF",
        accent_primary_hover="#7AA2FF",
        accent_primary_pressed="#446FD6",
        accent_secondary="#9AA4B8",
        success="#34D399",
        warning="#FBBF24",
        danger="#F87171",
        info="#38BDF8",
        shadow_sm="rgba(0, 0, 0, 0.35)",
        shadow_md="rgba(0, 0, 0, 0.45)",
        radius_sm="6px",
        radius_m="10px",
        radius_l="14px",
    )


def default_tokens(variant: ThemeVariant = ThemeVariant.LIGHT) -> DesignTokens:
    """The default token set for a variant, defaulting to light.

    There is no "system" variant here: :class:`ThemeVariant` only has a concrete
    light and dark. ``ColorTheme.SYSTEM`` is resolved to one of them by
    :mod:`pypackhub.ui.theme.theme_manager` (which asks Qt for the operating
    system preference) before it ever reaches this function.
    """
    return default_dark_tokens() if variant is ThemeVariant.DARK else default_light_tokens()


def _srgb_component(byte: int) -> float:
    value = byte / 255.0
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float | None:
    """Relative luminance of a ``#rrggbb`` colour, or ``None`` when unparseable."""
    cleaned = hex_color.strip().lstrip("#")
    if len(cleaned) != 6:
        return None

    try:
        r = int(cleaned[0:2], 16)
        g = int(cleaned[2:4], 16)
        b = int(cleaned[4:6], 16)
    except ValueError:
        return None

    return 0.2126 * _srgb_component(r) + 0.7152 * _srgb_component(g) + 0.0722 * _srgb_component(b)
