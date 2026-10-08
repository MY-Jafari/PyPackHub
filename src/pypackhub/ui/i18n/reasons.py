"""Translated text for the core's machine-readable reason codes.

The core refuses input and policy decisions with a stable code, never with a
sentence (see :class:`pypackhub.core.validation.Reason` and the ``reason_code``
on :class:`pypackhub.core.platform_guard.ModificationPolicy`). That split is what
lets the warning a user sees be fully translated while the decision itself stays
testable and language-independent.

This module is the other half of that contract: one table per language, keyed by
the code. ``tests/ui/test_i18n.py`` walks the enums and asserts every code has an
entry, so adding a reason without a translation fails the build rather than
showing up as an English sentence inside a Persian window.
"""

from __future__ import annotations

from typing import Final

from pypackhub.core.settings import Language

__all__ = ["reason_text"]

_VALIDATION_EN: Final[dict[str, str]] = {
    "empty": "The value is empty.",
    "too_long": "The value is too long.",
    "control_character": "The value contains a control character.",
    "non_ascii": "The value contains characters that are not allowed here.",
    "variation_selector": "The value contains an invisible variation selector.",
    "option_injection": "The value looks like a command-line option, not a package.",
    "shell_metacharacter": "The value contains a character a shell would interpret.",
    "invalid_name": "That is not a valid package name.",
    "invalid_specifier": "That is not a valid version specifier.",
    "invalid_version": "That is not a valid version.",
    "direct_reference_not_allowed": (
        "Direct references (URLs and VCS paths) are disabled. Turn on advanced installs and "
        "review the warning first."
    ),
    "marker_not_allowed": "Environment markers are not accepted here.",
    "insecure_scheme": "Only HTTPS is allowed.",
    "unsupported_scheme": "That URL scheme is not supported.",
    "embedded_credentials": "The URL contains a username and password.",
    "missing_host": "The URL has no host.",
    "not_found": "That path does not exist.",
    "not_a_directory": "That path is not a directory.",
    "not_verified_environment": "That directory is not a verified environment.",
    "forbidden_target": "That target may never be modified or deleted.",
    "outside_root": "That path is outside the environment it claims to belong to.",
    "unsupported": "That option does not apply to this action.",
}

_VALIDATION_FA: Final[dict[str, str]] = {
    "empty": "مقدار خالی است.",
    "too_long": "مقدار بیش از اندازه بلند است.",
    "control_character": "مقدار دارای نویسهٔ کنترلی است.",
    "non_ascii": "مقدار دارای نویسه‌هایی است که اینجا مجاز نیستند.",
    "variation_selector": "مقدار دارای نشانگر تغییرِ نامرئی است.",
    "option_injection": "این مقدار شبیه یک گزینهٔ خط فرمان است، نه نام بسته.",
    "shell_metacharacter": "مقدار دارای نویسه‌ای است که پوسته آن را تفسیر می‌کند.",
    "invalid_name": "این نام بستهٔ معتبری نیست.",
    "invalid_specifier": "این مشخصهٔ نسخهٔ معتبری نیست.",
    "invalid_version": "این نسخهٔ معتبری نیست.",
    "direct_reference_not_allowed": (
        "ارجاع مستقیم (نشانی اینترنتی یا مخزن) غیرفعال است. نخست نصب پیشرفته را روشن کنید و "
        "هشدار را بخوانید."
    ),
    "marker_not_allowed": "نشانگرهای محیطی اینجا پذیرفته نمی‌شوند.",
    "insecure_scheme": "تنها HTTPS مجاز است.",
    "unsupported_scheme": "این نوع نشانی پشتیبانی نمی‌شود.",
    "embedded_credentials": "نشانی دارای نام کاربری و گذرواژه است.",
    "missing_host": "نشانی میزبان ندارد.",
    "not_found": "این مسیر وجود ندارد.",
    "not_a_directory": "این مسیر یک پوشه نیست.",
    "not_verified_environment": "این پوشه یک محیط تأییدشده نیست.",
    "forbidden_target": "این مقصد هرگز نباید تغییر کند یا حذف شود.",
    "outside_root": "این مسیر بیرون از محیطی است که ادعا می‌کند به آن تعلق دارد.",
    "unsupported": "این گزینه برای این کار کاربرد ندارد.",
}

_POLICY_EN: Final[dict[str, str]] = {
    "target.alias_stub": (
        "This is a Windows App Execution Alias stub, not a Python installation. It cannot "
        "install packages."
    ),
    "target.externally_managed": (
        "This interpreter is managed by the operating system (PEP 668), so pip refuses to "
        "install into it. PyPackHub will not pass --break-system-packages on your behalf."
    ),
    "target.base_interpreter": (
        "This is a base interpreter, not a virtual environment. Packages installed here are "
        "visible to every project that uses it."
    ),
    "target.pyenv": (
        "This environment lives inside pyenv's versions directory. It is isolated, but pyenv "
        "also owns the folder name."
    ),
    "target.isolated_environment": (
        "This environment is isolated; installing here cannot affect other projects."
    ),
}

_POLICY_FA: Final[dict[str, str]] = {
    "target.alias_stub": (
        "این یک میان‌بر ساختگی ویندوز است، نه نصب پایتون. نمی‌تواند بسته نصب کند."
    ),
    "target.externally_managed": (
        "این مفسر توسط سیستم‌عامل مدیریت می‌شود (PEP 668) و pip در آن نصب نمی‌کند. "
        "پای‌پک‌هاب به‌جای شما --break-system-packages را نمی‌فرستد."
    ),
    "target.base_interpreter": (
        "این یک مفسر پایه است، نه محیط مجازی. بسته‌های نصب‌شده در آن برای همهٔ پروژه‌هایی که "
        "از آن استفاده می‌کنند دیده می‌شوند."
    ),
    "target.pyenv": (
        "این محیط در پوشهٔ نسخه‌های pyenv قرار دارد. جدا است، اما نام پوشه هم در اختیار pyenv است."
    ),
    "target.isolated_environment": ("این محیط جدا است؛ نصب در آن به پروژه‌های دیگر آسیبی نمی‌زند."),
}

_TABLES: Final[dict[Language, dict[str, str]]] = {
    Language.EN: {**_VALIDATION_EN, **_POLICY_EN},
    Language.FA: {**_VALIDATION_FA, **_POLICY_FA},
}


def reason_text(code: str, language: Language, *, fallback: str = "") -> str:
    """Translate a reason code.

    ``fallback`` is the English sentence the core already carries (for example
    ``ModificationPolicy.explanation``). When a code has no translation the
    fallback is shown rather than the raw code, because a sentence a user cannot
    read still explains more than ``target.pyenv`` does.
    """
    table = _TABLES.get(language) or _TABLES[Language.EN]
    return table.get(code) or fallback or code
