"""Opening links from package metadata, safely.

Metadata text is untrusted input (SPEC section 11.7): a package can claim any
home page it likes. Links therefore open only when the user clicks them, only in
the system browser, and only over HTTPS. A ``javascript:`` or ``file:`` URL in a
description must never become an action, so the scheme is checked here rather
than left to the platform's URL handler.
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

__all__ = ["ALLOWED_SCHEMES", "is_openable_url", "open_external_url", "safe_url"]

#: The only scheme a package author may get a user to open. ``http`` is excluded
#: on purpose: the spec requires HTTPS for anything the application opens.
ALLOWED_SCHEMES: Final = frozenset({"https"})


def safe_url(url: str) -> str:
    """Return *url* when it is safe to open, otherwise an empty string."""
    text = (url or "").strip()
    if not text:
        return ""

    parsed = QUrl(text)
    if not parsed.isValid():
        return ""

    if str(parsed.scheme()).lower() not in ALLOWED_SCHEMES:
        return ""

    if not parsed.host():
        return ""

    return text


def is_openable_url(url: str) -> bool:
    """True when this URL would be opened rather than ignored."""
    return bool(safe_url(url))


def open_external_url(url: str) -> bool:
    """Open a URL in the system browser, or refuse it.

    Returns True when the browser was asked to open it. The user has to click the
    link first; nothing here opens a URL on its own.
    """
    checked = safe_url(url)
    if not checked:
        return False

    return QDesktopServices.openUrl(QUrl(checked))
