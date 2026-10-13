"""Package search: what to look up, in what order, with what shown.

PyPI has no search API - the old XML-RPC one is gone - so a desktop
application cannot ask "find me packages like X". What it *can* do, and what
this module does, is decide which names are worth a lookup:

* a bundled catalogue of popular package names (``assets/popular_packages.txt``)
  is ranked against the query - exact, prefix, substring - and the top
  matches are resolved against the PyPI JSON API through the existing
  hardened client;
* the query itself is always looked up as an exact name, because an exact
  name must work whether or not it is popular. That is the "search for
  ``textual``" case, and it is also how an uncommon package is found at all.

Ranking is local and instant; resolution is network. The two are separate so
the page can say "searching" over a list it already has, and so the ranking
is testable without a network.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from pypackhub.core.pypi import PyPIClient

__all__ = [
    "Catalog",
    "SearchResult",
    "SearchService",
    "rank",
]

#: How many resolved results one search shows.
RESULT_LIMIT: Final = 20

#: How many name lookups run at once. Each is one small HTTPS request.
LOOKUP_WORKERS: Final = 8


def _normalize(text: str) -> str:
    """PEP 503 normalisation, so ``BeautifulSoup`` finds ``beautifulsoup4``."""
    return text.strip().lower().replace("_", "-").replace(".", "-")


class Catalog:
    """The bundled popular-names catalogue, loaded once and kept in memory."""

    def __init__(
        self,
        *,
        source: Path | None = None,
        names: Sequence[str] | None = None,
    ) -> None:
        self._names: tuple[str, ...] = ()
        if names is not None:
            # An explicit catalogue, already curated; normalised on the way in.
            seen: set[str] = set()
            entries: list[str] = []
            for entry in names:
                name = _normalize(entry)
                if not name or name in seen:
                    continue
                seen.add(name)
                entries.append(name)
            self._names = tuple(entries)
        else:
            self._load(source or _default_catalog())

    def _load(self, source: Path) -> None:
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except OSError:
            # A missing catalogue means local ranking has nothing to offer;
            # the exact-name lookup still works, so this is a degradation,
            # never a crash.
            lines = []

        seen: set[str] = set()
        names: list[str] = []
        for line in lines:
            name = _normalize(line)
            if not name or name.startswith("#") or name in seen:
                continue
            seen.add(name)
            names.append(name)
        self._names = tuple(names)

    @property
    def names(self) -> tuple[str, ...]:
        return self._names

    def count(self, name: str) -> int:
        """How many catalogue entries *name* matches, exactly or as a prefix."""
        return sum(1 for entry in self._names if entry == name or entry.startswith(name))


def _default_catalog() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "popular_packages.txt"


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One resolvable package, as the search page shows it."""

    name: str
    latest_version: str = ""
    summary: str = ""
    #: True when the lookup could not complete: the row is shown with what the
    # ranking knew (the name) and the error, rather than being dropped.
    error: str = ""


def rank(query: str, catalogue: Sequence[str], *, limit: int = RESULT_LIMIT) -> list[str]:
    """The names worth a lookup for *query*, best first.

    Exact matches beat prefixes, prefixes beat substrings; inside each group
    the catalogue order wins (a curated popularity order). The query itself,
    normalised, is always first when it is not empty - it is the one name the
    user explicitly asked about, and the only way an unpopular package is
    ever found.
    """
    needle = _normalize(query)
    if not needle:
        return []

    prefix: list[str] = []
    substring: list[str] = []

    for name in catalogue:
        if name == needle:
            continue  # the needle is added unconditionally below
        if name.startswith(needle):
            prefix.append(name)
        elif needle in name:
            substring.append(name)

    ranked = [needle, *prefix, *substring]
    return ranked[:limit]


class IndexLookup(Protocol):
    """The part of the PyPI client the search uses, so tests can fake it."""

    def info(self, package: str) -> object:
        """Return an object with ``latest_version``, ``summary`` and ``error``."""


class SearchService:
    """Rank a query locally, then resolve the best names against the index."""

    def __init__(
        self,
        *,
        catalogue: Catalog | None = None,
        client: IndexLookup | None = None,
    ) -> None:
        self._catalogue = catalogue or Catalog()
        self._client = client

    @property
    def client(self) -> IndexLookup:
        if self._client is None:
            self._client = PyPIClient()
        return self._client

    def search(
        self,
        query: str,
        *,
        cancel: threading.Event | None = None,
        on_result: Callable[[SearchResult], None] | None = None,
    ) -> list[SearchResult]:
        """Resolve the best names for *query*, several lookups at a time.

        Results keep the ranking order. ``on_result`` is called as each
        answer arrives, from a worker thread, so a page can fill its list in
        progressively; an unreachable index degrades the row to the name plus
        an error rather than failing the search.
        """
        names = rank(query, self._catalogue.names)
        if not names:
            return []

        client = self.client
        outcomes: dict[str, SearchResult] = {}

        def lookup(name: str) -> SearchResult:
            try:
                info = client.info(name)
            except Exception as error:
                return SearchResult(name=name, error=str(error))
            return SearchResult(
                name=name,
                latest_version=str(getattr(info, "latest_version", "") or ""),
                summary=str(getattr(info, "summary", "") or ""),
                error=str(getattr(info, "error", "") or ""),
            )

        queue = iter(names)
        workers = min(LOOKUP_WORKERS, len(names))

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pypackhub-search") as pool:
            pending: set[Future[SearchResult]] = set()
            while True:
                while len(pending) < workers and not _is_cancelled(cancel):
                    try:
                        pending.add(pool.submit(lookup, next(queue)))
                    except StopIteration:
                        break
                if not pending:
                    break
                done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
                for future in done:
                    pending.discard(future)
                    # Defensive only: ``lookup`` already degrades its own
                    # failures, so an exception here is the pool's, not a row's.
                    try:
                        result = future.result()
                    except Exception:  # noqa: S112
                        continue
                    outcomes[result.name] = result
                    if on_result is not None:
                        on_result(result)

        return [outcomes[name] for name in names if name in outcomes]


def _is_cancelled(cancel: threading.Event | None) -> bool:
    return cancel is not None and cancel.is_set()
