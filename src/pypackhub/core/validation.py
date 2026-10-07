"""Validate everything that comes from outside the application.

Nothing in this module trusts its input (SPEC section 11.2). A package spec, an
environment name, a path, an index URL and the contents of an imported
``requirements.txt`` are all treated as hostile until they have been through
here.

The threats being defended against, in order of how often they actually show up:

* **Option injection.** ``-r /etc/passwd`` or ``--index-url=http://evil`` typed
  into a name field would otherwise be handed to pip as an instruction. Any
  leading ``-`` is refused outright, and imported files that carry options are
  held back for review instead of being applied.
* **Shell injection.** PyPackHub never uses a shell, so ``;``/``&&``/``$(...)``
  cannot execute anything through the runner - but a confirmation dialog shows
  a copy-pasteable command line, so these characters are refused anyway rather
  than displayed as if they were safe.
* **Path escapes.** ``../../x``, a symlink pointing out of an environment, a
  home directory or a filesystem root must never become a delete target.
* **Redirected installs.** Credentials inside an index URL would end up in a
  command line and in a log; a plain-``http`` index would let anyone on the
  path replace a package.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from packaging.requirements import InvalidRequirement, Requirement

from pypackhub.core.platform_guard import EnvironmentKind, detect_kind

__all__ = [
    "MAX_SPEC_LENGTH",
    "LineKind",
    "ParsedRequirements",
    "Reason",
    "RequirementLine",
    "ValidationError",
    "ensure_deletable_environment",
    "ensure_inside",
    "is_verified_environment",
    "parse_requirements",
    "validate_environment_name",
    "validate_index_url",
    "validate_package_spec",
]

#: Longest package specifier accepted. Real ones are far shorter; the cap keeps
#: a pathological input from reaching a regular expression nobody measured.
MAX_SPEC_LENGTH: Final = 512

#: Longest environment name accepted, matching conda's own practical limit.
MAX_ENVIRONMENT_NAME_LENGTH: Final = 64

#: A PEP 508 name: starts and ends alphanumeric, `-`, `_` and `.` in between.
_NAME_PATTERN: Final = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")

#: `name`, optional `[extras]`, optional comma-separated version specifiers.
_SPEC_PATTERN: Final = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"(?:\[(?P<extras>[A-Za-z0-9](?:[A-Za-z0-9._,-]*[A-Za-z0-9])?)\])?"
    r"(?P<specifier>(?:[=<>!~]=?[A-Za-z0-9][A-Za-z0-9.*+!_-]*)(?:,(?:[=<>!~]=?[A-Za-z0-9][A-Za-z0-9.*+!_-]*))*)?$"
)

_ENVIRONMENT_NAME_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: Characters that carry meaning to a shell and that never appear in a valid
#: specifier. `<` and `>` are absent on purpose: they are version operators.
_SHELL_METACHARACTERS: Final = frozenset(";`$&|\\\"'(){}")

#: The same screen, relaxed for imported files, where `;` separates a real
#: environment marker and quotes delimit its values.
_SHELL_METACHARACTERS_WITH_MARKERS: Final = _SHELL_METACHARACTERS - frozenset(";\"'")

#: Whitespace is only tolerated next to specifier punctuation, so that
#: "requests >= 2.31" works while "requests bar" is refused.
_WHITESPACE_RUN: Final = re.compile(r"\s+")
_SPECIFIER_ADJACENT: Final = "=<>!~,"

#: Zero-width and bidirectional characters used to make one name render as
#: another. None of them belong in a package name, a path or a URL.
_INVISIBLE_CHARACTERS: Final = frozenset(
    {
        "\u200b",
        "\u200c",
        "\u200d",
        "\u2060",
        "\ufeff",
        "\ufe0f",
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
    }
)

_URL_SCHEMES: Final = ("https",)
_DIRECT_REFERENCE_SCHEMES: Final = ("https", "git+https")

_UNSAFE_DIRECT_REFERENCE_SCHEMES: Final = (
    "file",
    "git",
    "git+http",
    "git+ssh",
    "hg",
    "svn",
    "bzr",
)


class Reason(str, Enum):
    """Why a value was refused. Stable identifiers, translated by the UI."""

    EMPTY = "empty"
    TOO_LONG = "too_long"
    CONTROL_CHARACTER = "control_character"
    NON_ASCII = "non_ascii"
    VARIATION_SELECTOR = "variation_selector"
    OPTION_INJECTION = "option_injection"
    SHELL_METACHARACTER = "shell_metacharacter"
    INVALID_NAME = "invalid_name"
    INVALID_SPECIFIER = "invalid_specifier"
    DIRECT_REFERENCE_NOT_ALLOWED = "direct_reference_not_allowed"
    MARKER_NOT_ALLOWED = "marker_not_allowed"
    INSECURE_SCHEME = "insecure_scheme"
    UNSUPPORTED_SCHEME = "unsupported_scheme"
    EMBEDDED_CREDENTIALS = "embedded_credentials"
    MISSING_HOST = "missing_host"
    NOT_FOUND = "not_found"
    NOT_A_DIRECTORY = "not_a_directory"
    NOT_VERIFIED_ENVIRONMENT = "not_verified_environment"
    FORBIDDEN_TARGET = "forbidden_target"
    OUTSIDE_ROOT = "outside_root"


class ValidationError(ValueError):
    """A value was refused, with a machine-readable reason and a human hint."""

    def __init__(
        self,
        reason: Reason,
        message: str,
        *,
        value: str | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.value = value
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        if self.hint:
            return f"{self.message} ({self.hint})"

        return self.message


def _screen_characters(value: str) -> None:
    """Reject anything that no legitimate spec, name or URL needs to contain."""

    if not value:
        raise ValidationError(Reason.EMPTY, "the value is empty", value=value)

    if len(value) > MAX_SPEC_LENGTH:
        raise ValidationError(
            Reason.TOO_LONG,
            f"the value is longer than {MAX_SPEC_LENGTH} characters",
            value=value,
        )

    for character in value:
        if character in "\x00\n\r\t\v\f":
            raise ValidationError(
                Reason.CONTROL_CHARACTER,
                "the value contains a control character",
                value=value,
            )

    # Checked before the ASCII test below, because the interesting invisible
    # characters are themselves non-ASCII and would otherwise be reported as
    # "just" a lookalike.
    invisible = sorted({character for character in value if character in _INVISIBLE_CHARACTERS})
    if invisible:
        raise ValidationError(
            Reason.VARIATION_SELECTOR,
            "the value contains invisible characters",
            value=value,
            hint=f"offending characters: {[hex(ord(one)) for one in invisible]}",
        )

    if not value.isascii():
        offenders = "".join(sorted({character for character in value if not character.isascii()}))
        raise ValidationError(
            Reason.NON_ASCII,
            "the value contains non-ASCII characters that can be confused with real ones",
            value=value,
            hint=f"offending characters: {offenders!r}",
        )


def validate_package_name(name: str) -> str:
    """Validate a bare package name (no version, no extras)."""

    _screen_characters(name)

    if not _NAME_PATTERN.match(name):
        raise ValidationError(
            Reason.INVALID_NAME,
            f"{name!r} is not a valid package name",
            value=name,
            hint="a package name starts and ends with a letter or digit",
        )

    return name


def validate_package_spec(
    spec: str,
    *,
    allow_direct_reference: bool = False,
    allow_marker: bool = False,
) -> str:
    """Validate a package specifier typed by a person.

    The strict path accepts exactly ``name``, ``name[extras]`` and
    ``name<specifiers>``. Direct references (``pkg @ https://...``) and
    environment markers are refused unless the caller explicitly asks for them,
    because both are ways to point an install somewhere unexpected.
    """

    _screen_characters(spec)

    if spec.startswith("-"):
        raise ValidationError(
            Reason.OPTION_INJECTION,
            f"{spec!r} starts with '-', which would be treated as an installer option",
            value=spec,
            hint="a package name cannot begin with a dash",
        )

    forbidden = _SHELL_METACHARACTERS_WITH_MARKERS if allow_marker else _SHELL_METACHARACTERS
    offenders = sorted({character for character in spec if character in forbidden})
    if offenders:
        raise ValidationError(
            Reason.SHELL_METACHARACTER,
            f"{spec!r} contains characters a shell would act on",
            value=spec,
            hint=f"offending characters: {''.join(offenders)!r}",
        )

    if ";" in spec and not allow_marker:
        raise ValidationError(
            Reason.MARKER_NOT_ALLOWED,
            f"{spec!r} contains an environment marker",
            value=spec,
            hint="environment markers are only accepted when imported from a file",
        )

    if "@" in spec and not allow_direct_reference:
        raise ValidationError(
            Reason.DIRECT_REFERENCE_NOT_ALLOWED,
            f"{spec!r} points at a URL instead of a package on an index",
            value=spec,
            hint="enable advanced installs if you really mean to install from a URL",
        )

    # The relaxed path hands the line straight to packaging: only that parser
    # understands markers and direct references well enough to be trusted with
    # deciding what the line means.
    if allow_direct_reference or allow_marker:
        return _validate_relaxed_spec(spec.strip(), spec)

    normalized = _normalize_whitespace(spec)
    match = _SPEC_PATTERN.match(normalized)
    if match is None:
        raise ValidationError(
            Reason.INVALID_SPECIFIER,
            f"{spec!r} is not a valid package specifier",
            value=spec,
            hint="expected something like 'requests', 'requests[security]' or 'requests>=2.31'",
        )

    # packaging has the last word: if its parser disagrees with the pattern
    # above, the pattern is the thing that is wrong.
    try:
        Requirement(normalized)
    except InvalidRequirement as error:
        raise ValidationError(
            Reason.INVALID_SPECIFIER,
            f"{spec!r} is not a valid package specifier: {error}",
            value=spec,
        ) from error

    return normalized


def _normalize_whitespace(spec: str) -> str:
    """Drop whitespace around specifier punctuation, refuse it anywhere else."""

    for match in _WHITESPACE_RUN.finditer(spec):
        before = spec[match.start() - 1] if match.start() else ""
        after = spec[match.end()] if match.end() < len(spec) else ""

        if before in _SPECIFIER_ADJACENT or after in _SPECIFIER_ADJACENT:
            continue

        raise ValidationError(
            Reason.SHELL_METACHARACTER,
            f"{spec!r} contains whitespace outside a version specifier",
            value=spec,
            hint="remove the spaces, or write them around the comparison operator",
        )

    return _WHITESPACE_RUN.sub("", spec)


def _validate_relaxed_spec(normalized: str, original: str) -> str:
    """Second path for imported files, which may legitimately carry more."""

    try:
        requirement = Requirement(normalized)
    except InvalidRequirement as error:
        raise ValidationError(
            Reason.INVALID_SPECIFIER,
            f"{original!r} is not a valid requirement: {error}",
            value=original,
        ) from error

    validate_package_name(requirement.name)

    if requirement.url:
        _validate_url(
            requirement.url,
            original=original,
            allowed_schemes=_DIRECT_REFERENCE_SCHEMES,
            allow_insecure_http=False,
        )

    return normalized


def validate_environment_name(name: str) -> str:
    """Validate a name for a virtual or conda environment we are about to create."""

    _screen_characters(name)

    if len(name) > MAX_ENVIRONMENT_NAME_LENGTH:
        raise ValidationError(
            Reason.TOO_LONG,
            f"an environment name is limited to {MAX_ENVIRONMENT_NAME_LENGTH} characters",
            value=name,
        )

    if name.startswith("-"):
        raise ValidationError(
            Reason.OPTION_INJECTION,
            f"{name!r} starts with '-'",
            value=name,
        )

    if not _ENVIRONMENT_NAME_PATTERN.match(name):
        raise ValidationError(
            Reason.INVALID_NAME,
            f"{name!r} is not a valid environment name",
            value=name,
            hint="use letters, digits, dots, dashes and underscores",
        )

    return name


def _validate_url(
    url: str,
    *,
    original: str,
    allowed_schemes: tuple[str, ...],
    allow_insecure_http: bool,
) -> str:
    _screen_characters(url)

    parts = urlsplit(url)

    if not parts.scheme:
        raise ValidationError(
            Reason.UNSUPPORTED_SCHEME,
            f"{original!r} has no scheme",
            value=original,
            hint="a URL has to start with https://",
        )

    scheme = parts.scheme.lower()

    if scheme == "http" and allow_insecure_http:
        pass
    elif scheme not in allowed_schemes:
        reason = (
            Reason.INSECURE_SCHEME if scheme in {"http", "git+http"} else Reason.UNSUPPORTED_SCHEME
        )
        raise ValidationError(
            reason,
            f"{original!r} does not use a secure scheme",
            value=original,
            hint=f"allowed schemes: {', '.join(allowed_schemes)}",
        )

    if not parts.hostname:
        raise ValidationError(
            Reason.MISSING_HOST,
            f"{original!r} has no host",
            value=original,
        )

    if parts.username or parts.password:
        raise ValidationError(
            Reason.EMBEDDED_CREDENTIALS,
            f"{original!r} contains credentials in the URL",
            value=original,
            hint="store the credential in the system keychain instead; a URL is logged",
        )

    return url


def validate_index_url(url: str, *, allow_insecure_http: bool = False) -> str:
    """Validate a package index URL.

    HTTPS only. ``allow_insecure_http`` exists because a private mirror on a
    local network is a real thing, but the caller is expected to have shown the
    user a warning that has to be acknowledged before it is set.
    """

    return _validate_url(
        url.strip(),
        original=url,
        allowed_schemes=_URL_SCHEMES,
        allow_insecure_http=allow_insecure_http,
    )


class LineKind(str, Enum):
    """What one line of an imported requirements file turned out to be."""

    PACKAGE = "package"
    DIRECT_REFERENCE = "direct_reference"
    EDITABLE = "editable"
    INCLUDE = "include"
    CONSTRAINT = "constraint"
    INDEX_URL = "index_url"
    EXTRA_INDEX_URL = "extra_index_url"
    FIND_LINKS = "find_links"
    TRUSTED_HOST = "trusted_host"
    REQUIRE_HASHES = "require_hashes"
    HASH = "hash"
    OPTION = "option"
    INVALID = "invalid"
    COMMENT = "comment"


@dataclass(frozen=True, slots=True)
class RequirementLine:
    """One parsed line, with the decision already made about it."""

    number: int
    raw: str
    kind: LineKind
    value: str = ""
    requires_review: bool = False
    reason: str = ""

    @property
    def is_package(self) -> bool:
        return self.kind in {LineKind.PACKAGE, LineKind.DIRECT_REFERENCE}


@dataclass(frozen=True, slots=True)
class ParsedRequirements:
    """A parsed requirements file, split by what may be applied unattended."""

    source: str
    lines: tuple[RequirementLine, ...]

    @property
    def packages(self) -> tuple[RequirementLine, ...]:
        """Lines that are packages and carry nothing that needs a decision."""

        return tuple(line for line in self.lines if line.is_package and not line.requires_review)

    @property
    def review(self) -> tuple[RequirementLine, ...]:
        """Lines that must be shown to the user before anything happens."""

        return tuple(line for line in self.lines if line.requires_review)

    @property
    def invalid(self) -> tuple[RequirementLine, ...]:
        return tuple(line for line in self.lines if line.kind is LineKind.INVALID)

    @property
    def requires_review(self) -> bool:
        return bool(self.review)

    @property
    def is_applicable(self) -> bool:
        """True only when the file can be applied with no human decision."""

        return not self.requires_review and not self.invalid


_OPTION_KINDS: Final = (
    ("-r", LineKind.INCLUDE),
    ("--requirement", LineKind.INCLUDE),
    ("-c", LineKind.CONSTRAINT),
    ("--constraint", LineKind.CONSTRAINT),
    ("-e", LineKind.EDITABLE),
    ("--editable", LineKind.EDITABLE),
    ("-i", LineKind.INDEX_URL),
    ("--index-url", LineKind.INDEX_URL),
    ("--extra-index-url", LineKind.EXTRA_INDEX_URL),
    ("-f", LineKind.FIND_LINKS),
    ("--find-links", LineKind.FIND_LINKS),
    ("--trusted-host", LineKind.TRUSTED_HOST),
    ("--require-hashes", LineKind.REQUIRE_HASHES),
    ("--hash", LineKind.HASH),
)


def _classify_option(stripped: str) -> tuple[LineKind, str, bool]:
    """Classify a line that starts with a dash."""

    for flag, kind in _OPTION_KINDS:
        if stripped == flag:
            return kind, "", kind is not LineKind.HASH
        if stripped.startswith(f"{flag}="):
            return kind, stripped[len(flag) + 1 :], kind is not LineKind.HASH
        if stripped.startswith(f"{flag} "):
            return kind, stripped[len(flag) + 1 :].strip(), kind is not LineKind.HASH

    return LineKind.OPTION, stripped, True


def _strip_comment(line: str) -> str:
    """Remove a trailing comment without breaking anything else."""

    without_inline = line.split(" #", 1)[0]

    return without_inline.rstrip()


def parse_requirements(text: str, *, source: str = "<memory>") -> ParsedRequirements:
    """Parse an imported requirements file line by line.

    The file is untrusted input: it may install from a private index, add a
    search path, or fetch a wheel from anywhere. Every line is therefore
    classified, and anything that is not a plain package is marked as needing
    review rather than being applied on the user's behalf.
    """

    parsed: list[RequirementLine] = []

    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#"):
            parsed.append(RequirementLine(number=number, raw=raw_line, kind=LineKind.COMMENT))
            continue

        stripped = _strip_comment(line)

        if not stripped:
            parsed.append(RequirementLine(number=number, raw=raw_line, kind=LineKind.COMMENT))
            continue

        if stripped.startswith("-"):
            kind, value, requires_review = _classify_option(stripped)

            if kind is LineKind.INDEX_URL and value:
                try:
                    value = validate_index_url(value)
                except ValidationError as error:
                    parsed.append(
                        RequirementLine(
                            number=number,
                            raw=raw_line,
                            kind=LineKind.INVALID,
                            value=stripped,
                            requires_review=True,
                            reason=error.reason.value,
                        )
                    )
                    continue

            parsed.append(
                RequirementLine(
                    number=number,
                    raw=raw_line,
                    kind=kind,
                    value=value,
                    requires_review=requires_review,
                    reason="" if not requires_review else "installer option",
                )
            )
            continue

        # Hash-pinned lines look like `requests==2.31.0 --hash=sha256:...`. The
        # hashes cannot be enforced until Phase 4, so the line is kept - and
        # flagged for review rather than silently downgraded to an unpinned
        # install, which is exactly the kind of quiet weakening this module
        # exists to prevent.
        head, _, trailing = stripped.partition(" ")
        pinned_by_hash = False
        if trailing:
            tokens = trailing.split()
            if tokens and all(token.startswith("--hash=") for token in tokens):
                pinned_by_hash = True
                stripped = head

        try:
            normalized = validate_package_spec(
                stripped, allow_direct_reference=True, allow_marker=True
            )
        except ValidationError as error:
            parsed.append(
                RequirementLine(
                    number=number,
                    raw=raw_line,
                    kind=LineKind.INVALID,
                    value=stripped,
                    requires_review=True,
                    reason=error.reason.value,
                )
            )
            continue

        if "@" in normalized:
            # A direct reference fetches a file from a URL the file chose. That
            # is a decision for the person importing it, not for this parser.
            parsed.append(
                RequirementLine(
                    number=number,
                    raw=raw_line,
                    kind=LineKind.DIRECT_REFERENCE,
                    value=normalized,
                    requires_review=True,
                    reason="direct reference",
                )
            )
            continue

        parsed.append(
            RequirementLine(
                number=number,
                raw=raw_line,
                kind=LineKind.PACKAGE,
                value=normalized,
                requires_review=pinned_by_hash,
                reason=(
                    "hash pinned: PyPackHub does not verify hashes yet" if pinned_by_hash else ""
                ),
            )
        )

    return ParsedRequirements(source=source, lines=tuple(parsed))


def is_verified_environment(path: Path) -> bool:
    """True only for a directory that really is an environment.

    The spec is explicit that an unrelated folder must never be mistaken for
    one, so this requires the markers the tool that created it leaves behind:
    ``pyvenv.cfg`` for virtual environments (including pyenv's) and ``conda-meta``
    for conda environments.
    """

    resolved = Path(path).expanduser()

    if not resolved.is_dir():
        return False

    resolved = resolved.resolve()

    if resolved.parent == resolved:
        return False

    if resolved == Path.home().resolve():
        return False

    kind = detect_kind(resolved)

    return kind in {EnvironmentKind.VENV, EnvironmentKind.CONDA, EnvironmentKind.PYBENV}


def ensure_inside(path: Path, root: Path) -> Path:
    """Resolve ``path`` and prove it is inside ``root``.

    Symlinks are resolved before the check, so a link that points out of the
    environment is refused rather than followed.
    """

    resolved_root = Path(root).expanduser().resolve()
    resolved = Path(path).expanduser().resolve()

    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValidationError(
            Reason.OUTSIDE_ROOT,
            f"{resolved} is outside {resolved_root}",
            value=str(path),
        )

    return resolved


def ensure_deletable_environment(
    path: Path,
    *,
    home: Path | None = None,
    protected_prefixes: tuple[Path, ...] = (),
) -> Path:
    """Prove that a directory may be deleted as an environment, or refuse.

    Refusals are deliberate and specific: not found, not a directory, not a
    verified environment, or a target that is a home directory, a filesystem
    root, or a system Python. Deleting any of those would be catastrophic and
    irreversible, so the check is not a warning the UI can click past.
    """

    candidate = Path(path).expanduser()

    if not candidate.exists():
        raise ValidationError(
            Reason.NOT_FOUND,
            f"{candidate} does not exist",
            value=str(path),
        )

    if not candidate.is_dir():
        raise ValidationError(
            Reason.NOT_A_DIRECTORY,
            f"{candidate} is not a directory",
            value=str(path),
        )

    resolved = candidate.resolve()
    home_directory = (home if home is not None else Path.home()).expanduser().resolve()

    if resolved == home_directory or resolved in home_directory.parents:
        raise ValidationError(
            Reason.FORBIDDEN_TARGET,
            f"{resolved} is your home directory or one of its parents",
            value=str(path),
            hint="refusing to delete it",
        )

    if resolved.parent == resolved:
        raise ValidationError(
            Reason.FORBIDDEN_TARGET,
            f"{resolved} is a filesystem root",
            value=str(path),
        )

    # The interpreter PyPackHub itself runs under is always protected, whether
    # or not the caller passed anything in.
    protected = {Path(sys.base_prefix).expanduser().resolve()}
    protected.update(prefix.expanduser().resolve() for prefix in protected_prefixes)

    for prefix in protected:
        if resolved == prefix or prefix in resolved.parents:
            raise ValidationError(
                Reason.FORBIDDEN_TARGET,
                f"{resolved} is (or is inside) a protected Python installation: {prefix}",
                value=str(path),
                hint="refusing to delete a Python installation",
            )

    if not is_verified_environment(resolved):
        raise ValidationError(
            Reason.NOT_VERIFIED_ENVIRONMENT,
            f"{resolved} does not look like a Python environment",
            value=str(path),
            hint="a virtual environment has a pyvenv.cfg; a conda environment has conda-meta",
        )

    return resolved
