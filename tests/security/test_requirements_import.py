"""SPEC section 11.9: an imported requirements file is untrusted input.

The rule the tests below encode: a line is either a plain package, or it is
held back for the user to look at. Nothing in between - an installer option
never gets applied because it happened to appear in a file.
"""

from __future__ import annotations

import pytest

from pypackhub.core.validation import LineKind, parse_requirements

pytestmark = pytest.mark.security

REVIEW_REQUIRED = [
    ("--index-url=https://mirror.example/simple", LineKind.INDEX_URL),
    ("--extra-index-url=https://mirror.example/simple", LineKind.EXTRA_INDEX_URL),
    ("-i https://mirror.example/simple", LineKind.INDEX_URL),
    ("--trusted-host=mirror.example", LineKind.TRUSTED_HOST),
    ("-e .", LineKind.EDITABLE),
    ("--editable ../local-project", LineKind.EDITABLE),
    ("-r other-requirements.txt", LineKind.INCLUDE),
    ("--requirement=other.txt", LineKind.INCLUDE),
    ("-c constraints.txt", LineKind.CONSTRAINT),
    ("--find-links=https://example.org/wheels", LineKind.FIND_LINKS),
    ("--require-hashes", LineKind.REQUIRE_HASHES),
    ("--prefix=/usr/local", LineKind.OPTION),
]

PLAIN_PACKAGES = [
    "requests==2.31.0",
    "numpy>=1.26,<3",
    "pandas",
    "ruamel.yaml==0.18.6",
    "requests[socks]>=2.31  # with the SOCKS extra",
]


@pytest.mark.parametrize("line,kind", REVIEW_REQUIRED)
def test_installer_options_are_held_back_for_review(line: str, kind: LineKind) -> None:
    parsed = parse_requirements(line)

    assert len(parsed.lines) == 1
    entry = parsed.lines[0]

    assert entry.kind is kind
    assert entry.requires_review is True
    assert parsed.is_applicable is False
    assert entry in parsed.review


@pytest.mark.parametrize("line", PLAIN_PACKAGES)
def test_plain_packages_apply_without_a_prompt(line: str) -> None:
    parsed = parse_requirements(line)

    assert len(parsed.lines) == 1
    entry = parsed.lines[0]

    assert entry.kind is LineKind.PACKAGE
    assert entry.requires_review is False
    assert parsed.is_applicable is True
    assert entry in parsed.packages


def test_a_plain_http_index_is_rejected_outright() -> None:
    parsed = parse_requirements("--index-url=http://evil.example/simple")

    entry = parsed.lines[0]

    assert entry.kind is LineKind.INVALID
    assert entry.requires_review is True
    assert parsed.is_applicable is False


def test_a_direct_reference_needs_review() -> None:
    parsed = parse_requirements("demo @ https://example.org/demo-1.0-py3-none-any.whl")

    entry = parsed.lines[0]

    assert entry.kind is LineKind.DIRECT_REFERENCE
    assert entry.requires_review is True
    assert parsed.is_applicable is False


@pytest.mark.parametrize("line", ["foo; ls", "foo && whoami", "$(id)", "not a requirement at all"])
def test_garbage_lines_are_invalid(line: str) -> None:
    parsed = parse_requirements(line)

    assert parsed.lines[0].kind is LineKind.INVALID
    assert parsed.is_applicable is False
    assert parsed.invalid


def test_markers_are_accepted_in_an_imported_file() -> None:
    parsed = parse_requirements('requests>=2.31; python_version >= "3.9"')

    assert parsed.lines[0].kind is LineKind.PACKAGE
    assert parsed.is_applicable is True


def test_hash_pinned_lines_are_flagged_rather_than_silently_unpinned() -> None:
    parsed = parse_requirements("requests==2.31.0 --hash=sha256:" + "0" * 64)

    entry = parsed.lines[0]

    assert entry.kind is LineKind.PACKAGE
    assert entry.value == "requests==2.31.0"
    # The hash cannot be enforced yet, so the line is held back instead of
    # being applied as an ordinary unpinned install.
    assert entry.requires_review is True
    assert parsed.is_applicable is False


def test_comments_and_blank_lines_are_ignored() -> None:
    parsed = parse_requirements("# a comment\n\n   \nrequests\n")

    kinds = [line.kind for line in parsed.lines]

    assert kinds == [LineKind.COMMENT, LineKind.PACKAGE]


def test_line_numbers_survive_comment_and_blank_filtering() -> None:
    parsed = parse_requirements("requests\n\n# comment\nnumpy\n")

    numbers = [line.number for line in parsed.lines]

    assert numbers == [1, 3, 4]


def test_a_file_mixing_safe_and_unsafe_lines_is_not_applicable() -> None:
    parsed = parse_requirements(
        "\n".join(
            [
                "requests==2.31.0",
                "numpy>=1.26",
                "--index-url=https://mirror.example/simple",
                "pandas",
            ]
        )
    )

    assert parsed.is_applicable is False
    assert [line.value for line in parsed.packages] == [
        "requests==2.31.0",
        "numpy>=1.26",
        "pandas",
    ]
    assert len(parsed.review) == 1


def test_the_source_is_recorded_for_the_review_ui() -> None:
    parsed = parse_requirements("--trusted-host=x", source="/tmp/requirements.txt")

    assert parsed.source == "/tmp/requirements.txt"
