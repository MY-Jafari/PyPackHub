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

from pathlib import Path
from typing import Protocol

from pypackhub.core.environments import DEFAULT_SEARCH_ROOTS, detected_environments
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

    def __init__(self, *, prefer_uv: bool = True, search_working_directory: bool = True) -> None:
        self._prefer_uv = prefer_uv
        self._search_working_directory = search_working_directory

    def create(self, prefix: Path) -> InventoryService:
        return InventoryService(prefix=prefix, prefer_uv=self._prefer_uv)

    def environments(self) -> tuple[Path, ...]:
        """Environments to offer: the built-in roots, plus this project's ``.venv``.

        The discovery is intentionally shallow (it looks at the immediate children
        of a few well-known directories) because a full scan is Phase 3 work. The
        working directory is added because a developer's ``.venv`` next to the
        project they opened is the environment they almost always mean.
        """
        roots: list[Path] = list(DEFAULT_SEARCH_ROOTS)
        if self._search_working_directory:
            roots.append(Path.cwd())

        listing = detected_environments(tuple(roots))

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
    ) -> None:
        self.provider: InventoryProvider = provider or DefaultInventoryProvider()
        self.tasks: TaskManager = tasks or TaskManager()
        self._default = (
            Path(default_environment).resolve()
            if default_environment is not None
            else current_prefix()
        )
        self._services: dict[Path, InventoryService] = {}

    @property
    def default_environment(self) -> Path:
        return self._default

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
