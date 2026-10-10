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
    SEARCH_TITLE = "search.title"
    SEARCH_QUERY = "search.query"
    SEARCH_RESULTS = "search.results"
    SEARCH_NO_RESULTS = "search.no_results"
    SEARCH_LOADING = "search.loading"
    SEARCH_ERROR = "search.error"
    SEARCH_VERSION = "search.version"
    SEARCH_LATEST = "search.latest"
    SEARCH_SUMMARY = "search.summary"
    SEARCH_INSTALLED = "search.installed"
    SEARCH_NOT_INSTALLED = "search.not_installed"
    SEARCH_HOME_PAGE = "search.home_page"
    SEARCH_PROJECT_URL = "search.project_url"
    SEARCH_INSTALL = "search.install"
    SEARCH_INSTALLED_VERSION = "search.installed_version"
    SEARCH_LATEST_VERSION = "search.latest_version"
    NAV_SECURITY = "nav.security"
    SECURITY_TITLE = "security.title"
    SECURITY_SCANNING = "security.scanning"
    SECURITY_RESULTS = "security.results"
    SECURITY_NO_VULNERABILITIES = "security.no_vulnerabilities"
    SECURITY_VULNERABILITY = "security.vulnerability"
    SECURITY_PACKAGE = "security.package"
    SECURITY_VERSION = "security.version"
    SECURITY_SEVERITY = "security.severity"
    SECURITY_LOW = "security.low"
    SECURITY_MEDIUM = "security.medium"
    SECURITY_HIGH = "security.high"
    SECURITY_CRITICAL = "security.critical"
    SECURITY_UNKNOWN = "security.unknown"
    SECURITY_FIXED_IN = "security.fixed_in"
    SECURITY_DETAILS = "security.details"
    SECURITY_SCANNING_ERROR = "security.scanning_error"
    SECURITY_START_SCAN = "security.start_scan"
    SECURITY_STOP_SCAN = "security.stop_scan"
    SECURITY_SCANNING_IN_PROGRESS = "security.scanning_in_progress"
    NAV_SNAPSHOTS = "nav.snapshots"
    SNAPSHOTS_TITLE = "snapshots.title"
    SNAPSHOTS_LIST = "snapshots.list"
    SNAPSHOTS_NO_SNAPSHOTS = "snapshots.no_snapshots"
    SNAPSHOTS_CREATE = "snapshots.create"
    SNAPSHOTS_RESTORE = "snapshots.restore"
    SNAPSHOTS_DELETE = "snapshots.delete"
    SNAPSHOTS_CREATING = "snapshots.creating"
    SNAPSHOTS_CREATED = "snapshots.created"
    SNAPSHOTS_DELETE_CONFIRM = "snapshots.delete_confirm"
    SNAPSHOTS_RESTORE_CONFIRM = "snapshots.restore_confirm"
    SNAPSHOTS_RESTORED = "snapshots.restored"
    SNAPSHOTS_DELETED = "snapshots.deleted"
    SNAPSHOTS_ERROR = "snapshots.error"
    SNAPSHOTS_ENVIRONMENT = "snapshots.environment"
    SNAPSHOTS_TIME = "snapshots.time"
    SNAPSHOTS_PACKAGES = "snapshots.packages"
    NAV_HISTORY = "nav.history"
    HISTORY_TITLE = "history.title"
    HISTORY_ACTIONS = "history.actions"
    HISTORY_NO_ACTIONS = "history.no_actions"
    HISTORY_PACKAGE = "history.package"
    HISTORY_VERSION = "history.version"
    HISTORY_ACTION = "history.action"
    HISTORY_TIME = "history.time"
    HISTORY_ENVIRONMENT = "history.environment"
    HISTORY_STATUS = "history.status"
    HISTORY_SUCCESS = "history.success"
    HISTORY_FAILED = "history.failed"
    HISTORY_CANCELLED = "history.cancelled"
    NAV_LOGS = "nav.logs"
    NAV_SETTINGS = "nav.settings"

    DASHBOARD_TITLE = "dashboard.title"
    DASHBOARD_ENVIRONMENT = "dashboard.environment"
    DASHBOARD_TODAY_LABEL = "dashboard.today_label"
    DASHBOARD_THIS_WEEK_LABEL = "dashboard.this_week_label"
    DASHBOARD_TOTAL_PACKAGES = "dashboard.total_packages"
    DASHBOARD_OUTDATED_PACKAGES = "dashboard.outdated_packages"
    DASHBOARD_SIZES_TAKEN = "dashboard.sizes_taken"
    DASHBOARD_NO_PACKAGES = "dashboard.no_packages"
    DASHBOARD_REFRESHING = "dashboard.refreshing"
    DASHBOARD_READY = "dashboard.ready"
    DASHBOARD_ENVIRONMENT_UNKNOWN = "dashboard.environment_unknown"
    DASHBOARD_THIS_WEEK_FOLDER = "dashboard.this_week_folder"

    MENU_VIEW = "menu.view"
    MENU_VIEW_SIDEBAR = "menu.view.sidebar"

    TOPBAR_ENVIRONMENT = "topbar.environment"
    TOPBAR_SEARCH_PLACEHOLDER = "topbar.search"
    TOPBAR_TOGGLE_THEME = "topbar.toggle_theme"
    TOPBAR_TOGGLE_LANGUAGE = "topbar.toggle_language"
    TOPBAR_REFRESH = "topbar.refresh"
    TOPBAR_UPGRADE = "topbar.upgrade"
    TOPBAR_NO_ENVIRONMENT = "topbar.no_environment"

    VIEW_EMPTY_TITLE = "view.empty_title"
    VIEW_EMPTY_BODY = "view.empty_body"
    VIEW_PLACEHOLDER_DASHBOARD = "view.placeholder.dashboard"
    DASHBOARD_UNABLE_TO_LOADING = "dashboard.unable_to_loading"
    DASHBOARD_LOADING = "dashboard.loading"
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
    SETTINGS_PACKAGES_PER_PAGE_UNITS = "settings.packages_per_page_units"
    SETTINGS_PYPI_INDEX = "settings.pypi_index"
    SETTINGS_PYPI_EXTRA_INDEX_URLS = "settings.pypi_extra_index_urls"
    SETTINGS_PYPI_TRUSTED_HOSTS = "settings.pypi_trusted_hosts"
    SETTINGS_LINES_HINT = "settings.lines_hint"
    SETTINGS_PYPI = "settings.pypi"
    SETTINGS_TABLE = "settings.table"
    SETTINGS_BEHAVIOUR = "settings.behaviour"
    SETTINGS_SHOW_SIZES = "settings.show_sizes"
    SETTINGS_SHOW_OUTDATED = "settings.show_outdated"
    SETTINGS_CONFIRM_INSTALL = "settings.confirm_install"
    SETTINGS_CONFIRM_UNINSTALL = "settings.confirm_uninstall"
    SETTINGS_CONFIRM_UPGRADE = "settings.confirm_upgrade"
    SETTINGS_PACKAGE_MANAGER = "settings.package_manager"
    SETTINGS_PACKAGE_MANAGER_HINT = "settings.package_manager_hint"
    SETTINGS_SAVE = "settings.save"
    SETTINGS_CANCEL = "settings.cancel"

    CREATE_ENV_TITLE = "create_env.title"
    CREATE_ENV_INTRO = "create_env.intro"
    CREATE_ENV_LOCATION = "create_env.location"
    CREATE_ENV_INTERPRETER = "create_env.interpreter"
    CREATE_ENV_INTERPRETER_HINT = "create_env.interpreter_hint"
    CREATE_ENV_LOCATION_REQUIRED = "create_env.location_required"
    CREATE_ENV_INTERPRETER_ABSOLUTE = "create_env.interpreter_absolute"
    CREATE_ENV_BUTTON = "create_env.button"
    CREATE_ENV_RUNNING = "create_env.running"
    CREATE_ENV_CREATED = "create_env.created"
    CREATE_ENV_FAILED = "create_env.failed"
    CREATE_ENV_CANCELLED = "create_env.cancelled"
    BLOCKED_CREATE_ENVIRONMENT = "blocked.create_environment"

    DEPS_TITLE = "deps.title"
    DEPS_SUMMARY = "deps.summary"
    DEPS_SEARCH = "deps.search"
    DEPS_SELECT_PROMPT = "deps.select_prompt"
    DEPS_REFRESH = "deps.refresh"
    DEPS_WHY = "deps.why"
    DEPS_WHY_ORPHAN = "deps.why_orphan"
    DEPS_WHY_REQUESTED = "deps.why_requested"
    DEPS_REQUIRES = "deps.requires"
    DEPS_REQUIRED_BY = "deps.required_by"
    DEPS_NONE = "deps.none"
    DEPS_MISSING = "deps.missing"
    DEPS_MISSING_NOTE = "deps.missing_note"
    DEPS_UNKNOWN = "deps.unknown"
    DEPS_ORPHANS = "deps.orphans"
    DEPS_ORPHANS_NOTE = "deps.orphans_note"
    DEPS_ORPHANS_NONE = "deps.orphans_none"
    DEPS_ORPHANS_REQUESTED_ONLY = "deps.orphans_requested_only"
    DEPS_ORPHANS_SIZE = "deps.orphans_size"
    DEPS_ORPHANS_PARTIAL = "deps.orphans_partial"
    DEPS_CLEAN_UP = "deps.clean_up"
    DEPS_SCANNING = "deps.scanning"
    DEPS_SCANNED = "deps.scanned"
    DEPS_FAILED = "deps.failed"

    REMOVAL_PREVIEW_TITLE = "removal_preview.title"
    REMOVAL_PREVIEW_WOULD_REMOVE = "removal_preview.would_remove"
    REMOVAL_PREVIEW_LEAVE_UNUSED = "removal_preview.leave_unused"
    REMOVAL_PREVIEW_BLOCKED = "removal_preview.blocked"
    REMOVAL_PREVIEW_BREAKS = "removal_preview.breaks"
    REMOVAL_PREVIEW_CONTINUE = "removal_preview.continue"

    PACKAGES_TITLE = "packages.title"
    PACKAGES_SEARCH = "packages.search"
    PACKAGES_SUMMARY = "packages.summary"
    PACKAGES_SHOW_SIZES = "packages.show_sizes"
    PACKAGES_ONLY_OUTDATED = "packages.only_outdated"
    PACKAGES_SELECTED = "packages.selected"
    PACKAGES_COUNT = "packages.count"
    PACKAGES_OUTDATED_TOTAL = "packages.outdated_total"
    PACKAGES_TOTAL_SIZE = "packages.total_size"
    PACKAGES_INSTALL_SPEC = "packages.install_spec"
    PACKAGES_INSTALL_VERSION = "packages.install_version"
    PACKAGES_INSTALL_USER = "packages.install_user"
    PACKAGES_DOWNGRADE = "packages.downgrade"
    PACKAGES_NO_SELECTION = "packages.no_selection"
    PACKAGES_REFRESHING = "packages.refreshing"
    PACKAGES_CHECKING = "packages.checking"
    PACKAGES_MEASURING = "packages.measuring"
    PACKAGES_CANCELLED = "packages.cancelled"
    PACKAGES_EMPTY = "packages.empty"
    PACKAGES_EMPTY_FILTER = "packages.empty_filter"

    DETAIL_TITLE = "detail.title"
    DETAIL_NO_SELECTION = "detail.no_selection"
    DETAIL_SUMMARY = "detail.summary"
    DETAIL_LICENSE = "detail.license"
    DETAIL_AUTHOR = "detail.author"
    DETAIL_LOCATION = "detail.location"
    DETAIL_REQUIRES = "detail.requires"
    DETAIL_REQUIRED_BY = "detail.required_by"
    DETAIL_LINKS = "detail.links"
    DETAIL_HOME_PAGE = "detail.home_page"
    DETAIL_PROJECT_URL = "detail.project_url"
    DETAIL_PYTHON = "detail.python"
    DETAIL_EDITABLE = "detail.editable"
    DETAIL_DIRECT_REFERENCE = "detail.direct_reference"
    DETAIL_NONE = "detail.none"
    DETAIL_MORE = "detail.more"

    INSTALL_TITLE = "install.title"
    DOWNGRADE_TITLE = "downgrade.title"
    DOWNGRADE_CURRENT = "downgrade.current"
    DOWNGRADE_VERSION = "downgrade.version"

    CONFIRM_COMMAND = "confirm.command"
    CONFIRM_ENVIRONMENT = "confirm.environment"
    CONFIRM_AFFECTED = "confirm.affected"
    CONFIRM_WARNING = "confirm.warning"

    BLOCKED_TITLE = "blocked.title"
    BLOCKED_SUGGESTION = "blocked.suggestion"
    ENV_NO_MANAGER = "env.no_manager"
    ENV_CURRENT = "env.current"
    ENV_BACKEND = "env.backend"
    ENV_BACKEND_FALLBACK = "env.backend_fallback"

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
    BUTTON_HIDE = "button.hide"

    LOG_TITLE = "log.title"
    LOG_CLEAR = "log.clear"
    LOG_SAVE = "log.save"
    LOG_CANCEL = "log.cancel"
    LOG_RUNNING = "log.running"
    LOG_DONE = "log.done"
    LOG_FAILED = "log.failed"
    LOG_CANCELLED = "log.cancelled"
    LOG_EMPTY = "log.empty"
    LOG_HIDE = "log.hide"

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
    TranslationKey.DASHBOARD_TITLE: "Dashboard",
    TranslationKey.DASHBOARD_ENVIRONMENT: "Environment",
    TranslationKey.DASHBOARD_TODAY_LABEL: "Today",
    TranslationKey.DASHBOARD_THIS_WEEK_LABEL: "This week",
    TranslationKey.DASHBOARD_TOTAL_PACKAGES: "Total packages",
    TranslationKey.DASHBOARD_OUTDATED_PACKAGES: "Outdated packages",
    TranslationKey.DASHBOARD_SIZES_TAKEN: "Sizes taken",
    TranslationKey.DASHBOARD_NO_PACKAGES: "No packages installed",
    TranslationKey.DASHBOARD_REFRESHING: "Refreshing...",
    TranslationKey.DASHBOARD_READY: "Ready",
    TranslationKey.DASHBOARD_ENVIRONMENT_UNKNOWN: "Unknown environment",
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
    TranslationKey.TOPBAR_UPGRADE: "Upgrade",
    TranslationKey.TOPBAR_NO_ENVIRONMENT: "No environment selected",
    TranslationKey.VIEW_EMPTY_TITLE: "Nothing here yet",
    TranslationKey.VIEW_EMPTY_BODY: "This view arrives in the next section of the build.",
    TranslationKey.    VIEW_PLACEHOLDER_DASHBOARD: (
        "Package counts, outdated and vulnerable totals, and the environment's purpose "
        "appear here once the package list is wired up."
    ),
    TranslationKey.DASHBOARD_UNABLE_TO_LOADING: (
        "Unable to load dashboard data due to an error."
    ),
    TranslationKey.DASHBOARD_LOADING: (
        "Loading dashboard data..."
    ),
    TranslationKey.DASHBOARD_THIS_WEEK_FOLDER: (
        "This week folder"
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
    TranslationKey.    VIEW_PLACEHOLDER_SEARCH: (
        "Searching PyPI and browsing a package's release history arrive in phase 2."
    ),
    TranslationKey.SEARCH_TITLE: "Search packages",
    TranslationKey.SEARCH_QUERY: "Search",
    TranslationKey.SEARCH_RESULTS: "Results",
    TranslationKey.SEARCH_NO_RESULTS: "No results found",
    TranslationKey.SEARCH_LOADING: "Searching...",
    TranslationKey.SEARCH_ERROR: "Search error",
    TranslationKey.SEARCH_VERSION: "Version",
    TranslationKey.SEARCH_LATEST: "Latest",
    TranslationKey.SEARCH_SUMMARY: "Summary",
    TranslationKey.SEARCH_INSTALLED: "Installed",
    TranslationKey.SEARCH_NOT_INSTALLED: "Not installed",
    TranslationKey.SEARCH_HOME_PAGE: "Home page",
    TranslationKey.SEARCH_PROJECT_URL: "Project URL",
    TranslationKey.SEARCH_INSTALL: "Install",
    TranslationKey.SEARCH_INSTALLED_VERSION: "Installed version",
    TranslationKey.SEARCH_LATEST_VERSION: "Latest version",

    TranslationKey.    VIEW_PLACEHOLDER_SECURITY: (
        "Vulnerability scanning, and the snapshot taken before every fix, arrive in phase 4."
    ),
    TranslationKey.SECURITY_TITLE: "Security",
    TranslationKey.SECURITY_SCANNING: "Scanning for vulnerabilities...",
    TranslationKey.SECURITY_RESULTS: "Results",
    TranslationKey.SECURITY_NO_VULNERABILITIES: "No vulnerabilities found",
    TranslationKey.SECURITY_VULNERABILITY: "Vulnerability",
    TranslationKey.SECURITY_PACKAGE: "Package",
    TranslationKey.SECURITY_VERSION: "Version",
    TranslationKey.SECURITY_SEVERITY: "Severity",
    TranslationKey.SECURITY_LOW: "Low",
    TranslationKey.SECURITY_MEDIUM: "Medium",
    TranslationKey.SECURITY_HIGH: "High",
    TranslationKey.SECURITY_CRITICAL: "Critical",
    TranslationKey.SECURITY_UNKNOWN: "Unknown",
    TranslationKey.SECURITY_FIXED_IN: "Fixed in",
    TranslationKey.SECURITY_DETAILS: "Details",
    TranslationKey.SECURITY_SCANNING_ERROR: "Scanning error",
    TranslationKey.SECURITY_START_SCAN: "Start scan",
    TranslationKey.SECURITY_STOP_SCAN: "Stop scan",
    TranslationKey.SECURITY_SCANNING_IN_PROGRESS: "Scanning in progress...",

    TranslationKey.    VIEW_PLACEHOLDER_SNAPSHOTS: (
        "Environment snapshots and rollback with a preview diff arrive in phase 4."
    ),
    TranslationKey.SNAPSHOTS_TITLE: "Snapshots",
    TranslationKey.SNAPSHOTS_LIST: "Snapshots",
    TranslationKey.SNAPSHOTS_NO_SNAPSHOTS: "No snapshots yet",
    TranslationKey.SNAPSHOTS_CREATE: "Create snapshot",
    TranslationKey.SNAPSHOTS_RESTORE: "Restore",
    TranslationKey.SNAPSHOTS_DELETE: "Delete",
    TranslationKey.SNAPSHOTS_CREATING: "Creating snapshot...",
    TranslationKey.SNAPSHOTS_CREATED: "Snapshot created",
    TranslationKey.SNAPSHOTS_DELETE_CONFIRM: "Delete this snapshot?",
    TranslationKey.SNAPSHOTS_RESTORE_CONFIRM: "Restore this snapshot?",
    TranslationKey.SNAPSHOTS_RESTORED: "Snapshot restored",
    TranslationKey.SNAPSHOTS_DELETED: "Snapshot deleted",
    TranslationKey.SNAPSHOTS_ERROR: "Error",
    TranslationKey.SNAPSHOTS_ENVIRONMENT: "Environment",
    TranslationKey.SNAPSHOTS_TIME: "Time",
    TranslationKey.SNAPSHOTS_PACKAGES: "Packages",

    TranslationKey.    VIEW_PLACEHOLDER_HISTORY: (
        "The action history and undo arrive with snapshots in phase 4."
    ),
    TranslationKey.HISTORY_TITLE: "History",
    TranslationKey.HISTORY_ACTIONS: "Actions",
    TranslationKey.HISTORY_NO_ACTIONS: "No actions yet",
    TranslationKey.HISTORY_PACKAGE: "Package",
    TranslationKey.HISTORY_VERSION: "Version",
    TranslationKey.HISTORY_ACTION: "Action",
    TranslationKey.HISTORY_TIME: "Time",
    TranslationKey.HISTORY_ENVIRONMENT: "Environment",
    TranslationKey.HISTORY_STATUS: "Status",
    TranslationKey.HISTORY_SUCCESS: "Success",
    TranslationKey.HISTORY_FAILED: "Failed",
    TranslationKey.HISTORY_CANCELLED: "Cancelled",

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
    TranslationKey.SETTINGS_PACKAGES_PER_PAGE_UNITS: "packages",
    TranslationKey.SETTINGS_PYPI_INDEX: "PyPI index URL",
    TranslationKey.SETTINGS_PYPI_EXTRA_INDEX_URLS: "Extra index URLs",
    TranslationKey.SETTINGS_PYPI_TRUSTED_HOSTS: "Trusted hosts",
    TranslationKey.SETTINGS_PYPI: "PyPI",
    TranslationKey.SETTINGS_TABLE: "Table",
    TranslationKey.SETTINGS_BEHAVIOUR: "Behaviour",
    TranslationKey.SETTINGS_SHOW_SIZES: "Show package sizes",
    TranslationKey.SETTINGS_SHOW_OUTDATED: "Show outdated packages",
    TranslationKey.SETTINGS_LINES_HINT: "one entry per line, or leave empty",
    TranslationKey.SETTINGS_CONFIRM_INSTALL: "Confirm before installing",
    TranslationKey.SETTINGS_CONFIRM_UNINSTALL: "Confirm before uninstalling",
    TranslationKey.SETTINGS_CONFIRM_UPGRADE: "Confirm before upgrading",
    TranslationKey.SETTINGS_PACKAGE_MANAGER: "Package manager",
    TranslationKey.SETTINGS_PACKAGE_MANAGER_HINT: (
        "The program used to install, remove and upgrade packages."
    ),
    TranslationKey.SETTINGS_SAVE: "Save",
    TranslationKey.SETTINGS_CANCEL: "Cancel",
    TranslationKey.CREATE_ENV_TITLE: "Create a virtual environment",
    TranslationKey.CREATE_ENV_INTRO: (
        "A virtual environment is a private package folder. Nothing outside it "
        "is touched, so this is the safe way to install packages on a system "
        "interpreter that refuses changes."
    ),
    TranslationKey.CREATE_ENV_LOCATION: "Folder",
    TranslationKey.CREATE_ENV_INTERPRETER: "Interpreter",
    TranslationKey.CREATE_ENV_INTERPRETER_HINT: (
        "Leave empty to use the interpreter PyPackHub is running on."
    ),
    TranslationKey.CREATE_ENV_LOCATION_REQUIRED: "Choose a folder for the environment.",
    TranslationKey.CREATE_ENV_INTERPRETER_ABSOLUTE: ("The interpreter path must be absolute."),
    TranslationKey.CREATE_ENV_BUTTON: "Create",
    TranslationKey.CREATE_ENV_RUNNING: "Creating the virtual environment...",
    TranslationKey.CREATE_ENV_CREATED: "Created %(prefix)s",
    TranslationKey.CREATE_ENV_FAILED: "Could not create the environment: %(error)s",
    TranslationKey.CREATE_ENV_CANCELLED: "Environment creation cancelled",
    TranslationKey.BLOCKED_CREATE_ENVIRONMENT: "Create a virtual environment",
    TranslationKey.DEPS_TITLE: "Dependencies",
    TranslationKey.DEPS_SUMMARY: "%(packages)s packages, %(orphans)s unneeded",
    TranslationKey.DEPS_SEARCH: "Filter packages...",
    TranslationKey.DEPS_SELECT_PROMPT: "Select a package to see what it needs and what needs it.",
    TranslationKey.DEPS_REFRESH: "Rescan",
    TranslationKey.DEPS_WHY: "Why is this installed?",
    TranslationKey.DEPS_WHY_REQUESTED: "You asked for this package by name.",
    TranslationKey.DEPS_WHY_ORPHAN: (
        "Nothing installed requires this package, and it was not installed by "
        "name, so it was pulled in for something that has since gone."
    ),
    TranslationKey.DEPS_REQUIRES: "Requires",
    TranslationKey.DEPS_REQUIRED_BY: "Required by",
    TranslationKey.DEPS_NONE: "Nothing",
    TranslationKey.DEPS_MISSING: "Missing requirements",
    TranslationKey.DEPS_MISSING_NOTE: ("Declared by this package but not installed here."),
    TranslationKey.DEPS_UNKNOWN: "no metadata",
    TranslationKey.DEPS_ORPHANS: "Unneeded packages",
    TranslationKey.DEPS_ORPHANS_NOTE: (
        "Nothing installed requires these, and you did not ask for them by name."
    ),
    TranslationKey.DEPS_ORPHANS_NONE: "Nothing is unneeded.",
    TranslationKey.DEPS_ORPHANS_REQUESTED_ONLY: (
        "%(count)s installed packages have no dependents, but you asked for them by name."
    ),
    TranslationKey.DEPS_ORPHANS_SIZE: "Reclaims %(size)s across %(count)s measured packages.",
    TranslationKey.DEPS_ORPHANS_PARTIAL: "sizes not measured",
    TranslationKey.DEPS_CLEAN_UP: "Preview removal",
    TranslationKey.DEPS_SCANNING: "Reading installed metadata...",
    TranslationKey.DEPS_SCANNED: "Read the metadata of %(count)s packages.",
    TranslationKey.DEPS_FAILED: "Could not read the environment: %(error)s",
    TranslationKey.REMOVAL_PREVIEW_TITLE: "Preview: what this removal would do",
    TranslationKey.REMOVAL_PREVIEW_WOULD_REMOVE: "Would remove %(count)s packages",
    TranslationKey.REMOVAL_PREVIEW_LEAVE_UNUSED: (
        "Left installed but unused afterwards: %(packages)s"
    ),
    TranslationKey.REMOVAL_PREVIEW_BLOCKED: (
        "Still required by something that is staying, so this removal would fail "
        "or be refused: %(packages)s"
    ),
    TranslationKey.REMOVAL_PREVIEW_BREAKS: (
        "Would be left with a missing requirement: %(packages)s"
    ),
    TranslationKey.REMOVAL_PREVIEW_CONTINUE: "Continue",
    TranslationKey.PACKAGES_TITLE: "Packages",
    TranslationKey.PACKAGES_SEARCH: "Search packages...",
    TranslationKey.PACKAGES_SUMMARY: "Summary",
    TranslationKey.PACKAGES_SHOW_SIZES: "Show sizes",
    TranslationKey.PACKAGES_ONLY_OUTDATED: "Outdated only",
    TranslationKey.PACKAGES_SELECTED: "%(count)s selected",
    TranslationKey.PACKAGES_COUNT: "%(count)s packages",
    TranslationKey.PACKAGES_OUTDATED_TOTAL: "%(count)s outdated",
    TranslationKey.PACKAGES_TOTAL_SIZE: "Total size: %(size)s",
    TranslationKey.PACKAGES_INSTALL_SPEC: "Package name or specifier",
    TranslationKey.PACKAGES_INSTALL_VERSION: "Version (optional)",
    TranslationKey.PACKAGES_INSTALL_USER: "Install into the user site directory",
    TranslationKey.PACKAGES_DOWNGRADE: "Downgrade to a version",
    TranslationKey.PACKAGES_NO_SELECTION: "Select a package first",
    TranslationKey.PACKAGES_REFRESHING: "Refreshing the package list...",
    TranslationKey.PACKAGES_CHECKING: (
        "Checking %(name)s on the package index (%(index)s of %(total)s)"
    ),
    TranslationKey.PACKAGES_MEASURING: "Measuring package sizes...",
    TranslationKey.PACKAGES_CANCELLED: "The running task was cancelled.",
    TranslationKey.PACKAGES_EMPTY: "No packages are installed in this environment.",
    TranslationKey.PACKAGES_EMPTY_FILTER: "No package matches the current filter.",
    TranslationKey.DETAIL_TITLE: "Package details",
    TranslationKey.DETAIL_NO_SELECTION: "Select a package to see its details.",
    TranslationKey.DETAIL_SUMMARY: "Description",
    TranslationKey.DETAIL_LICENSE: "License",
    TranslationKey.DETAIL_AUTHOR: "Author",
    TranslationKey.DETAIL_LOCATION: "Location",
    TranslationKey.DETAIL_REQUIRES: "Requires",
    TranslationKey.DETAIL_REQUIRED_BY: "Required by",
    TranslationKey.DETAIL_LINKS: "Links",
    TranslationKey.DETAIL_HOME_PAGE: "Home page",
    TranslationKey.DETAIL_PROJECT_URL: "Project URL",
    TranslationKey.DETAIL_PYTHON: "Requires Python",
    TranslationKey.DETAIL_EDITABLE: "Installed in editable mode",
    TranslationKey.DETAIL_DIRECT_REFERENCE: "Installed from a direct reference",
    TranslationKey.DETAIL_NONE: "None",
    TranslationKey.DETAIL_MORE: "+%(count)s more",
    TranslationKey.INSTALL_TITLE: "Install a package",
    TranslationKey.DOWNGRADE_TITLE: "Downgrade %(package)s",
    TranslationKey.DOWNGRADE_CURRENT: "Installed version: %(version)s",
    TranslationKey.DOWNGRADE_VERSION: "Version to install",
    TranslationKey.CONFIRM_COMMAND: "Command",
    TranslationKey.CONFIRM_ENVIRONMENT: "Environment",
    TranslationKey.CONFIRM_AFFECTED: "Affected packages",
    TranslationKey.CONFIRM_WARNING: (
        "This changes %(environment)s. PyPackHub runs exactly the command above, nothing else."
    ),
    TranslationKey.BLOCKED_TITLE: "This environment cannot be modified",
    TranslationKey.BLOCKED_SUGGESTION: "Suggestion",
    TranslationKey.ENV_NO_MANAGER: (
        "No package manager was found. Install pip or uv, then refresh."
    ),
    TranslationKey.ENV_CURRENT: "(current interpreter)",
    TranslationKey.ENV_BACKEND: "Backend",
    TranslationKey.ENV_BACKEND_FALLBACK: "Default backend",
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
    TranslationKey.BUTTON_HIDE: "Hide",
    TranslationKey.LOG_TITLE: "Log",
    TranslationKey.LOG_CLEAR: "Clear",
    TranslationKey.LOG_SAVE: "Save",
    TranslationKey.LOG_CANCEL: "Cancel the running task",
    TranslationKey.LOG_RUNNING: "Running...",
    TranslationKey.LOG_DONE: "Finished successfully.",
    TranslationKey.LOG_FAILED: "Failed: %(error)s",
    TranslationKey.LOG_CANCELLED: "Cancelled.",
    TranslationKey.LOG_EMPTY: "Nothing has run yet.",
    TranslationKey.LOG_HIDE: "Hide",
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
    TranslationKey.DASHBOARD_TITLE: "داشبورد",
    TranslationKey.DASHBOARD_ENVIRONMENT: "محیط",
    TranslationKey.DASHBOARD_TODAY_LABEL: "امروز",
    TranslationKey.DASHBOARD_THIS_WEEK_LABEL: "این هفته",
    TranslationKey.DASHBOARD_TOTAL_PACKAGES: "کل بسته‌ها",
    TranslationKey.DASHBOARD_OUTDATED_PACKAGES: "بسته‌های به‌روزنشده",
    TranslationKey.DASHBOARD_SIZES_TAKEN: "حجم گرفته‌شده",
    TranslationKey.DASHBOARD_NO_PACKAGES: "هیچ بسته‌ای نصب نشده",
    TranslationKey.DASHBOARD_REFRESHING: "در حال تازه‌سازی...",
    TranslationKey.DASHBOARD_READY: "آماده",
    TranslationKey.DASHBOARD_ENVIRONMENT_UNKNOWN: "محیط ناشناخته",
    TranslationKey.NAV_PACKAGES: "بسته‌ها",
    TranslationKey.NAV_ENVIRONMENTS: "محیط‌ها",
    TranslationKey.NAV_DEPENDENCIES: "وابستگی‌ها",
    TranslationKey.NAV_SEARCH: "جست‌وجو",
    TranslationKey.NAV_SECURITY: "امنیت",
    TranslationKey.NAV_SNAPSHOTS: "نقاط بازیابی",
    TranslationKey.NAV_HISTORY: "تاریخچه",
    TranslationKey.NAV_LOGS: "گزارشات",
    TranslationKey.NAV_SETTINGS: "تنظیمات",
    TranslationKey.MENU_VIEW: "نمایش",
    TranslationKey.MENU_VIEW_SIDEBAR: "نمایش یا پنهان کردن نوار کناری",
    TranslationKey.TOPBAR_ENVIRONMENT: "محیط",
    TranslationKey.TOPBAR_SEARCH_PLACEHOLDER: "جست‌وجو در بسته‌های نصب‌شده...",
    TranslationKey.TOPBAR_TOGGLE_THEME: "تغییر تم",
    TranslationKey.TOPBAR_TOGGLE_LANGUAGE: "تغییر زبان",
    TranslationKey.TOPBAR_REFRESH: "تازه‌سازی",
    TranslationKey.TOPBAR_UPGRADE: "ارتقا",
    TranslationKey.TOPBAR_NO_ENVIRONMENT: "هیچ محیطی انتخاب نشده",
    TranslationKey.VIEW_EMPTY_TITLE: "هنوز چیزی اینجا نیست",
    TranslationKey.VIEW_EMPTY_BODY: "این بخش در مرحله بعدی ساخت اضافه می‌شود.",
    TranslationKey.VIEW_PLACEHOLDER_DASHBOARD: (
        "تعداد بسته‌ها، تعداد بسته‌های به‌روزنشده و آسیب‌پذیر، و کاربرد محیط "
        "پس از آماده شدن فهرست بسته‌ها نمایش داده می‌شود."
    ),
    TranslationKey.DASHBOARD_UNABLE_TO_LOADING: (
        "نمی‌تواند داده‌ها را بارگذاری کند."
    ),
    TranslationKey.DASHBOARD_LOADING: (
        "در حال بارگذاری..."
    ),
    TranslationKey.DASHBOARD_THIS_WEEK_FOLDER: (
        "پوشهٔ این هفته"
    ),
    TranslationKey.VIEW_PLACEHOLDER_PACKAGES: (
        "جدول بسته‌های نصب‌شده، فیلترها و عمل‌های آن در مرحله ۴ اضافه می‌شود."
    ),
    TranslationKey.VIEW_PLACEHOLDER_ENVIRONMENTS: (
        "شناسایی و تغییر میان محیط‌ها در فاز ۳ اضافه می‌شود."
    ),
    TranslationKey.VIEW_PLACEHOLDER_DEPENDENCIES: (
        "درخت وابستگی‌ها و نمایش وابستگی‌های معکوس در فاز ۲ اضافه می‌شود."
    ),
    TranslationKey.VIEW_PLACEHOLDER_SEARCH: (
        "جست‌وجو در پای‌پی‌آی و مرور تاریخچه نسخه‌های یک بسته در فاز ۲ اضافه می‌شود."
    ),
    TranslationKey.SEARCH_TITLE: "جست‌وجوی بسته‌ها",
    TranslationKey.SEARCH_QUERY: "جست‌وجو",
    TranslationKey.SEARCH_RESULTS: "نتایج",
    TranslationKey.SEARCH_NO_RESULTS: "هیچ نتیجه‌ای پیدا نشد",
    TranslationKey.SEARCH_LOADING: "در حال جست‌وجو...",
    TranslationKey.SEARCH_ERROR: "خطای جست‌وجو",
    TranslationKey.SEARCH_VERSION: "نسخه",
    TranslationKey.SEARCH_LATEST: "آخرین",
    TranslationKey.SEARCH_SUMMARY: "توضیح",
    TranslationKey.SEARCH_INSTALLED: "نصب‌شده",
    TranslationKey.SEARCH_NOT_INSTALLED: "نشده",
    TranslationKey.SEARCH_HOME_PAGE: "صفحه اصلی",
    TranslationKey.SEARCH_PROJECT_URL: "آدرس پروژه",
    TranslationKey.SEARCH_INSTALL: "نصب",
    TranslationKey.SEARCH_INSTALLED_VERSION: "نسخه نصب‌شده",
    TranslationKey.SEARCH_LATEST_VERSION: "آخرین نسخه",
    TranslationKey.VIEW_PLACEHOLDER_SECURITY: (
        "بررسی آسیب‌پذیری‌ها و نقطهٔ بازیابی که پیش از هر اصلاح گرفته می‌شود، در فاز ۴ اضافه می‌شود."
    ),
    TranslationKey.SECURITY_TITLE: "امنیت",
    TranslationKey.SECURITY_SCANNING: "در حال بررسی آسیب‌پذیری‌ها...",
    TranslationKey.SECURITY_RESULTS: "نتایج",
    TranslationKey.SECURITY_NO_VULNERABILITIES: "هیچ آسیب‌پذیری‌ای پیدا نشد",
    TranslationKey.SECURITY_VULNERABILITY: " آسیب‌پذیری",
    TranslationKey.SECURITY_PACKAGE: "بسته",
    TranslationKey.SECURITY_VERSION: "نسخه",
    TranslationKey.SECURITY_SEVERITY: "خشگیری",
    TranslationKey.SECURITY_LOW: "پایت",
    TranslationKey.SECURITY_MEDIUM: "متوسط",
    TranslationKey.SECURITY_HIGH: "بالا",
    TranslationKey.SECURITY_CRITICAL: "حساس",
    TranslationKey.SECURITY_UNKNOWN: "ناشناخته",
    TranslationKey.SECURITY_FIXED_IN: "رفع در",
    TranslationKey.SECURITY_DETAILS: "جزئیات",
    TranslationKey.SECURITY_SCANNING_ERROR: "خطای اسکن",
    TranslationKey.SECURITY_START_SCAN: "شروع اسکن",
    TranslationKey.SECURITY_STOP_SCAN: "توقف اسکن",
    TranslationKey.SECURITY_SCANNING_IN_PROGRESS: "اسکن در حال اجراست...",
    TranslationKey.VIEW_PLACEHOLDER_SNAPSHOTS: (
        "نقاط بازیابی محیط و بازگشت همراه با پیش‌نمایش تغییرات در فاز ۴ اضافه می‌شود."
    ),
    TranslationKey.SNAPSHOTS_TITLE: "نقاط بازیابی",
    TranslationKey.SNAPSHOTS_LIST: "نقاط بازیابی",
    TranslationKey.SNAPSHOTS_NO_SNAPSHOTS: "هنوز نقطه بازیابی‌ای نیست",
    TranslationKey.SNAPSHOTS_CREATE: "ایجاد نقطه بازیابی",
    TranslationKey.SNAPSHOTS_RESTORE: "بازگرداندن",
    TranslationKey.SNAPSHOTS_DELETE: "حذف",
    TranslationKey.SNAPSHOTS_CREATING: "در حال ایجاد نقطه بازیابی...",
    TranslationKey.SNAPSHOTS_CREATED: "نقطه بازیابی ساخته شد",
    TranslationKey.SNAPSHOTS_DELETE_CONFIRM: "حذف این نقطه بازیابی؟",
    TranslationKey.SNAPSHOTS_RESTORE_CONFIRM: "بازگرداندن این نقطه بازیابی؟",
    TranslationKey.SNAPSHOTS_RESTORED: "نقطه بازیابی بازگردانده شد",
    TranslationKey.SNAPSHOTS_DELETED: "نقطه بازیابی حذف شد",
    TranslationKey.SNAPSHOTS_ERROR: "خطا",
    TranslationKey.SNAPSHOTS_ENVIRONMENT: "محیط",
    TranslationKey.SNAPSHOTS_TIME: "زمان",
    TranslationKey.SNAPSHOTS_PACKAGES: "بسته‌ها",
    TranslationKey.VIEW_PLACEHOLDER_HISTORY: (
        "تاریخچهٔ کارها و بازگشت آن‌ها همراه با نقاط بازیابی در فاز ۴ اضافه می‌شود."
    ),
    TranslationKey.HISTORY_TITLE: "تاریخچه",
    TranslationKey.HISTORY_ACTIONS: "اقدامات",
    TranslationKey.HISTORY_NO_ACTIONS: "هنوز اعمالی نداریم",
    TranslationKey.HISTORY_PACKAGE: "بسته",
    TranslationKey.HISTORY_VERSION: "نسخه",
    TranslationKey.HISTORY_ACTION: "اقدام",
    TranslationKey.HISTORY_TIME: "زمان",
    TranslationKey.HISTORY_ENVIRONMENT: "محیط",
    TranslationKey.HISTORY_STATUS: "وضعیت",
    TranslationKey.HISTORY_SUCCESS: "موفق",
    TranslationKey.HISTORY_FAILED: "ناموفق",
    TranslationKey.HISTORY_CANCELLED: "لغو‌شده",
    TranslationKey.VIEW_PLACEHOLDER_LOGS: (
        "پنل خط فرمان زنده و فایل گزارش پایدار همراه با کارهای بسته‌ها در مرحله ۴ اضافه می‌شود."
    ),
    TranslationKey.VIEW_PLACEHOLDER_SETTINGS: (
        "تم، زبان و سایر تنظیمات همراه صفحه تنظیمات در مرحله ۵ اضافه می‌شود."
    ),
    TranslationKey.THEME_SYSTEM: "همانند سیستم",
    TranslationKey.THEME_LIGHT: "روشن",
    TranslationKey.THEME_DARK: "تیره",
    TranslationKey.LANGUAGE_SYSTEM: "همانند سیستم",
    TranslationKey.LANGUAGE_EN: "انگلیسی",
    TranslationKey.LANGUAGE_FA: "فارسی",
    TranslationKey.SETTINGS_TITLE: "تنظیمات",
    TranslationKey.SETTINGS_THEME: "تم",
    TranslationKey.SETTINGS_LANGUAGE: "زبان",
    TranslationKey.SETTINGS_APPLY: "اعمال",
    TranslationKey.SETTINGS_RESET: "بازنشانی",
    TranslationKey.SETTINGS_SIDEBAR_WIDTH: "عرض نوار کناری",
    TranslationKey.SETTINGS_PACKAGE_PAGE_SIZE: "تعداد بسته در هر صفحه",
    TranslationKey.SETTINGS_PACKAGES_PER_PAGE_UNITS: "بسته",
    TranslationKey.SETTINGS_PYPI_INDEX: "آدرس نمایه پای‌پی‌آی",
    TranslationKey.SETTINGS_PYPI_EXTRA_INDEX_URLS: "آدرس‌های نمایه اضافی",
    TranslationKey.SETTINGS_PYPI_TRUSTED_HOSTS: "میزبان‌های مورد اعتماد",
    TranslationKey.SETTINGS_PYPI: "پای‌پی‌آی",
    TranslationKey.SETTINGS_TABLE: "جدول",
    TranslationKey.SETTINGS_BEHAVIOUR: "رفتار",
    TranslationKey.SETTINGS_SHOW_SIZES: "نمایش اندازه بسته‌ها",
    TranslationKey.SETTINGS_SHOW_OUTDATED: "نمایش بسته‌های به‌روزنشده",
    TranslationKey.SETTINGS_LINES_HINT: "هر مورد در یک خط، یا خالی بگذارید",
    TranslationKey.SETTINGS_CONFIRM_INSTALL: "تأیید قبل از نصب",
    TranslationKey.SETTINGS_CONFIRM_UNINSTALL: "تأیید قبل از حذف",
    TranslationKey.SETTINGS_CONFIRM_UPGRADE: "تأیید قبل از ارتقا",
    TranslationKey.SETTINGS_PACKAGE_MANAGER: "مدیر بسته",
    TranslationKey.SETTINGS_PACKAGE_MANAGER_HINT: (
        "برنامه‌ای که برای نصب، حذف و ارتقای بسته‌ها استفاده می‌شود."
    ),
    TranslationKey.SETTINGS_SAVE: "ذخیره",
    TranslationKey.SETTINGS_CANCEL: "لغو",
    TranslationKey.CREATE_ENV_TITLE: "ساخت محیط مجازی",
    TranslationKey.CREATE_ENV_INTRO: (
        "محیط مجازی یک پوشهٔ خصوصی برای بسته‌هاست. هیچ‌چیز بیرون از آن تغییر "
        "نمی‌کند، پس این روش، شیوهٔ ایمنِ نصب بسته روی مفسری است که تغییر را نمی‌پذیرد."
    ),
    TranslationKey.CREATE_ENV_LOCATION: "پوشه",
    TranslationKey.CREATE_ENV_INTERPRETER: "مفسر",
    TranslationKey.CREATE_ENV_INTERPRETER_HINT: (
        "برای استفاده از مفسری که PyPackHub با آن اجرا می‌شود، خالی بگذارید."
    ),
    TranslationKey.CREATE_ENV_LOCATION_REQUIRED: "یک پوشه برای محیط انتخاب کنید.",
    TranslationKey.CREATE_ENV_INTERPRETER_ABSOLUTE: "مسیر مفسر باید مطلق باشد.",
    TranslationKey.CREATE_ENV_BUTTON: "ساخت",
    TranslationKey.CREATE_ENV_RUNNING: "در حال ساخت محیط مجازی...",
    TranslationKey.CREATE_ENV_CREATED: "%(prefix)s ساخته شد",
    TranslationKey.CREATE_ENV_FAILED: "ساخت محیط ناموفق بود: %(error)s",
    TranslationKey.CREATE_ENV_CANCELLED: "ساخت محیط لغو شد",
    TranslationKey.BLOCKED_CREATE_ENVIRONMENT: "ساخت محیط مجازی",
    TranslationKey.DEPS_TITLE: "وابستگی‌ها",
    TranslationKey.DEPS_SUMMARY: "%(packages)s بسته، %(orphans)s بدون نیاز",
    TranslationKey.DEPS_SEARCH: "فیلتر بسته‌ها...",
    TranslationKey.DEPS_SELECT_PROMPT: "یک بسته را انتخاب کنید تا نیازهایش و نیازمندانش را ببینید.",
    TranslationKey.DEPS_REFRESH: "اسکن مجدد",
    TranslationKey.DEPS_WHY: "چرا این بسته نصب است؟",
    TranslationKey.DEPS_WHY_REQUESTED: "شما این بسته را با نام درخواست کرده‌اید.",
    TranslationKey.DEPS_WHY_ORPHAN: (
        "هیچ بسته نصب‌شده‌ای به این نیاز ندارد و این بسته هم با نام نصب نشده، "
        "پس برای چیزی نصب شده که حالا دیگر وجود ندارد."
    ),
    TranslationKey.DEPS_REQUIRES: "پیش‌نیازها",
    TranslationKey.DEPS_REQUIRED_BY: "نیازمندان",
    TranslationKey.DEPS_NONE: "هیچ",
    TranslationKey.DEPS_MISSING: "نیازهای نصب‌نشده",
    TranslationKey.DEPS_MISSING_NOTE: "توسط این بسته اعلام شده، اما در این محیط نصب نیست.",
    TranslationKey.DEPS_UNKNOWN: "بدون فراداده",
    TranslationKey.DEPS_ORPHANS: "بسته‌های بدون نیاز",
    TranslationKey.DEPS_ORPHANS_NOTE: (
        "هیچ بسته نصب‌شده‌ای به این‌ها نیاز ندارد و شما هم با نام درخواستشان نکرده‌اید."
    ),
    TranslationKey.DEPS_ORPHANS_NONE: "هیچ بسته‌ای بی‌نیاز نیست.",
    TranslationKey.DEPS_ORPHANS_REQUESTED_ONLY: (
        "%(count)s بستهٔ نصب‌شده به چیزی نیاز ندارند، اما شما آن‌ها را با نام درخواست کرده‌اید."
    ),
    TranslationKey.DEPS_ORPHANS_SIZE: "%(size)s در %(count)s بسته اندازه گرفته‌شده آزاد می‌شود.",
    TranslationKey.DEPS_ORPHANS_PARTIAL: "اندازه‌ها اندازه‌گیری نشده‌اند",
    TranslationKey.DEPS_CLEAN_UP: "پیش‌نمایش حذف",
    TranslationKey.DEPS_SCANNING: "خواندن فراداده بسته‌های نصب‌شده...",
    TranslationKey.DEPS_SCANNED: "فرادادهٔ %(count)s بسته خوانده شد.",
    TranslationKey.DEPS_FAILED: "خواندن اطلاعات محیط ناموفق بود: %(error)s",
    TranslationKey.REMOVAL_PREVIEW_TITLE: "پیش‌نمایش: این حذف چه می‌کند",
    TranslationKey.REMOVAL_PREVIEW_WOULD_REMOVE: "%(count)s بسته حذف می‌شود",
    TranslationKey.REMOVAL_PREVIEW_LEAVE_UNUSED: (
        "این بسته‌ها پس از حذف، نصب‌شده اما بی‌استفاده می‌مانند: %(packages)s"
    ),
    TranslationKey.REMOVAL_PREVIEW_BLOCKED: (
        "این بسته‌ها هنوز مورد نیاز چیزی هستند که باقی می‌ماند؛ پس حذف ناموفق "
        "می‌شود یا رد: %(packages)s"
    ),
    TranslationKey.REMOVAL_PREVIEW_BREAKS: "نیاز نصب‌نشده باقی می‌ماند: %(packages)s",
    TranslationKey.REMOVAL_PREVIEW_CONTINUE: "ادامه",
    TranslationKey.PACKAGES_TITLE: "بسته‌ها",
    TranslationKey.PACKAGES_SEARCH: "جست‌وجوی بسته‌ها...",
    TranslationKey.PACKAGES_SUMMARY: "توضیح",
    TranslationKey.PACKAGES_SHOW_SIZES: "نمایش اندازه‌ها",
    TranslationKey.PACKAGES_ONLY_OUTDATED: "فقط به‌روزنشده‌ها",
    TranslationKey.PACKAGES_SELECTED: "%(count)s مورد انتخاب‌شده",
    TranslationKey.PACKAGES_COUNT: "%(count)s بسته",
    TranslationKey.PACKAGES_OUTDATED_TOTAL: "%(count)s بستهٔ به‌روزنشده",
    TranslationKey.PACKAGES_TOTAL_SIZE: "اندازهٔ کل: %(size)s",
    TranslationKey.PACKAGES_INSTALL_SPEC: "نام یا مشخصه بسته",
    TranslationKey.PACKAGES_INSTALL_VERSION: "نسخه (اختیاری)",
    TranslationKey.PACKAGES_INSTALL_USER: "نصب در شاخه کاربر",
    TranslationKey.PACKAGES_DOWNGRADE: "نصب نسخه قدیمی‌تر",
    TranslationKey.PACKAGES_NO_SELECTION: "ابتدا یک بسته انتخاب کنید",
    TranslationKey.PACKAGES_REFRESHING: "تازه‌سازی فهرست بسته‌ها...",
    TranslationKey.PACKAGES_CHECKING: ("بررسی %(name)s در نمایه بسته‌ها (%(index)s از %(total)s)"),
    TranslationKey.PACKAGES_MEASURING: "در حال اندازه‌گیری بسته‌ها...",
    TranslationKey.PACKAGES_CANCELLED: "کار در حال اجرا لغو شد.",
    TranslationKey.PACKAGES_EMPTY: "هیچ بسته‌ای در این محیط نصب نیست.",
    TranslationKey.PACKAGES_EMPTY_FILTER: "هیچ بسته‌ای با فیلتر فعلی همخوانی ندارد.",
    TranslationKey.DETAIL_TITLE: "جزئیات بسته",
    TranslationKey.DETAIL_NO_SELECTION: "برای دیدن جزئیات، یک بسته انتخاب کنید.",
    TranslationKey.DETAIL_SUMMARY: "توضیح",
    TranslationKey.DETAIL_LICENSE: "پروانه",
    TranslationKey.DETAIL_AUTHOR: "نویسنده",
    TranslationKey.DETAIL_LOCATION: "محل نصب",
    TranslationKey.DETAIL_REQUIRES: "پیش‌نیازها",
    TranslationKey.DETAIL_REQUIRED_BY: "نیازمندان",
    TranslationKey.DETAIL_LINKS: "لینک‌ها",
    TranslationKey.DETAIL_HOME_PAGE: "صفحه اصلی",
    TranslationKey.DETAIL_PROJECT_URL: "آدرس پروژه",
    TranslationKey.DETAIL_PYTHON: "نسخه پایتون مورد نیاز",
    TranslationKey.DETAIL_EDITABLE: "به‌صورت قابل‌ویرایش نصب شده",
    TranslationKey.DETAIL_DIRECT_REFERENCE: "از یک ارجاع مستقیم نصب شده",
    TranslationKey.DETAIL_NONE: "هیچ",
    TranslationKey.DETAIL_MORE: "%(count)s مورد دیگر",
    TranslationKey.INSTALL_TITLE: "نصب یک بسته",
    TranslationKey.DOWNGRADE_TITLE: "نصب نسخه قدیمی‌تر %(package)s",
    TranslationKey.DOWNGRADE_CURRENT: "نسخه نصب‌شده: %(version)s",
    TranslationKey.DOWNGRADE_VERSION: "نسخه مورد نظر برای نصب",
    TranslationKey.CONFIRM_COMMAND: "دستور",
    TranslationKey.CONFIRM_ENVIRONMENT: "محیط",
    TranslationKey.CONFIRM_AFFECTED: "بسته‌های تحت تأثیر",
    TranslationKey.CONFIRM_WARNING: (
        "این کار %(environment)s را تغییر می‌دهد. پای‌پک‌هاب دقیقاً همان دستور بالا را اجرا می‌کند و بس."
    ),
    TranslationKey.BLOCKED_TITLE: "این محیط قابل تغییر نیست",
    TranslationKey.BLOCKED_SUGGESTION: "پیشنهاد",
    TranslationKey.ENV_NO_MANAGER: (
        "هیچ مدیر بسته‌ای پیدا نشد. pip یا uv را نصب کنید و سپس تازه‌سازی را بزنید."
    ),
    TranslationKey.ENV_CURRENT: "(مفسر فعلی)",
    TranslationKey.ENV_BACKEND: "موتور",
    TranslationKey.ENV_BACKEND_FALLBACK: "موتور پیش‌فرض",
    TranslationKey.PACKAGES_INSTALL: "نصب",
    TranslationKey.PACKAGES_UNINSTALL: "حذف",
    TranslationKey.PACKAGES_UPGRADE: "ارتقا",
    TranslationKey.PACKAGES_UPGRADE_ALL: "ارتقا همه",
    TranslationKey.PACKAGES_REFRESH: "تازه‌سازی",
    TranslationKey.PACKAGES_NAME: "نام",
    TranslationKey.PACKAGES_VERSION: "نسخه",
    TranslationKey.PACKAGES_LATEST: "آخرین",
    TranslationKey.PACKAGES_STATUS: "وضعیت",
    TranslationKey.PACKAGES_SIZE: "اندازه",
    TranslationKey.PACKAGES_BACKEND: "موتور",
    TranslationKey.PACKAGES_ENVIRONMENT: "محیط",
    TranslationKey.STATUS_OUTDATED: "به‌روزنشده",
    TranslationKey.STATUS_UP_TO_DATE: "به‌روز",
    TranslationKey.STATUS_UNKNOWN: "ناشناخته",
    TranslationKey.CONFIRM_INSTALL_TITLE: "نصب بسته؟",
    TranslationKey.CONFIRM_INSTALL_MESSAGE: "بسته %(package)s در %(environment)s نصب شود؟",
    TranslationKey.CONFIRM_UNINSTALL_TITLE: "حذف بسته؟",
    TranslationKey.CONFIRM_UNINSTALL_MESSAGE: "بسته %(package)s از %(environment)s حذف شود؟",
    TranslationKey.CONFIRM_UPGRADE_TITLE: "ارتقا بسته؟",
    TranslationKey.CONFIRM_UPGRADE_MESSAGE: ("بسته %(package)s از %(from)s به %(to)s ارتقا یابد؟"),
    TranslationKey.BUTTON_OK: "تأیید",
    TranslationKey.BUTTON_CANCEL: "لغو",
    TranslationKey.BUTTON_YES: "بله",
    TranslationKey.BUTTON_NO: "خیر",
    TranslationKey.BUTTON_INSTALL: "نصب",
    TranslationKey.BUTTON_UNINSTALL: "حذف",
    TranslationKey.BUTTON_UPGRADE: "ارتقا",
    TranslationKey.BUTTON_CLOSE: "بستن",
    TranslationKey.BUTTON_HIDE: "پنهان‌کردن",
    TranslationKey.LOG_TITLE: "گزارش",
    TranslationKey.LOG_CLEAR: "پاک‌کردن",
    TranslationKey.LOG_SAVE: "ذخیره",
    TranslationKey.LOG_CANCEL: "لغو کار در حال اجرا",
    TranslationKey.LOG_RUNNING: "در حال اجرا...",
    TranslationKey.LOG_DONE: "با موفقیت تمام شد.",
    TranslationKey.LOG_FAILED: "ناموفق: %(error)s",
    TranslationKey.LOG_CANCELLED: "لغو شد.",
    TranslationKey.LOG_EMPTY: "هنوز چیزی اجرا نشده.",
    TranslationKey.LOG_HIDE: "پنهان‌کردن",
    TranslationKey.TOAST_INSTALLED: "%(package)s نصب شد",
    TranslationKey.TOAST_UNINSTALLED: "%(package)s حذف شد",
    TranslationKey.TOAST_UPGRADED: "%(package)s ارتقا یافت",
    TranslationKey.TOAST_CANCELLED: "لغو شد",
    TranslationKey.TOAST_FAILED: "ناموفق",
    TranslationKey.TOAST_OFFLINE: "بدون اتصال",
    TranslationKey.ERROR_TITLE: "خطا",
    TranslationKey.ERROR_MESSAGE: "خطایی رخ داد.",
    TranslationKey.SAFETY_NOTICE: (
        "هر نصب، حذف یا ارتقا، دستور دقیق خود را قبل از اجرا نشان می‌دهد."
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
