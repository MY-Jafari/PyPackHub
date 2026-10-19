"""Supply-chain signals: is this the name you meant, and is it what it looks like?

Two questions, both answered before anything is installed, both local or
one-lookup cheap, and both advisory - the user decides, the warnings just
make sure the decision is an informed one:

* **Typosquatting.** A name one edit away from a popular package is the
  oldest attack on a package index: ``reqeusts`` exists to be installed by
  mistake. The requested name is normalised the way PEP 503 normalises
  (which also covers the ``-``/``_``/``.`` tricks) and compared against the
  bundled popular-package catalogue with a distance that counts a swapped,
  missing or doubled letter as one edit - the three shapes real typos take.
  A match is reported as "did you mean X?", never as a refusal.

* **Trust signals.** The index knows things about a package that say how
  careful to be: how old its first release is (brand-new is the classic
  supply-chain shape), whether its latest release is years stale, whether
  it ships a wheel or is sdist-only (an sdist runs code at install time,
  a wheel does not), and whether it names a home page at all. The facts
  are fetched by :meth:`pypackhub.core.pypi.PyPIClient.release_facts` and
  evaluated here into flags a panel can render in either language.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Final

from pypackhub.core.pypi import ReleaseFacts
from pypackhub.core.search import Catalog

__all__ = [
    "ReleaseFacts",
    "TrustFlags",
    "TyposquatWarning",
    "bundled_catalog",
    "damerau_levenshtein",
    "evaluate_trust",
    "typosquat_check",
]

#: Popular names shorter than this are never the answer to a near-miss: one
#: edit away from a three-letter name is half the namespace.
_MIN_POPULAR_LENGTH: Final = 5

#: A package whose first release is younger than this is flagged: it has had
#: no time to build a reputation, which is exactly the shape of a fresh
#: typosquat or a throwaway attack package.
_BRAND_NEW_DAYS: Final = 14

#: A package whose latest release is older than this is flagged as dormant:
#: nothing wrong in itself, but worth knowing before depending on it.
_ABANDONED_DAYS: Final = 730

#: ASCII confusables folded away before any distance is measured, so
#: ``lnstall`` reads as ``install`` and ``b0to`` as ``boto``. Applied to both
#: sides, because the trick in the wild is in the *requested* name.
_HOMOGLYPHS: Final = {
    "0": "o",
    "1": "l",
    "3": "e",
    "5": "s",
    "rn": "m",
    "vv": "w",
}

_BUNDLED: Final = Path(__file__).resolve().parents[1] / "assets" / "popular_packages.txt"

_bundled_catalog: Catalog | None = None


def bundled_catalog() -> Catalog:
    """The popular-names catalogue, loaded once per process.

    The same bundled file the search page ranks against; loading it once here
    means a typosquat check costs a scan over ~320 normalised strings - no
    subprocess, no network, instant enough for the confirm path.
    """
    global _bundled_catalog
    if _bundled_catalog is None:
        _bundled_catalog = Catalog(source=_BUNDLED)
    return _bundled_catalog


def _normalize(text: str) -> str:
    """PEP 503 normalisation, so the comparison ignores ``-``/``_``/``.``."""
    return text.strip().lower().replace("_", "-").replace(".", "-")


def _fold(name: str) -> str:
    """Fold ASCII lookalikes, so a distance of one is measured honestly."""
    folded = name
    for lookalike, real in _HOMOGLYPHS.items():
        folded = folded.replace(lookalike, real)
    return folded


def damerau_levenshtein(left: str, right: str) -> int:
    """Edit distance where a swapped pair counts as one edit.

    A plain Levenshtein would count ``reqeusts`` as two edits away from
    ``requests`` (substitute, substitute) and miss the most common typo of
    all. This is the optimal-string-alignment variant, which is enough for
    package names and does not pretend to handle the repeated-edit corner
    cases nobody types.
    """
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    columns = len(right)
    # Three rolling rows of the full matrix: the row two above (for
    # transpositions), the row above, and the row being filled.
    two_above = list(range(columns + 1))
    above = list(range(columns + 1))

    for row in range(1, len(left) + 1):
        current = [row] + [0] * columns
        for column in range(1, columns + 1):
            cost = 0 if left[row - 1] == right[column - 1] else 1
            best = min(
                above[column] + 1,  # deletion
                current[column - 1] + 1,  # insertion
                above[column - 1] + cost,  # substitution
            )
            if (
                row > 1
                and column > 1
                and left[row - 1] == right[column - 2]
                and left[row - 2] == right[column - 1]
            ):
                # A transposition: two characters swapped in place.
                best = min(best, two_above[column - 2] + 1)
            current[column] = best
        two_above, above = above, current

    return above[columns]


@dataclass(frozen=True, slots=True)
class TyposquatWarning:
    """One requested name that is probably a mistyping of a popular one."""

    #: The name as it was asked for, not normalised - the user should
    #: recognise what they typed.
    name: str
    #: The popular package the name is one edit away from.
    suspect_of: str
    #: The distance measured (always 1 today; kept so the panel can say it).
    distance: int = 1


def typosquat_check(name: str, catalogue: Sequence[str]) -> TyposquatWarning | None:
    """The near-miss warning for *name*, or ``None`` when it looks fine.

    Only one edit away counts, and only from popular names long enough that
    one edit is meaningful. An exact popular name is fine - that is the
    package itself, not a near-miss - and an unknown, far-from-anything name
    is someone's unglamorous but honest package.
    """
    normalized = _normalize(name)
    if not normalized or normalized in catalogue:
        return None

    folded = _fold(normalized)
    for entry in catalogue:
        if len(entry) < _MIN_POPULAR_LENGTH:
            continue
        distance = damerau_levenshtein(folded, _fold(entry))
        if distance <= 1:
            # Distance zero after folding is a lookalike-exact match
            # (``b0to`` for ``boto``) - the strongest near-miss there is.
            return TyposquatWarning(name=name, suspect_of=entry, distance=distance)

    return None


@dataclass(frozen=True, slots=True)
class TrustFlags:
    """The evaluated signals a trust panel shows, all advisory."""

    #: First release within the brand-new window.
    brand_new: bool = False
    #: Latest release older than the dormancy window.
    abandoned: bool = False
    #: The newest release has no wheel: code runs at install time.
    sdist_only: bool = False
    #: The package names no home page or repository at all.
    no_homepage: bool = False
    #: The facts could not be fetched: nothing is claimed either way.
    unknown: bool = False

    @property
    def is_clean(self) -> bool:
        """True when every check ran and found nothing worth saying."""
        return not any(
            (self.brand_new, self.abandoned, self.sdist_only, self.no_homepage, self.unknown)
        )


def _as_date(value: str) -> date | None:
    """An ISO date string back to a date, or ``None`` when unparsable."""
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def evaluate_trust(facts: ReleaseFacts, *, today: date | None = None) -> TrustFlags:
    """Turn release facts into the flags a person can act on.

    Unparsable or missing dates never raise and never guess: a fact that
    cannot be evaluated is simply not claimed.
    """
    if not facts.known:
        return TrustFlags(unknown=True)

    now = today or date.today()
    flags = {
        "brand_new": False,
        "abandoned": False,
        "sdist_only": not facts.latest_has_wheel and facts.releases > 0,
        "no_homepage": not facts.homepage.strip(),
    }

    first = _as_date(facts.first_release)
    if first is not None and (now - first).days < _BRAND_NEW_DAYS:
        flags["brand_new"] = True

    latest = _as_date(facts.latest_release)
    if latest is not None and (now - latest).days > _ABANDONED_DAYS:
        flags["abandoned"] = True

    return TrustFlags(**flags)
