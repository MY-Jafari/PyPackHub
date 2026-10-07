"""Logging setup for PyPackHub's headless core.

This module is the single place that configures the process-wide logger the core
uses. It is deliberately boring: a JSON-ish-ish log file that rotates, and a
redacting filter so credentials never leak into on-disk logs even when some
future logging call forgets to redact its own arguments.

The log file is created under a platformdirs app data directory so it follows
the same rules as the rest of the application's persistent state. The UI layer
does its own per-session logging and never imports this module.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

from platformdirs import user_data_dir

from pypackhub import APP_ID
from pypackhub.core.secrets import RedactingFilter

__all__ = [
    "DEFAULT_LOG_BACKUPS",
    "DEFAULT_LOG_BYTES",
    "ensure_log_directory",
    "setup_logging",
]

DEFAULT_LOG_BYTES: Final = 5 * 1024 * 1024
DEFAULT_LOG_BACKUPS: Final = 4

LOG_FORMAT: Final = "%(asctime)s %(levelname)-8s %(name)s %(message)s"

_logger_configured: bool = False


def ensure_log_directory() -> Path:
    """Return the directory logs should be written into, creating it if needed."""
    directory = Path(user_data_dir(APP_ID, create_file=False, ensure_exists=True))
    log_directory = directory / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    return log_directory


def _default_log_path() -> Path:
    return ensure_log_directory() / f"{APP_ID}.log"


def setup_logging(
    *,
    log_path: Path | None = None,
    level: int = logging.INFO,
    max_bytes: int = DEFAULT_LOG_BYTES,
    backups: int = DEFAULT_LOG_BACKUPS,
    to_stdout: bool = True,
) -> Path:
    """Configure one rotating log file and an optional stdout sink.

    This function is safe to call more than once per process, but it deliberately
    guards against double-configuration: only the first call installs the
    rotating file handler.
    """
    global _logger_configured

    log_path = log_path or _default_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    if not _logger_configured:
        redacting = RedactingFilter()
        root.addFilter(redacting)

        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backups,
            encoding="utf-8",
            delay=True,
        )
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        file_handler.setLevel(logging.DEBUG)
        root.addHandler(file_handler)

        _logger_configured = True

    if to_stdout and not any(
        isinstance(handler, logging.StreamHandler)
        and getattr(handler, "stream", None) is sys.stdout
        for handler in root.handlers
    ):
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setFormatter(logging.Formatter("%(levelname)-8s %(message)s"))
        stdout_handler.setLevel(logging.INFO)
        root.addHandler(stdout_handler)

    return log_path
