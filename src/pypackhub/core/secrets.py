"""Keep secrets out of logs, diagnostics and command lines.

Two jobs live here (SPEC sections 11.6 and 11.7):

* **Redaction.** Anything that reaches a log line, an error dialog, a
  confirmation box or a diagnostics bundle is passed through :func:`redact`
  first. Credentials embedded in a URL (``https://user:pass@host``), tokens in
  query strings, ``Authorization`` headers, ``pypi-...`` API tokens and
  ``name=value`` pairs with a secret-looking name are all masked. The logging
  filter at the bottom of this module applies the same rules to every record,
  so a log line cannot miss it by accident.
* **Storage.** Credentials for private indexes live in the operating system's
  keychain, never in a configuration file. :class:`CredentialStore` is a thin,
  testable wrapper around ``keyring``; the secret is returned to the caller and
  is not logged, formatted or included in any exception.

Redaction is a safety net, not a licence to print secrets: the code that handles
a credential still has to pass it to the child process through the environment
or a config file rather than through an argument list.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any, Final

__all__ = [
    "REDACTED",
    "CredentialStore",
    "CredentialStoreError",
    "RedactingFilter",
    "redact",
    "redact_mapping",
    "redact_url",
]

#: What a masked value looks like in logs and dialogs.
REDACTED: Final = "***"

#: Keychain service name. One service for the whole application, with the index
#: host as the key, so a user can see and revoke entries in their OS keychain.
SERVICE_NAME: Final = "pypackhub"

#: Query-string or assignment names whose value is never shown.
_SECRET_NAMES: Final = (
    "access[_-]?token",
    "api[_-]?key",
    "apikey",
    "auth",
    "credential",
    "key",
    "passwd",
    "password",
    "pwd",
    "secret",
    "sig",
    "signature",
    "token",
)

# `(?i)` is a global flag and Python only accepts it at the very start of a
# pattern, so each of these spells it out first rather than inline.
_SECRET_NAMES_ALTERNATION: Final = "|".join(_SECRET_NAMES)

# The negative lookahead matters: the query-string rule runs first, so by the
# time this pattern sees `?token=***&page=2` the value has already been masked.
# Without it, the greedy `\S+` would swallow `&page=2` as well and destroy the
# rest of the URL, which is exactly the part a user needs to read.
_SECRET_NAME_PATTERN: Final = re.compile(
    r"(?i)(?P<prefix>\b(?:" + _SECRET_NAMES_ALTERNATION + r")\b\s*[:=]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|(?!" + re.escape(REDACTED) + r")[^\s\"']+)"
)

_QUERY_PATTERN: Final = re.compile(
    r"(?i)(?P<prefix>[?&](?:" + _SECRET_NAMES_ALTERNATION + r")=)(?P<value>[^&\s#]+)"
)

_USERINFO_PATTERN: Final = re.compile(
    r"(?i)(?P<prefix>\b[a-z][a-z0-9+.\-]*://)(?P<userinfo>[^/@\s]+)@"
)

_AUTHORIZATION_PATTERN: Final = re.compile(
    r"(?i)(?P<prefix>\b(?:authorization|proxy-authorization)\s*:\s*\S+\s+)(?P<value>\S+)"
)

_SCHEME_TOKEN_PATTERN: Final = re.compile(
    r"(?i)(?P<prefix>\b(?:bearer|basic|token)\s+)(?P<value>[A-Za-z0-9._~+/=\-]{8,})"
)

#: PyPI upload tokens have a distinctive shape, and they leak into log output
#: easily because people paste them into commands.
_PYPI_TOKEN_PATTERN: Final = re.compile(r"\bpypi-[A-Za-z0-9_\-]{16,}={0,2}")


def _mask(match: re.Match[str]) -> str:
    if "value" in match.groupdict():
        return f"{match.group('prefix')}{REDACTED}"

    return f"{match.group('prefix')}{REDACTED}@"


def redact(text: str) -> str:
    """Return ``text`` with every recognised secret replaced by ``***``.

    Safe to call on any string, including strings that contain no secret: the
    patterns require a recognisable shape of their own, so an ordinary URL or a
    version number passes through unchanged.
    """

    if not text:
        return text

    result = _USERINFO_PATTERN.sub(_mask, text)
    result = _AUTHORIZATION_PATTERN.sub(_mask, result)
    result = _QUERY_PATTERN.sub(_mask, result)
    result = _SECRET_NAME_PATTERN.sub(_mask, result)
    result = _SCHEME_TOKEN_PATTERN.sub(_mask, result)
    result = _PYPI_TOKEN_PATTERN.sub(REDACTED, result)

    return result


def redact_url(url: str) -> str:
    """Mask the credentials of a URL while keeping it recognisable.

    Returns the URL with any userinfo replaced by ``***`` and any
    secret-looking query value masked. The host, path and non-secret query
    parameters survive, because they are what makes an error message useful.
    """

    masked = redact(url)

    # `redact` already handles userinfo; this exists so callers that only care
    # about a URL have an intention-revealing name to call.
    return masked


def redact_mapping(values: Mapping[str, Any]) -> dict[str, str]:
    """Redact every value of a mapping, for diagnostics bundles.

    Keys are kept as they are - a diagnostics report that hides *which*
    settings exist is much less useful - but a key whose own name says it holds
    a secret gets its value replaced outright rather than merely scanned.
    """

    secret_key_names = tuple(name.replace("[_-]?", "") for name in _SECRET_NAMES)

    result: dict[str, str] = {}
    for key, value in values.items():
        rendered = value if isinstance(value, str) else repr(value)
        folded = key.lower().replace("-", "").replace("_", "")
        if any(folded == name for name in secret_key_names) or any(
            part in folded for part in ("password", "secret", "token", "apikey", "credential")
        ):
            result[key] = REDACTED
        else:
            result[key] = redact(rendered)

    return result


class RedactingFilter(logging.Filter):
    """Apply :func:`redact` to every log record before it is formatted.

    Attached to the root logger by :mod:`pypackhub.core.logging_setup`, so a
    ``logger.info("... %s", url)`` anywhere in the codebase is covered without
    the caller having to remember anything.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.msg))

        # `record.args` is a tuple for %-formatting, a mapping for
        # %(name)-formatting, and occasionally a single value. All three have
        # to be handled, or the redaction silently stops working for one style.
        args = record.args
        if isinstance(args, Mapping):
            record.args = {
                key: redact(value) if isinstance(value, str) else value
                for key, value in args.items()
            }
        elif isinstance(args, tuple):
            record.args = tuple(
                redact(argument) if isinstance(argument, str) else argument for argument in args
            )
        elif isinstance(args, str):
            record.args = redact(args)

        # Anything already rendered into the record (an exception traceback, a
        # custom `exc_text`) bypasses the message path and has to be cleaned
        # separately.
        if record.exc_text:
            record.exc_text = redact(record.exc_text)

        return True


class CredentialStoreError(RuntimeError):
    """The operating system keychain could not be used."""


class CredentialStore:
    """Store index credentials in the OS keychain.

    A backend that is unavailable (a headless Linux box with no Secret Service,
    for example) raises :class:`CredentialStoreError` instead of silently
    downgrading to a plaintext file.
    """

    def __init__(self, service: str = SERVICE_NAME) -> None:
        self._service = service

    @staticmethod
    def _keyring() -> Any:
        # Imported lazily so that importing `pypackhub.core.secrets` - and
        # therefore the runner, which redacts its own command lines - does not
        # require a working keychain backend.
        import keyring

        return keyring

    def get(self, key: str) -> str | None:
        """Return the stored secret, or ``None`` when nothing is stored."""

        try:
            return self._keyring().get_password(self._service, key)
        except Exception as error:
            raise CredentialStoreError(
                f"could not read the credential for {key!r} from the system keychain: "
                f"{redact(str(error))}"
            ) from error

    def set(self, key: str, secret: str) -> None:
        """Store a secret. The secret is never logged, not even on failure."""

        try:
            self._keyring().set_password(self._service, key, secret)
        except Exception as error:
            raise CredentialStoreError(
                f"could not store the credential for {key!r} in the system keychain: "
                f"{redact(str(error))}"
            ) from error

    def delete(self, key: str) -> None:
        """Remove a stored secret, ignoring the case where none existed."""

        try:
            self._keyring().delete_password(self._service, key)
        except Exception as error:
            message = redact(str(error))
            if "not found" in message.lower() or "no password" in message.lower():
                return
            raise CredentialStoreError(
                f"could not delete the credential for {key!r}: {message}"
            ) from error

    def __repr__(self) -> str:
        return f"CredentialStore(service={self._service!r})"
