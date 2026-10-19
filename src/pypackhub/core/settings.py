"""Application settings for PyPackHub.

This module defines the settings the application cares about, how they are
validated, and how they are persisted. The UI layer owns the dialogs and the
widgets; this module owns the model and the contract.

Persistence uses QSettings through the UI layer in section 3. This core module
deliberately stays headless: it exposes a typed model, a validator, and a
safe-default factory, but it does not import Qt. The persistence glue lives in
the UI layer so the core can be exercised without a display server.
"""

from __future__ import annotations

import contextlib
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Final

from platformdirs import user_data_dir

from pypackhub import APP_ID
from pypackhub.core.secrets import CredentialStore
from pypackhub.core.validation import validate_environment_name, validate_index_url

__all__ = [
    "AppSettings",
    "ColorTheme",
    "Language",
    "default_settings",
    "settings_data_directory",
    "settings_file",
    "validate_settings",
]


class ColorTheme(str, Enum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


class Language(str, Enum):
    SYSTEM = "system"
    EN = "en"
    FA = "fa"


#: How long PyPI metadata stays fresh in the local cache.
PYPI_CACHE_TTL_SECONDS: Final = 300.0

#: How many package versions the PyPI client keeps per package.
PYPI_CACHE_MAX_VERSIONS: Final = 12


@dataclass(slots=True)
class AppSettings:
    """The settings the application persists across restarts."""

    theme: ColorTheme = ColorTheme.SYSTEM
    language: Language = Language.SYSTEM
    main_window_geometry: str = ""
    main_window_state: str = ""
    sidebar_width: int = 220
    package_list_page_size: int = 60
    default_environment_index: int = 0
    default_target_environment: str = ""
    pypi_index_url: str = "https://pypi.org/simple"
    extra_index_urls: tuple[str, ...] = ()
    trusted_hosts: tuple[str, ...] = ()
    credential_store: CredentialStore = field(default_factory=CredentialStore)
    pypi_cache_ttl: float = PYPI_CACHE_TTL_SECONDS
    pypi_cache_max_versions: int = PYPI_CACHE_MAX_VERSIONS
    auto_refresh_interval_seconds: float = 0.0
    show_sizes_by_default: bool = True
    show_outdated_by_default: bool = True
    confirm_before_install: bool = True
    confirm_before_uninstall: bool = True
    confirm_before_upgrade: bool = True
    package_manager: str = "auto"


def default_settings() -> AppSettings:
    """A working default the application can start with before any file is read."""
    return AppSettings()


def validate_settings(settings: AppSettings) -> AppSettings:
    """Validate the parts of settings that come from the user or a file."""
    validated = AppSettings(
        theme=settings.theme,
        language=settings.language,
        main_window_geometry=_window_blob(settings.main_window_geometry),
        main_window_state=_window_blob(settings.main_window_state),
        sidebar_width=max(80, min(600, settings.sidebar_width)),
        package_list_page_size=max(10, min(500, settings.package_list_page_size)),
        default_environment_index=max(0, settings.default_environment_index),
        default_target_environment=settings.default_target_environment,
        pypi_index_url=validate_index_url(settings.pypi_index_url),
        extra_index_urls=tuple(validate_index_url(url) for url in settings.extra_index_urls),
        trusted_hosts=tuple(host for host in map(_as_trusted_host, settings.trusted_hosts) if host),
        credential_store=settings.credential_store,
        pypi_cache_ttl=max(15.0, min(3600.0, settings.pypi_cache_ttl)),
        pypi_cache_max_versions=max(4, min(64, settings.pypi_cache_max_versions)),
        auto_refresh_interval_seconds=max(0.0, min(300.0, settings.auto_refresh_interval_seconds)),
        show_sizes_by_default=bool(settings.show_sizes_by_default),
        show_outdated_by_default=bool(settings.show_outdated_by_default),
        confirm_before_install=bool(settings.confirm_before_install),
        confirm_before_uninstall=bool(settings.confirm_before_uninstall),
        confirm_before_upgrade=bool(settings.confirm_before_upgrade),
        package_manager=_as_package_manager(settings.package_manager),
    )

    if validated.default_target_environment:
        validate_environment_name(validated.default_target_environment)

    return validated


#: A window-state blob is a few hundred bytes; anything far larger is a
#: hand-edited file, not a window position, and is dropped rather than read.
_WINDOW_STATE_MAX_BYTES: Final = 8192

#: What pip accepts as a ``--trusted-host`` value: a hostname or a host:port,
#: and nothing else - no scheme, no path, no spaces, no shell characters.
#: The entries are inert until a plan carries them, and by then each one is
#: on a command line, so the shape is enforced where the value enters.
_TRUSTED_HOST_PATTERN = re.compile(r"^[A-Za-z0-9._*-]{1,253}(?::\d{1,5})?$")


def _window_blob(value: str) -> str:
    """Keep a persisted window blob only when it is a plausible size."""
    return value if len(value) <= _WINDOW_STATE_MAX_BYTES else ""


def _as_trusted_host(value: object) -> str:
    """One trusted-host entry, lowercased and shape-checked; empty is invalid."""
    if isinstance(value, str):
        stripped = value.strip().lower()
        if _TRUSTED_HOST_PATTERN.match(stripped):
            return stripped
    return ""


def _as_package_manager(value: object) -> str:
    if isinstance(value, str) and value in {"auto", "pip", "uv"}:
        return value
    return "auto"


def settings_data_directory() -> Path:
    """Where the application keeps its persistent settings and caches.

    The directory is created on first use and, where the operating system
    supports it, restricted to the user (SPEC section 11.6: local settings,
    snapshots, cache and logs live in ``platformdirs`` directories with
    user-only permissions).
    """
    directory = Path(user_data_dir(APP_ID))
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        with contextlib.suppress(OSError):
            directory.chmod(0o700)
    return directory


def settings_file() -> Path:
    """The INI file the UI layer's QSettings bridge reads and writes.

    It lives in :func:`settings_data_directory` rather than in the platform's
    native registry so that the location is the same one documented for caches
    and logs, and so that it can carry user-only permissions.
    """
    return settings_data_directory() / "settings.ini"
