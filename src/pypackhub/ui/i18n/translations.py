"""Bilingual translation tables for PyPackHub.

This module is the bundle of UI strings for the two languages Phase 1 ships:
English and Persian. The tables are plain dicts so they can be inspected by tests
and by the translator without importing Qt.

Conventions:

* The English table is the source of truth. A key exists for a string the UI
  actually renders; :func:`untranslated_keys` and the i18n test-suite keep the two
  tables in step, so a missing Persian string is a test failure rather than a
  half-translated window.
* Keys are dotted, lower case, and grouped by the widget they belong to
  (``nav.``, ``topbar.``, ``settings.``). :class:`TranslationKey` names the ones
  the code references, so a typo is caught by the type checker as well.
* Placeholders are ``%(name)s`` and must be identical in every language; the
  test-suite asserts that too.
"""

from __future__ import annotations

import re
from enum import Enum

from pypackhub.core.settings import Language

__all__ = [
    "EN_TABLE",
    "FA_TABLE",
    "PLACEHOLDER_PATTERN",
    "TranslationKey",
    "get_translation_table",
    "missing_keys",
    "placeholders_in",
    "untranslated_keys",
]


class TranslationKey(str, Enum):
    """Stable keys the rest of the UI uses to ask for a translated string."""

    APP_TITLE = "app.title"
    APP_SUBTITLE = "app.subtitle"
    APP_VERSION = "app.version"
    APP_WINDOW_TITLE = "app.window_title"

    NAV_DASHBOARD = "nav.dashboard"
    NAV_PACKAGES = "nav.packages"
    NAV_ENVIRONMENTS = "nav.environments"
    NAV_DEPENDENCIES = "nav.dependencies"
    NAV_SEARCH = "nav.search"
    NAV_SECURITY = "nav.security"
    NAV_SNAPSHOTS = "nav.snapshots"
    NAV_HISTORY = "nav.history"
    NAV_LOGS = "nav.logs"
    NAV_SETTINGS = "nav.settings"

    MENU_VIEW = "menu.view"
    MENU_VIEW_SIDEBAR = "menu.view.sidebar"

    TOPBAR_ENVIRONMENT = "topbar.environment"
    TOPBAR_SEARCH_PLACEHOLDER = "topbar.search"
    TOPBAR_TOGGLE_THEME = "topbar.toggle_theme"
    TOPBAR_TOGGLE_LANGUAGE = "topbar.toggle_language"
    TOPBAR_REFRESH = "topbar.refresh"
    TOPBAR_NO_ENVIRONMENT = "topbar.no_environment"

    VIEW_EMPTY_TITLE = "view.empty_title"
    VIEW_EMPTY_BODY = "view.empty_body"
    VIEW_PLACEHOLDER_DASHBOARD = "view.placeholder.dashboard"
    VIEW_PLACEHOLDER_PACKAGES = "view.placeholder.packages"
    VIEW_PLACEHOLDER_ENVIRONMENTS = "view.placeholder.environments"
    VIEW_PLACEHOLDER_DEPENDENCIES = "view.placeholder.dependencies"
    VIEW_PLACEHOLDER_SEARCH = "view.placeholder.search"
    VIEW_PLACEHOLDER_SECURITY = "view.placeholder.security"
    VIEW_PLACEHOLDER_SNAPSHOTS = "view.placeholder.snapshots"
    VIEW_PLACEHOLDER_HISTORY = "view.placeholder.history"
    VIEW_PLACEHOLDER_LOGS = "view.placeholder.logs"
    VIEW_PLACEHOLDER_SETTINGS = "view.placeholder.settings"

    THEME_SYSTEM = "theme.system"
    THEME_LIGHT = "theme.light"
    THEME_DARK = "theme.dark"

    LANGUAGE_SYSTEM = "language.system"
    LANGUAGE_EN = "language.en"
    LANGUAGE_FA = "language.fa"

    SETTINGS_TITLE = "settings.title"
    SETTINGS_THEME = "settings.theme"
    SETTINGS_LANGUAGE = "settings.language"
    SETTINGS_APPLY = "settings.apply"
    SETTINGS_RESET = "settings.reset"
    SETTINGS_SIDEBAR_WIDTH = "settings.sidebar_width"
    SETTINGS_PACKAGE_PAGE_SIZE = "settings.package_page_size"
    SETTINGS_PYPI_INDEX = "settings.pypi_index"
    SETTINGS_SAVE = "settings.save"
    SETTINGS_CANCEL = "settings.cancel"

    PACKAGES_TITLE = "packages.title"
    PACKAGES_SEARCH = "packages.search"
    PACKAGES_INSTALL = "packages.install"
    PACKAGES_UNINSTALL = "packages.uninstall"
    PACKAGES_UPGRADE = "packages.upgrade"
    PACKAGES_UPGRADE_ALL = "packages.upgrade_all"
    PACKAGES_REFRESH = "packages.refresh"
    PACKAGES_NAME = "packages.name"
    PACKAGES_VERSION = "packages.version"
    PACKAGES_LATEST = "packages.latest"
    PACKAGES_STATUS = "packages.status"
    PACKAGES_SIZE = "packages.size"
    PACKAGES_BACKEND = "packages.backend"
    PACKAGES_ENVIRONMENT = "packages.environment"

    STATUS_OUTDATED = "status.outdated"
    STATUS_UP_TO_DATE = "status.up_to_date"
    STATUS_UNKNOWN = "status.unknown"

    CONFIRM_INSTALL_TITLE = "confirm.install.title"
    CONFIRM_INSTALL_MESSAGE = "confirm.install.message"
    CONFIRM_UNINSTALL_TITLE = "confirm.uninstall.title"
    CONFIRM_UNINSTALL_MESSAGE = "confirm.uninstall.message"
    CONFIRM_UPGRADE_TITLE = "confirm.upgrade.title"
    CONFIRM_UPGRADE_MESSAGE = "confirm.upgrade.message"

    BUTTON_OK = "button.ok"
    BUTTON_CANCEL = "button.cancel"
    BUTTON_YES = "button.yes"
    BUTTON_NO = "button.no"
    BUTTON_INSTALL = "button.install"
    BUTTON_UNINSTALL = "button.uninstall"
    BUTTON_UPGRADE = "button.upgrade"
    BUTTON_CLOSE = "button.close"

    LOG_TITLE = "log.title"
    LOG_CLEAR = "log.clear"
    LOG_SAVE = "log.save"

    TOAST_INSTALLED = "toast.installed"
    TOAST_UNINSTALLED = "toast.uninstalled"
    TOAST_UPGRADED = "toast.upgraded"
    TOAST_CANCELLED = "toast.cancelled"
    TOAST_FAILED = "toast.failed"
    TOAST_OFFLINE = "toast.offline"

    ERROR_TITLE = "error.title"
    ERROR_MESSAGE = "error.message"

    SAFETY_NOTICE = "safety.notice"
    SAFETY_SYSTEM_PYTHON = "safety.system_python"


#: Matches one ``%(name)s`` placeholder.
PLACEHOLDER_PATTERN = re.compile(r"%\(([A-Za-z_][A-Za-z0-9_]*)\)s")


EN_TABLE: dict[str, str] = {
    TranslationKey.APP_TITLE: "PyPackHub",
    TranslationKey.APP_SUBTITLE: "Python package manager",
    TranslationKey.APP_VERSION: "Version %(version)s",
    TranslationKey.APP_WINDOW_TITLE: "PyPackHub - Python package manager",
    TranslationKey.NAV_DASHBOARD: "Dashboard",
    TranslationKey.NAV_PACKAGES: "Packages",
    TranslationKey.NAV_ENVIRONMENTS: "Environments",
    TranslationKey.NAV_DEPENDENCIES: "Dependencies",
    TranslationKey.NAV_SEARCH: "Search",
    TranslationKey.NAV_SECURITY: "Security",
    TranslationKey.NAV_SNAPSHOTS: "Snapshots",
    TranslationKey.NAV_HISTORY: "History",
    TranslationKey.NAV_LOGS: "Logs",
    TranslationKey.NAV_SETTINGS: "Settings",
    TranslationKey.MENU_VIEW: "View",
    TranslationKey.MENU_VIEW_SIDEBAR: "Show or hide the sidebar",
    TranslationKey.TOPBAR_ENVIRONMENT: "Environment",
    TranslationKey.TOPBAR_SEARCH_PLACEHOLDER: "Search installed packages...",
    TranslationKey.TOPBAR_TOGGLE_THEME: "Switch theme",
    TranslationKey.TOPBAR_TOGGLE_LANGUAGE: "Switch language",
    TranslationKey.TOPBAR_REFRESH: "Refresh",
    TranslationKey.TOPBAR_NO_ENVIRONMENT: "No environment selected",
    TranslationKey.VIEW_EMPTY_TITLE: "Nothing here yet",
    TranslationKey.VIEW_EMPTY_BODY: "This view arrives in the next section of the build.",
    TranslationKey.VIEW_PLACEHOLDER_DASHBOARD: (
        "Package counts, outdated and vulnerable totals, and the environment's purpose "
        "appear here once the package list is wired up."
    ),
    TranslationKey.VIEW_PLACEHOLDER_PACKAGES: (
        "The installed-package table, its filters and its actions arrive in section 4."
    ),
    TranslationKey.VIEW_PLACEHOLDER_ENVIRONMENTS: (
        "Discovering and switching between environments arrives in phase 3."
    ),
    TranslationKey.VIEW_PLACEHOLDER_DEPENDENCIES: (
        "The dependency tree and reverse-dependency view arrive in phase 2."
    ),
    TranslationKey.VIEW_PLACEHOLDER_SEARCH: (
        "Searching PyPI and browsing a package's release history arrive in phase 2."
    ),
    TranslationKey.VIEW_PLACEHOLDER_SECURITY: (
        "Vulnerability scanning, and the snapshot taken before every fix, arrive in phase 4."
    ),
    TranslationKey.VIEW_PLACEHOLDER_SNAPSHOTS: (
        "Environment snapshots and rollback with a preview diff arrive in phase 4."
    ),
    TranslationKey.VIEW_PLACEHOLDER_HISTORY: (
        "The action history and undo arrive with snapshots in phase 4."
    ),
    TranslationKey.VIEW_PLACEHOLDER_LOGS: (
        "The live terminal panel and the persistent log file arrive with the package "
        "actions in section 4."
    ),
    TranslationKey.VIEW_PLACEHOLDER_SETTINGS: (
        "Theme, language and the remaining settings arrive with the settings page in section 5."
    ),
    TranslationKey.THEME_SYSTEM: "Follow system",
    TranslationKey.THEME_LIGHT: "Light",
    TranslationKey.THEME_DARK: "Dark",
    TranslationKey.LANGUAGE_SYSTEM: "Follow system",
    TranslationKey.LANGUAGE_EN: "English",
    TranslationKey.LANGUAGE_FA: "Persian",
    TranslationKey.SETTINGS_TITLE: "Settings",
    TranslationKey.SETTINGS_THEME: "Theme",
    TranslationKey.SETTINGS_LANGUAGE: "Language",
    TranslationKey.SETTINGS_APPLY: "Apply",
    TranslationKey.SETTINGS_RESET: "Reset",
    TranslationKey.SETTINGS_SIDEBAR_WIDTH: "Sidebar width",
    TranslationKey.SETTINGS_PACKAGE_PAGE_SIZE: "Packages per page",
    TranslationKey.SETTINGS_PYPI_INDEX: "PyPI index URL",
    TranslationKey.SETTINGS_SAVE: "Save",
    TranslationKey.SETTINGS_CANCEL: "Cancel",
    TranslationKey.PACKAGES_TITLE: "Packages",
    TranslationKey.PACKAGES_SEARCH: "Search packages...",
    TranslationKey.PACKAGES_INSTALL: "Install",
    TranslationKey.PACKAGES_UNINSTALL: "Uninstall",
    TranslationKey.PACKAGES_UPGRADE: "Upgrade",
    TranslationKey.PACKAGES_UPGRADE_ALL: "Upgrade all",
    TranslationKey.PACKAGES_REFRESH: "Refresh",
    TranslationKey.PACKAGES_NAME: "Name",
    TranslationKey.PACKAGES_VERSION: "Version",
    TranslationKey.PACKAGES_LATEST: "Latest",
    TranslationKey.PACKAGES_STATUS: "Status",
    TranslationKey.PACKAGES_SIZE: "Size",
    TranslationKey.PACKAGES_BACKEND: "Backend",
    TranslationKey.PACKAGES_ENVIRONMENT: "Environment",
    TranslationKey.STATUS_OUTDATED: "Outdated",
    TranslationKey.STATUS_UP_TO_DATE: "Up to date",
    TranslationKey.STATUS_UNKNOWN: "Unknown",
    TranslationKey.CONFIRM_INSTALL_TITLE: "Install package?",
    TranslationKey.CONFIRM_INSTALL_MESSAGE: "Install %(package)s into %(environment)s?",
    TranslationKey.CONFIRM_UNINSTALL_TITLE: "Uninstall package?",
    TranslationKey.CONFIRM_UNINSTALL_MESSAGE: "Uninstall %(package)s from %(environment)s?",
    TranslationKey.CONFIRM_UPGRADE_TITLE: "Upgrade package?",
    TranslationKey.CONFIRM_UPGRADE_MESSAGE: "Upgrade %(package)s from %(from)s to %(to)s?",
    TranslationKey.BUTTON_OK: "OK",
    TranslationKey.BUTTON_CANCEL: "Cancel",
    TranslationKey.BUTTON_YES: "Yes",
    TranslationKey.BUTTON_NO: "No",
    TranslationKey.BUTTON_INSTALL: "Install",
    TranslationKey.BUTTON_UNINSTALL: "Uninstall",
    TranslationKey.BUTTON_UPGRADE: "Upgrade",
    TranslationKey.BUTTON_CLOSE: "Close",
    TranslationKey.LOG_TITLE: "Log",
    TranslationKey.LOG_CLEAR: "Clear",
    TranslationKey.LOG_SAVE: "Save",
    TranslationKey.TOAST_INSTALLED: "Installed %(package)s",
    TranslationKey.TOAST_UNINSTALLED: "Uninstalled %(package)s",
    TranslationKey.TOAST_UPGRADED: "Upgraded %(package)s",
    TranslationKey.TOAST_CANCELLED: "Cancelled",
    TranslationKey.TOAST_FAILED: "Failed",
    TranslationKey.TOAST_OFFLINE: "Offline",
    TranslationKey.ERROR_TITLE: "Error",
    TranslationKey.ERROR_MESSAGE: "An error occurred.",
    TranslationKey.SAFETY_NOTICE: (
        "Every install, uninstall and upgrade shows the exact command before it runs."
    ),
    TranslationKey.SAFETY_SYSTEM_PYTHON: (
        "This environment is managed by the operating system. PyPackHub will not modify it; "
        "create a virtual environment instead."
    ),
}

FA_TABLE: dict[str, str] = {
    TranslationKey.APP_TITLE: "پای‌پک‌هاب",
    TranslationKey.APP_SUBTITLE: "مدیر بسته‌های پایتون",
    TranslationKey.APP_VERSION: "نسخه %(version)s",
    TranslationKey.APP_WINDOW_TITLE: "پای‌پک‌هاب - مدیر بسته‌های پایتون",
    TranslationKey.NAV_DASHBOARD: "داشبورد",
    TranslationKey.NAV_PACKAGES: "بسته‌ها",
    TranslationKey.NAV_ENVIRONMENTS: "محیط‌ها",
    TranslationKey.NAV_DEPENDENCIES: "وابستگی‌ها",
    TranslationKey.NAV_SEARCH: "جست‌وجو",
    TranslationKey.NAV_SECURITY: "امنیت",
    TranslationKey.NAV_SNAPSHOTS: "نسخه‌های پشتیبان",
    TranslationKey.NAV_HISTORY: "تاریخچه",
    TranslationKey.NAV_LOGS: "گزارش‌ها",
    TranslationKey.NAV_SETTINGS: "تنظیمات",
    TranslationKey.MENU_VIEW: "نمایش",
    TranslationKey.MENU_VIEW_SIDEBAR: "نمایش یا پنهان‌کردن نوار کناری",
    TranslationKey.TOPBAR_ENVIRONMENT: "محیط",
    TranslationKey.TOPBAR_SEARCH_PLACEHOLDER: "جست‌وجو در بسته‌های نصب‌شده...",
    TranslationKey.TOPBAR_TOGGLE_THEME: "تغییر پوسته",
    TranslationKey.TOPBAR_TOGGLE_LANGUAGE: "تغییر زبان",
    TranslationKey.TOPBAR_REFRESH: "به‌روزرسانی",
    TranslationKey.TOPBAR_NO_ENVIRONMENT: "هیچ محیطی انتخاب نشده است",
    TranslationKey.VIEW_EMPTY_TITLE: "هنوز چیزی اینجا نیست",
    TranslationKey.VIEW_EMPTY_BODY: "این بخش در گام بعدی ساخت اضافه می‌شود.",
    TranslationKey.VIEW_PLACEHOLDER_DASHBOARD: (
        "شمار بسته‌ها، تعداد بسته‌های قدیمی و آسیب‌پذیر و کاربرد احتمالی محیط، "
        "پس از آماده شدن فهرست بسته‌ها اینجا نمایش داده می‌شود."
    ),
    TranslationKey.VIEW_PLACEHOLDER_PACKAGES: (
        "جدول بسته‌های نصب‌شده، پالایه‌ها و کارهای آن در گام ۴ اضافه می‌شود."
    ),
    TranslationKey.VIEW_PLACEHOLDER_ENVIRONMENTS: (
        "شناسایی و جابه‌جایی میان محیط‌ها در فاز ۳ اضافه می‌شود."
    ),
    TranslationKey.VIEW_PLACEHOLDER_DEPENDENCIES: (
        "درخت وابستگی‌ها و نمایش وابستگی‌های معکوس در فاز ۲ اضافه می‌شود."
    ),
    TranslationKey.VIEW_PLACEHOLDER_SEARCH: (
        "جست‌وجو در پای‌پی‌آی و مرور تاریخچهٔ نسخه‌های یک بسته در فاز ۲ اضافه می‌شود."
    ),
    TranslationKey.VIEW_PLACEHOLDER_SECURITY: (
        "بررسی آسیب‌پذیری‌ها و نسخهٔ پشتیبانی که پیش از هر اصلاح گرفته می‌شود، در فاز ۴ اضافه می‌شود."
    ),
    TranslationKey.VIEW_PLACEHOLDER_SNAPSHOTS: (
        "نسخه‌های پشتیبان محیط و بازگردانی همراه با پیش‌نمایش تغییرات در فاز ۴ اضافه می‌شود."
    ),
    TranslationKey.VIEW_PLACEHOLDER_HISTORY: (
        "تاریخچهٔ کارها و بازگردانی آن‌ها همراه با نسخه‌های پشتیبان در فاز ۴ اضافه می‌شود."
    ),
    TranslationKey.VIEW_PLACEHOLDER_LOGS: (
        "پنل پایانهٔ زنده و پروندهٔ گزارش ماندگار همراه با کارهای بسته‌ها در گام ۴ اضافه می‌شود."
    ),
    TranslationKey.VIEW_PLACEHOLDER_SETTINGS: (
        "پوسته، زبان و دیگر تنظیمات همراه صفحهٔ تنظیمات در گام ۵ اضافه می‌شود."
    ),
    TranslationKey.THEME_SYSTEM: "هماهنگ با سامانه",
    TranslationKey.THEME_LIGHT: "روشن",
    TranslationKey.THEME_DARK: "تیره",
    TranslationKey.LANGUAGE_SYSTEM: "هماهنگ با سامانه",
    TranslationKey.LANGUAGE_EN: "انگلیسی",
    TranslationKey.LANGUAGE_FA: "فارسی",
    TranslationKey.SETTINGS_TITLE: "تنظیمات",
    TranslationKey.SETTINGS_THEME: "پوسته",
    TranslationKey.SETTINGS_LANGUAGE: "زبان",
    TranslationKey.SETTINGS_APPLY: "اعمال",
    TranslationKey.SETTINGS_RESET: "بازنشانی",
    TranslationKey.SETTINGS_SIDEBAR_WIDTH: "پهنای نوار کناری",
    TranslationKey.SETTINGS_PACKAGE_PAGE_SIZE: "تعداد بسته در هر صفحه",
    TranslationKey.SETTINGS_PYPI_INDEX: "نشانی نمایهٔ پای‌پی‌آی",
    TranslationKey.SETTINGS_SAVE: "ذخیره",
    TranslationKey.SETTINGS_CANCEL: "لغو",
    TranslationKey.PACKAGES_TITLE: "بسته‌ها",
    TranslationKey.PACKAGES_SEARCH: "جست‌وجوی بسته‌ها...",
    TranslationKey.PACKAGES_INSTALL: "نصب",
    TranslationKey.PACKAGES_UNINSTALL: "حذف",
    TranslationKey.PACKAGES_UPGRADE: "به‌روزرسانی",
    TranslationKey.PACKAGES_UPGRADE_ALL: "به‌روزرسانی همه",
    TranslationKey.PACKAGES_REFRESH: "به‌روزرسانی",
    TranslationKey.PACKAGES_NAME: "نام",
    TranslationKey.PACKAGES_VERSION: "نسخه",
    TranslationKey.PACKAGES_LATEST: "آخرین",
    TranslationKey.PACKAGES_STATUS: "وضعیت",
    TranslationKey.PACKAGES_SIZE: "اندازه",
    TranslationKey.PACKAGES_BACKEND: "موتور",
    TranslationKey.PACKAGES_ENVIRONMENT: "محیط",
    TranslationKey.STATUS_OUTDATED: "قدیمی",
    TranslationKey.STATUS_UP_TO_DATE: "به‌روز",
    TranslationKey.STATUS_UNKNOWN: "ناشناخته",
    TranslationKey.CONFIRM_INSTALL_TITLE: "نصب بسته؟",
    TranslationKey.CONFIRM_INSTALL_MESSAGE: "بستهٔ %(package)s در %(environment)s نصب شود؟",
    TranslationKey.CONFIRM_UNINSTALL_TITLE: "حذف بسته؟",
    TranslationKey.CONFIRM_UNINSTALL_MESSAGE: "بستهٔ %(package)s از %(environment)s حذف شود؟",
    TranslationKey.CONFIRM_UPGRADE_TITLE: "به‌روزرسانی بسته؟",
    TranslationKey.CONFIRM_UPGRADE_MESSAGE: (
        "بستهٔ %(package)s از %(from)s به %(to)s به‌روزرسانی شود؟"
    ),
    TranslationKey.BUTTON_OK: "تأیید",
    TranslationKey.BUTTON_CANCEL: "لغو",
    TranslationKey.BUTTON_YES: "بله",
    TranslationKey.BUTTON_NO: "خیر",
    TranslationKey.BUTTON_INSTALL: "نصب",
    TranslationKey.BUTTON_UNINSTALL: "حذف",
    TranslationKey.BUTTON_UPGRADE: "به‌روزرسانی",
    TranslationKey.BUTTON_CLOSE: "بستن",
    TranslationKey.LOG_TITLE: "گزارش",
    TranslationKey.LOG_CLEAR: "پاک‌کردن",
    TranslationKey.LOG_SAVE: "ذخیره",
    TranslationKey.TOAST_INSTALLED: "%(package)s نصب شد",
    TranslationKey.TOAST_UNINSTALLED: "%(package)s حذف شد",
    TranslationKey.TOAST_UPGRADED: "%(package)s به‌روز شد",
    TranslationKey.TOAST_CANCELLED: "لغو شد",
    TranslationKey.TOAST_FAILED: "ناموفق",
    TranslationKey.TOAST_OFFLINE: "بی‌اتصال",
    TranslationKey.ERROR_TITLE: "خطا",
    TranslationKey.ERROR_MESSAGE: "خطایی رخ داد.",
    TranslationKey.SAFETY_NOTICE: (
        "هر نصب، حذف یا به‌روزرسانی، دستور دقیق خود را پیش از اجرا نشان می‌دهد."
    ),
    TranslationKey.SAFETY_SYSTEM_PYTHON: (
        "این محیط توسط سیستم‌عامل مدیریت می‌شود. پای‌پک‌هاب آن را تغییر نمی‌دهد؛ "
        "در عوض یک محیط مجازی بسازید."
    ),
}


def get_translation_table(language: Language) -> dict[str, str]:
    """Return the translation table for the requested language.

    ``SYSTEM`` resolves to English: the OS locale could be anything, and English
    is the language the UI is authored in, so it is the honest fallback.
    """
    if language is Language.FA:
        return FA_TABLE
    return EN_TABLE


def missing_keys(language: Language) -> set[str]:
    """Return the English keys that *language* does not translate yet."""
    table = get_translation_table(language)
    return {key for key in EN_TABLE if not table.get(key)}


def untranslated_keys(language: Language) -> set[str]:
    """Return the keys whose translation is identical to the English string.

    A handful of strings are legitimately identical in both languages
    (``PyPackHub``, ``Ctrl``, ``OK``), so this is a review aid, not a hard gate;
    the test that uses it keeps an explicit allowlist.
    """
    table = get_translation_table(language)
    return {key for key, value in EN_TABLE.items() if table.get(key) == value}


def placeholders_in(text: str) -> frozenset[str]:
    """Return the placeholder names used in a translation string."""
    return frozenset(PLACEHOLDER_PATTERN.findall(text))
