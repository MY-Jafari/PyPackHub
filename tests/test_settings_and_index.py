"""The settings validator's hardening, and the PyPI client's index wiring.

Both were review findings: ``trusted_hosts`` was persisted with no validation
at all - the one knob that weakens pip's TLS, kept raw - and the window
geometry blobs were read from the INI file without any size limit. The
client-side counterparts: the configured index URL is what lookups ask, the
cache is bounded and does not store failures, and the version list is capped.
"""

from __future__ import annotations

from pypackhub.core.pypi import PyPICache, PyPIClient, _json_base_for
from pypackhub.core.settings import AppSettings, validate_settings

# -- settings validation ---------------------------------------------------------


def test_trusted_hosts_are_shape_checked_and_lowercased() -> None:
    """A trusted host weakens TLS for exactly that host: it is a hostname or
    a host:port, and nothing else - a value with a scheme, a path or shell
    characters is dropped rather than stored for a future command line."""
    settings = validate_settings(
        AppSettings(
            trusted_hosts=(
                "Mirror.Example.COM:8080",
                "not a host",
                "https://mirror.example",
                "mirror.example/path",
                "ok-host",
                "",
            )
        )
    )

    assert settings.trusted_hosts == ("mirror.example.com:8080", "ok-host")


def test_an_oversized_window_blob_is_dropped_not_loaded() -> None:
    """A geometry blob is a few hundred bytes; anything huge is not one.

    A hand-edited settings file could stuff megabytes into these fields; the
    validator drops them rather than handing the whole thing to base64."""
    junk = "A" * 20_000

    settings = validate_settings(AppSettings(main_window_geometry=junk, main_window_state=junk))

    assert settings.main_window_geometry == ""
    assert settings.main_window_state == ""


# -- the PyPI client ------------------------------------------------------------


def test_the_json_endpoint_follows_the_configured_index() -> None:
    """A mirror configured as its simple root serves JSON next to it.

    The whole point of the setting: lookups must ask the index the user
    chose, or the app silently answers from the public one."""
    assert _json_base_for(None) == "https://pypi.org/pypi"
    assert _json_base_for("https://mirror.example/simple") == "https://mirror.example/pypi"
    assert _json_base_for("https://mirror.example/simple/") == ("https://mirror.example/pypi")
    assert _json_base_for("https://mirror.example/pypi") == "https://mirror.example/pypi"


def test_the_cache_is_bounded_and_evicts_the_least_recently_used() -> None:
    from time import monotonic

    from pypackhub.core.pypi import PackageInfo

    cache = PyPICache(ttl=300.0, max_entries=8)

    def entry(name: str) -> PackageInfo:
        return PackageInfo(
            name=name, canonical_name=name, latest_version="1.0", last_checked=monotonic()
        )

    for index in range(8):
        cache.set(entry(f"pkg-{index}"))
    # Touch the oldest so the second-oldest becomes the eviction victim.
    assert cache.get("pkg-0") is not None
    cache.set(entry("pkg-new"))

    assert cache.get("pkg-1") is None  # evicted: least recently used
    assert cache.get("pkg-0") is not None  # survived: it was used
    assert cache.get("pkg-new") is not None
    assert cache.get("pkg-7") is not None


def test_a_failed_parse_is_not_cached() -> None:
    """A response that parses into an error is not an answer.

    Caching it would serve the same failure for the whole TTL instead of
    retrying, which is how a transient malformed response turned into three
    minutes of "no latest version" for one package."""

    class _Http:
        calls = 0

        def get_json(self, url, *, max_bytes):
            type(self).calls += 1
            return {"unexpected": "shape"}

    http = _Http()
    client = PyPIClient(http=http)

    first = client.info("requests")
    assert first.error

    second = client.info("requests")
    assert second.error
    # Not cached: the malformed answer was asked for again, not replayed.
    assert _Http.calls == 2


def test_the_version_list_is_capped_by_the_setting() -> None:
    class _Http:
        def get_json(self, url, *, max_bytes):
            return {
                "info": {"name": "pkg", "version": "1.0"},
                "releases": {
                    f"1.{minor}.0": [{"filename": "pkg.whl", "yanked": False}]
                    for minor in range(50)
                },
            }

    client = PyPIClient(http=_Http(), max_versions=12)
    meta = client.meta("pkg")

    assert len(meta.versions) == 12
