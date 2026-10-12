"""Search: what to look up, in what order, and the purpose a package set hints at.

PyPI has no search API, so the ranking is local and the resolution is a
per-name lookup; both halves are testable without a network, which is what
the fakes here are for. The purpose inference at the bottom is the
dashboard's "what is this environment for" guess - deterministic, so the
same environment never describes itself differently twice.
"""

from __future__ import annotations

from pypackhub.core.environments import infer_purpose
from pypackhub.core.search import Catalog, SearchResult, SearchService, rank

CATALOGUE = (
    "flask",
    "flask-login",
    "flask-wtf",
    "requests",
    "numpy",
    "pandas",
)


# -- ranking -------------------------------------------------------------------


def test_the_query_itself_is_always_first() -> None:
    """The one name the user typed is the one they meant - popular or not."""
    assert rank("textual", CATALOGUE) == ["textual"]


def test_prefixes_beat_substrings() -> None:
    assert rank("flask", CATALOGUE)[:3] == ["flask", "flask-login", "flask-wtf"]


def test_an_empty_query_ranks_nothing() -> None:
    assert rank("", CATALOGUE) == []


def test_ranking_ignores_case_and_separator_spelling() -> None:
    assert rank("Flask_WTF", CATALOGUE) == ["flask-wtf"]


def test_a_substring_match_is_found_after_prefixes() -> None:
    ranked = rank("quests", CATALOGUE)

    assert ranked[-1] == "requests"


# -- resolution -----------------------------------------------------------------


class RecordingIndex:
    """Answers from a dict, so a search's lookups can be asserted."""

    def __init__(self, answers: dict[str, tuple[str, str]]) -> None:
        self._answers = answers
        self.asked: list[str] = []

    def info(self, package: str) -> object:
        self.asked.append(package)
        latest, summary = self._answers.get(package, ("", ""))

        class _Info:
            error = "" if package in self._answers else "not found"

        return type(
            "Info", (), {"latest_version": latest, "summary": summary, "error": _Info.error}
        )()


def test_search_resolves_the_ranked_names_in_order() -> None:
    index = RecordingIndex(
        {
            "flask": ("3.1.3", "micro web framework"),
            "flask-login": ("0.6.3", "user session management"),
        }
    )
    service = SearchService(catalogue=Catalog(names=CATALOGUE), client=index)

    results = service.search("flask")

    assert index.asked[:2] == ["flask", "flask-login"]
    assert [result.name for result in results[:2]] == ["flask", "flask-login"]
    assert results[0].latest_version == "3.1.3"
    assert results[0].summary == "micro web framework"


def test_an_unresolvable_name_degrades_to_a_row_with_an_error() -> None:
    """The row is shown with what ranking knew - the name - plus the reason."""
    index = RecordingIndex({})
    service = SearchService(catalogue=Catalog(names=CATALOGUE), client=index)

    results = service.search("flask")

    assert results[0].error == "not found"
    assert results[0].name == "flask"


def test_a_search_that_raises_per_row_keeps_the_rest() -> None:
    class Broken:
        def info(self, package: str) -> object:
            if package == "flask":
                raise RuntimeError("index unreachable")

            class _Ok:
                latest_version = "1.0"
                summary = ""
                error = ""

            return _Ok()

    service = SearchService(catalogue=Catalog(names=CATALOGUE), client=Broken())
    collected: list[SearchResult] = []

    results = service.search("flask", on_result=collected.append)

    assert results[0].error  # the broken row, degraded
    assert any(result.name == "flask-login" for result in results)


# -- purpose inference -----------------------------------------------------------


def test_a_data_science_environment_says_so() -> None:
    assert infer_purpose(["numpy", "pandas", "scipy", "matplotlib", "left-pad"]) == "data-science"


def test_a_web_environment_says_so() -> None:
    assert infer_purpose(["flask", "sqlalchemy", "gunicorn"]) == "web-api"


def test_an_ml_environment_wins_over_data_science() -> None:
    """torch signals ML even when pandas is along for the ride."""
    assert infer_purpose(["torch", "transformers", "pandas"]) == "machine-learning"


def test_a_gui_environment_says_so() -> None:
    assert infer_purpose(["PySide6", "pyqt6"]) == "gui-desktop"


def test_an_unrecognisable_environment_is_general() -> None:
    assert infer_purpose(["left-pad", "some-internal-tool"]) == "general"


def test_an_empty_environment_is_general() -> None:
    assert infer_purpose([]) == "general"


def test_names_are_normalized_before_matching() -> None:
    """PySide6 with capitals and scikit_learn with underscores both count."""
    assert infer_purpose(["PySide6", "scikit_learn"]) == "gui-desktop"
