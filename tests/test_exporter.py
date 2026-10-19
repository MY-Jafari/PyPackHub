"""The requirements exporter: what a committed file says must be installable.

The import side already refuses what it cannot trust; the export side has
the same duty in reverse - a line this module writes must round-trip through
the same validator the importer uses, or it is a file that lies about being
usable.
"""

from __future__ import annotations

from pypackhub.core.exporter import export_requirements


def test_the_export_is_sorted_and_pinned() -> None:
    text = export_requirements([("urllib3", "2.2.0"), ("requests", "2.32.3")])

    assert text == "requests==2.32.3\nurllib3==2.2.0\n"


def test_duplicate_rows_collapse() -> None:
    """Two listings of one package - a merge gone by, a cache served twice -
    must not produce a file that installs the same thing twice."""
    text = export_requirements(
        [("requests", "2.32.3"), ("requests", "2.32.3"), ("urllib3", "2.2.0")]
    )

    assert text.count("requests==2.32.3") == 1


def test_a_row_without_a_version_is_flagged_not_faked() -> None:
    """``requests`` unpinned would silently mean "any version"; the honest
    export says the row could not be pinned and moves on."""
    text = export_requirements([("broken", ""), ("ok", "1.0")])

    assert "ok==1.0" in text
    assert "broken==" not in text
    assert "# skipped: no name or version recorded" in text


def test_a_name_that_cannot_round_trip_is_reported_in_the_file() -> None:
    """A name the validator refuses is a broken row, not a quiet omission: the
    user sees exactly what was left out."""
    text = export_requirements([("not a name!", "1.0")])

    assert text.startswith("# skipped: unparsable entry")


def test_an_empty_environment_exports_an_empty_file() -> None:
    assert export_requirements([]) == ""
