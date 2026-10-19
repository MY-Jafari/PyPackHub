"""Supply-chain signals: the typosquat warning and the trust flags.

The gate these tests exist for is the one the security core deferred on
purpose: ``reqeusts``, ``numpyy`` and ``pandsa`` must warn before anything is
installed. Everything around that gate is here too, because a typosquat
check is only as good as its false-positive rate: an honest package one
letter-adjacent to a popular name must not be slandered, and a check that
flags everything flags nothing.
"""

from __future__ import annotations

from datetime import date

from pypackhub.core.net import HttpError
from pypackhub.core.pypi import PyPIClient, ReleaseFacts
from pypackhub.core.trust import (
    TrustFlags,
    bundled_catalog,
    damerau_levenshtein,
    evaluate_trust,
    typosquat_check,
)

#: The three shapes a real typo takes: swapped, doubled, swapped again.
GATE_CASES = ("reqeusts", "numpyy", "pandsa")

#: Small enough to control every distance the tests assert.
CATALOGUE = ("requests", "numpy", "pandas", "flask", "nltk", "install")


def test_the_gate_cases_warn_with_the_name_they_are_close_to() -> None:
    """The Phase 1 acceptance cases, deferred to this module, now warn."""
    for name, popular in (
        ("reqeusts", "requests"),
        ("numpyy", "numpy"),
        ("pandsa", "pandas"),
    ):
        warning = typosquat_check(name, CATALOGUE)
        assert warning is not None, f"{name} must warn"
        assert warning.suspect_of == popular


def test_the_gate_cases_warn_against_the_bundled_catalogue() -> None:
    """Not just against a hand-picked list: the shipped catalogue answers too."""
    catalogue = bundled_catalog().names
    assert "requests" in catalogue
    for name in GATE_CASES:
        assert typosquat_check(name, catalogue) is not None


def test_the_bundled_catalogue_is_loaded_once() -> None:
    assert bundled_catalog() is bundled_catalog()


def test_a_popular_name_itself_is_fine() -> None:
    """The package is not a near-miss of itself."""
    for name in ("requests", "numpy", "flask"):
        assert typosquat_check(name, CATALOGUE) is None


def test_a_far_away_honest_name_is_not_slandered() -> None:
    """Two edits away, or a compound name that merely contains a popular one,
    is somebody's real package - the check must stay quiet."""
    for name in ("requests-toolbelt", "flask-login", "requestsy2"):
        assert typosquat_check(name, CATALOGUE) is None


def test_short_popular_names_are_never_the_answer_to_a_near_miss() -> None:
    """One edit away from a four-letter name is half the namespace."""
    assert typosquat_check("ntlk", CATALOGUE) is None  # nltk is too short to match


def test_a_single_inserted_letter_warns() -> None:
    assert typosquat_check("flaskk", CATALOGUE) is not None


def test_a_lookalike_exact_match_warns_hardest() -> None:
    """``request5`` folds to ``requests``: distance zero is the strongest
    near-miss there is, and it must not be treated as "no match"."""
    warning = typosquat_check("request5", CATALOGUE)
    assert warning is not None
    assert warning.suspect_of == "requests"
    assert warning.distance == 0


def test_a_homoglyph_name_folds_to_the_real_one() -> None:
    """``1nstall`` reads as ``install`` once lookalike characters fold."""
    warning = typosquat_check("1nstall", CATALOGUE)
    assert warning is not None
    assert warning.suspect_of == "install"


def test_punctuation_tricks_are_normalised_before_comparing() -> None:
    """``re quests`` is not a name, but ``re_quest.s`` is - and it is far."""
    assert typosquat_check("re_quest.s", CATALOGUE) is None


def test_an_empty_name_is_not_checked_at_all() -> None:
    assert typosquat_check("", CATALOGUE) is None
    assert typosquat_check("   ", CATALOGUE) is None


def test_a_swapped_pair_is_one_edit() -> None:
    """The distance the whole check rests on: a transposition counts once."""
    assert damerau_levenshtein("reqeusts", "requests") == 1
    assert damerau_levenshtein("requests", "requests") == 0
    assert damerau_levenshtein("flask", "flask-login") == 6


# -- the trust flags -------------------------------------------------------------


class _FactsHttp:
    """Stands in for the network with one canned JSON answer."""

    def __init__(self, payload: object) -> None:
        self._payload = payload

    def get_json(self, url: str, *, max_bytes: int) -> object:
        return self._payload


def _releases_payload() -> dict[str, object]:
    return {
        "info": {
            "name": "pkg",
            "version": "2.0",
            "home_page": "",
            "license": "MIT",
            "project_urls": {"Source": "https://example.com/pkg"},
        },
        "releases": {
            "1.0": [
                {
                    "filename": "pkg-1.0.tar.gz",
                    "upload_time_iso_8601": "2015-01-02T03:04:05",
                    "packagetype": "sdist",
                }
            ],
            "2.0": [
                {
                    "filename": "pkg-2.0.tar.gz",
                    "upload_time_iso_8601": "2026-09-01T00:00:00",
                    "packagetype": "sdist",
                },
                {
                    "filename": "pkg-2.0-py3-none-any.whl",
                    "upload_time_iso_8601": "2026-09-01T00:00:00",
                    "packagetype": "bdist_wheel",
                },
            ],
        },
    }


def test_release_facts_come_from_the_index_answer() -> None:
    client = PyPIClient(http=_FactsHttp(_releases_payload()))
    facts = client.release_facts("pkg")

    assert facts.first_release == "2015-01-02"
    assert facts.releases == 2
    assert facts.latest_release == "2026-09-01"
    assert facts.latest_has_wheel is True
    # home_page empty: the first URL the project declares stands in for it.
    assert facts.homepage == "https://example.com/pkg"
    assert facts.license == "MIT"


def test_an_sdist_only_release_is_reported_and_flagged() -> None:
    payload = _releases_payload()
    payload["releases"] = {
        "2.0": [
            {
                "filename": "pkg-2.0.tar.gz",
                "upload_time_iso_8601": "2026-09-01T00:00:00",
                "packagetype": "sdist",
            }
        ]
    }
    facts = PyPIClient(http=_FactsHttp(payload)).release_facts("pkg")

    assert facts.latest_has_wheel is False
    assert evaluate_trust(facts, today=date(2026, 9, 10)).sdist_only is True


class _FailingHttp:
    def get_json(self, url: str, *, max_bytes: int) -> object:
        raise HttpError("the index is unreachable")


def test_an_unreachable_index_degrades_to_unknown_not_to_guesses() -> None:
    facts = PyPIClient(http=_FailingHttp()).release_facts("pkg")

    assert not facts.known
    flags = evaluate_trust(facts)
    assert flags.unknown is True
    # And "unknown" is not "clean": the panel must say it could not tell.
    assert flags.is_clean is False


def test_a_brand_new_package_is_flagged_as_such() -> None:
    flags = evaluate_trust(
        ReleaseFacts(
            name="pkg",
            first_release="2026-10-01",
            releases=1,
            latest_release="2026-10-01",
            latest_has_wheel=True,
            homepage="https://example.com",
        ),
        today=date(2026, 10, 12),
    )
    assert flags.brand_new is True
    assert flags.abandoned is False
    assert flags.is_clean is False


def test_a_dormant_package_is_flagged_as_such() -> None:
    flags = evaluate_trust(
        ReleaseFacts(
            name="pkg",
            first_release="2010-01-01",
            releases=4,
            latest_release="2020-01-01",
            latest_has_wheel=True,
            homepage="https://example.com",
        ),
        today=date(2026, 10, 12),
    )
    assert flags.abandoned is True
    assert flags.brand_new is False


def test_an_old_wheel_shipping_homepaged_package_is_clean() -> None:
    flags = evaluate_trust(
        ReleaseFacts(
            name="pkg",
            first_release="2015-01-02",
            releases=9,
            latest_release="2026-09-01",
            latest_has_wheel=True,
            homepage="https://example.com",
        ),
        today=date(2026, 10, 12),
    )
    assert flags == TrustFlags()
    assert flags.is_clean is True


def test_unparsable_dates_never_guess() -> None:
    flags = evaluate_trust(
        ReleaseFacts(name="pkg", first_release="not-a-date", latest_release="whenever"),
        today=date(2026, 10, 12),
    )
    assert flags.brand_new is False
    assert flags.abandoned is False


def test_a_package_with_no_homepage_at_all_is_flagged() -> None:
    flags = evaluate_trust(
        ReleaseFacts(name="pkg", first_release="2020-01-01", latest_release="2026-01-01"),
        today=date(2026, 10, 12),
    )
    assert flags.no_homepage is True
