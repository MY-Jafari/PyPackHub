"""What the views are given instead of the core itself.

The views must not construct backends, spawn threads or talk to PyPI directly:
they ask this small context for a service and hand long work to the task manager
it owns. Two things follow from that, and both are worth the indirection:

* a test can build a window over a **fake** provider, so the UI test-suite never
  runs pip and never needs a network;
* the same context can back the CLI companion later, because nothing here is a
  Qt concept - the task manager is ``threading`` and the provider is core code.

``UiContext.service_for`` caches one service per environment, which is what makes
the expensive passes (the metadata index, the PyPI cache) survive moving between
views instead of being thrown away.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from pypackhub.core.environments import default_search_roots, detected_environments
from pypackhub.core.inventory import InventoryService
from pypackhub.core.platform_guard import current_prefix
from pypackhub.core.tasks import TaskManager

__all__ = [
    "DefaultInventoryProvider",
    "InventoryProvider",
    "UiContext",
]


class InventoryProvider(Protocol):
    """Creates the core service for an environment, and finds environments."""

    def create(self, prefix: Path) -> InventoryService:
        """Return the service for one environment."""

    def environments(self) -> tuple[Path, ...]:
        """Return the environments worth offering in the switcher."""


class DefaultInventoryProvider:
    """The real provider: core inventory plus environment discovery."""

    def __init__(
        self,
        *,
        prefer_uv: bool = True,
        search_working_directory: bool = True,
        package_manager: str = "auto",
        registered_environments: Sequence[Path] = (),
        index_url: str = "",
        extra_index_urls: Sequence[str] = (),
        trusted_hosts: Sequence[str] = (),
        cache_ttl: float = 300.0,
        cache_max_versions: int = 32,
    ) -> None:
        self._prefer_uv = prefer_uv
        self._search_working_directory = search_working_directory
        self._package_manager = package_manager
        self._registered: tuple[Path, ...] = tuple(registered_environments)
        self.set_index(
            index_url=index_url,
            extra_index_urls=extra_index_urls,
            trusted_hosts=trusted_hosts,
            cache_ttl=cache_ttl,
            cache_max_versions=cache_max_versions,
        )

    def set_package_manager(self, package_manager: str) -> None:
        """Record the backend the user forced for the environments.

        The rest of the provider's configuration is kept: switching the package
        manager must not quietly turn environment discovery off as a side effect.
        """
        self._package_manager = package_manager

    def set_registered_environments(self, registered: Sequence[Path]) -> None:
        """Record the folders the user pointed the application at.

        The services are not keyed by these folders, so unlike the package
        manager or the index this change costs nothing to apply; the next
        discovery pass simply offers the new folder as well.
        """
        self._registered = tuple(registered)

    def set_index(
        self,
        *,
        index_url: str,
        extra_index_urls: Sequence[str],
        trusted_hosts: Sequence[str],
        cache_ttl: float,
        cache_max_versions: int,
    ) -> None:
        """Record the package-index configuration the settings describe.

        Everything here was validated before it reached the provider. The
        flags are what every installing command carries; the client is what
        the version lookups ask - both point at the index the user chose,
        which is the entire point of the settings page.
        """
        from pypackhub.core.pypi import PyPIClient

        flags: list[str] = []
        if index_url:
            flags.extend(("--index-url", index_url))
        for url in extra_index_urls:
            flags.extend(("--extra-index-url", url))
        for host in trusted_hosts:
            flags.extend(("--trusted-host", host))

        self._index_args = tuple(flags)
        self._client = PyPIClient(
            index_url=index_url or None,
            ttl=cache_ttl,
            max_versions=cache_max_versions,
        )

    def create(self, prefix: Path) -> InventoryService:
        return InventoryService(
            prefix=prefix,
            prefer_uv=self._prefer_uv,
            backend_name=self._backend_name(prefix),
            pypi_client=self._client,
            index_args=self._index_args,
        )

    def _backend_name(self, prefix: Path) -> str:
        """The backend name to use for *prefix*, honouring the user's choice."""
        if self._package_manager and self._package_manager != "auto":
            return self._package_manager
        return ""

    def environments(self) -> tuple[Path, ...]:
        """Environments to offer: the built-in roots, plus this project's ``.venv``.

        The built-in roots are the platform-aware ones - the portable
        version-manager homes plus conda's ``envs`` directories - and the
        machine's standard installs are offered by the discovery itself,
        which is how a frozen build finds real Pythons whose parents it
        must not scan. The working directory is added because a developer's
        ``.venv`` next to the project they opened is the environment they
        almost always mean, and the registered folders are added because
        they are the one way to reach a conda environment outside the
        roots or a poetry/pipenv/pdm project.
        """
        roots: list[Path] = list(default_search_roots())
        if self._search_working_directory:
            roots.append(Path.cwd())

        listing = detected_environments(tuple(roots), registered=self._registered)

        prefixes: list[Path] = []
        seen: set[Path] = set()
        for environment in listing.environments:
            resolved = environment.prefix.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            prefixes.append(resolved)

        return tuple(prefixes)


class UiContext:
    """The services a view needs, and the threads long work runs on."""

    def __init__(
        self,
        *,
        provider: InventoryProvider | None = None,
        tasks: TaskManager | None = None,
        default_environment: Path | None = None,
        package_manager: str = "auto",
        registered_environments: Sequence[Path] = (),
    ) -> None:
        self.provider: InventoryProvider = provider or DefaultInventoryProvider(
            package_manager=package_manager,
            registered_environments=registered_environments,
        )
        self.tasks: TaskManager = tasks or TaskManager()
        self._default = (
            Path(default_environment).resolve()
            if default_environment is not None
            else current_prefix()
        )
        self._services: dict[Path, InventoryService] = {}
        self._package_manager = package_manager

    @property
    def default_environment(self) -> Path:
        return self._default

    @property
    def package_manager(self) -> str:
        return self._package_manager

    def set_package_manager(self, package_manager: str) -> None:
        """Update the default backend choice and clear cached services.

        The provider is updated *in place* when it knows how, rather than being
        replaced: a test's fake provider must survive the change (and so must any
        provider configured with more than its default search settings), and only
        the services - which were built for the previous choice - are thrown away.
        """
        self._package_manager = package_manager
        update = getattr(self.provider, "set_package_manager", None)
        if callable(update):
            update(package_manager)
        self._services.clear()

    def set_index(
        self,
        *,
        index_url: str,
        extra_index_urls: tuple[str, ...] = (),
        trusted_hosts: tuple[str, ...] = (),
        cache_ttl: float = 300.0,
        cache_max_versions: int = 32,
    ) -> None:
        """Point the services at the index the settings describe.

        Same contract as :meth:`set_package_manager`: the provider is updated
        in place and the services built for the previous index are discarded,
        so a plan or a lookup never mixes one index's answers with another's.
        """
        update = getattr(self.provider, "set_index", None)
        if callable(update):
            update(
                index_url=index_url,
                extra_index_urls=extra_index_urls,
                trusted_hosts=trusted_hosts,
                cache_ttl=cache_ttl,
                cache_max_versions=cache_max_versions,
            )
        self._services.clear()

    def set_registered_environments(self, registered: Sequence[Path]) -> None:
        """Point the provider at the folders the user registered.

        Same contract as :meth:`set_package_manager`: the provider is updated
        in place and nothing is thrown away, because the services are not
        keyed by the registered folders - only the next discovery pass sees
        the change.
        """
        update = getattr(self.provider, "set_registered_environments", None)
        if callable(update):
            update(registered)

    def set_default_environment(self, prefix: Path) -> None:
        self._default = Path(prefix).resolve()

    def service_for(self, prefix: Path) -> InventoryService:
        """The cached service for an environment."""
        resolved = Path(prefix).resolve()
        service = self._services.get(resolved)
        if service is None:
            service = self.provider.create(resolved)
            self._services[resolved] = service
        return service

    def environments(self) -> tuple[Path, ...]:
        """Discovered environments, with the default one always present and first."""
        found = list(self.provider.environments())
        if self._default not in found:
            found.insert(0, self._default)
        return tuple(found)
