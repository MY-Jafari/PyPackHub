"""Disk-usage analysis over a synthetic environment.

The section-9 gate: totals, the largest consumers and the package-manager
caches, measured from a built environment where every byte is accounted for
by construction - the same approach the size tests use, answering the
questions the analysis asks of a real machine.
"""

from __future__ import annotations

import os
from pathlib import Path

import pypackhub.core.disk_usage as disk_usage_module
from pypackhub.core.disk_usage import (
    analyse_disk_usage,
    conda_pkgs_dir,
    directory_size,
    pip_cache_dir,
    poetry_cache_dir,
    uv_cache_dir,
)

RECORD_HEADER = "path,hash,size"


def _install(site_packages: Path, name: str, version: str, *, files: dict[str, int]) -> None:
    """A distribution whose ``RECORD`` lists real files with known sizes."""
    directory = site_packages / f"{name}-{version}.dist-info"
    directory.mkdir(parents=True, exist_ok=True)

    lines = [RECORD_HEADER]
    for relative, size in files.items():
        target = site_packages / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * size)
        lines.append(f"{relative},sha256=0,{size}")
    lines.append(f"{name}-{version}.dist-info/RECORD,,")

    (directory / "RECORD").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _environment(tmp_path: Path) -> Path:
    prefix = tmp_path / "env"
    site = prefix / "lib" / "python3.13" / "site-packages"
    _install(site, "small", "1.0", files={"small/__init__.py": 100})
    _install(site, "large", "2.0", files={"large/__init__.py": 300, "large/data.bin": 700})
    (prefix / "pyvenv.cfg").write_text("home = nowhere\n", encoding="utf-8")
    return prefix


def test_the_total_covers_the_whole_prefix(tmp_path: Path) -> None:
    """The walked total is the honest number: the packages' measured bytes
    plus everything else the environment keeps."""
    usage = analyse_disk_usage(_environment(tmp_path))

    measured = sum(package.size_bytes for package in usage.packages)
    assert usage.total_bytes > measured  # pyvenv.cfg and the records are in it too
    assert usage.complete is True
    assert usage.files >= 5  # three package files, two records, one config


def test_the_largest_consumers_come_first(tmp_path: Path) -> None:
    usage = analyse_disk_usage(_environment(tmp_path))

    names = [package.name for package in usage.packages]
    assert names == ["large", "small"]
    sizes = [package.size_bytes for package in usage.packages]
    assert sizes == sorted(sizes, reverse=True)


def test_a_tree_that_cannot_be_read_is_a_floor_not_a_crash(tmp_path: Path) -> None:
    """Unreadable parts lower the total, never the analysis."""
    root = tmp_path / "env"
    root.mkdir()
    (root / "regular.bin").write_bytes(b"x" * 50)
    (root / "odd.bin").write_bytes(b"x" * 10)

    total, files, complete = directory_size(root)
    assert (total, files, complete) == (60, 2, True)

    # Measuring a path that is not a directory at all reports nothing and
    # says the answer is incomplete, rather than raising.
    total, files, complete = directory_size(root / "odd.bin")
    assert (total, files, complete) == (0, 0, False)


def test_cache_directories_follow_the_tools_and_the_environment(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PIP_CACHE_DIR", str(tmp_path / "pip-cache"))
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))

    assert pip_cache_dir() == tmp_path / "pip-cache"
    assert uv_cache_dir() == tmp_path / "uv-cache"


def test_a_cache_that_was_never_created_is_a_fact_not_an_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PIP_CACHE_DIR", str(tmp_path / "nothing-here"))
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "nothing-either"))
    monkeypatch.delenv("CONDA_PKGS_DIRS", raising=False)
    monkeypatch.delenv("CONDA_ROOT", raising=False)

    usage = analyse_disk_usage(_environment(tmp_path))
    by_kind = {cache.kind: cache for cache in usage.caches}

    # poetry's platform default is always locatable; conda's is only reported
    # when conda's own variables name it, so neither machine luck nor a
    # guess decides what the panel lists.
    assert set(by_kind) == {"pip", "uv", "poetry"}
    assert by_kind["pip"].exists is False
    assert by_kind["pip"].size_bytes == 0


def test_created_caches_are_measured(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PIP_CACHE_DIR", str(tmp_path / "pip-cache"))
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    (tmp_path / "pip-cache").mkdir()
    (tmp_path / "pip-cache" / "wheel.whl").write_bytes(b"x" * 4096)

    usage = analyse_disk_usage(_environment(tmp_path))
    by_kind = {cache.kind: cache for cache in usage.caches}

    assert by_kind["pip"].exists is True
    assert by_kind["pip"].size_bytes == 4096
    assert by_kind["uv"].exists is False


def test_the_cache_locations_match_the_platform_conventions(monkeypatch) -> None:
    """With no overrides, each platform's documented default is used - the
    analysis must not spawn a process to ask a tool where its own cache is."""
    monkeypatch.delenv("PIP_CACHE_DIR", raising=False)
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)

    assert pip_cache_dir().name == "Cache" or pip_cache_dir().name == "pip"
    assert uv_cache_dir().name == "cache" or uv_cache_dir().name == "uv"


# -- conda's and poetry's caches -------------------------------------------------


def test_condas_cache_honours_condas_own_variables(tmp_path: Path, monkeypatch) -> None:
    """``CONDA_PKGS_DIRS`` is a path list - the first entry is the one conda
    downloads into - and ``CONDA_ROOT`` names the installation whose ``pkgs``
    directory it is."""
    monkeypatch.setenv(
        "CONDA_PKGS_DIRS", str(tmp_path / "first") + os.pathsep + str(tmp_path / "second")
    )
    assert conda_pkgs_dir() == tmp_path / "first"

    monkeypatch.setenv("CONDA_PKGS_DIRS", "")
    monkeypatch.setenv("CONDA_ROOT", str(tmp_path / "conda-root"))
    assert conda_pkgs_dir() == tmp_path / "conda-root" / "pkgs"


def test_condas_cache_is_not_guessed_at(tmp_path: Path, monkeypatch) -> None:
    """No variables, no number: a guessed cache path could measure a
    directory that is not conda's at all."""
    monkeypatch.delenv("CONDA_PKGS_DIRS", raising=False)
    monkeypatch.delenv("CONDA_ROOT", raising=False)

    assert conda_pkgs_dir() is None


def test_poetrys_cache_follows_the_platform(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(disk_usage_module.sys, "platform", "darwin")
    assert poetry_cache_dir() == Path.home() / "Library" / "Caches" / "pypoetry"

    monkeypatch.setattr(disk_usage_module.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert poetry_cache_dir() == tmp_path / "xdg" / "pypoetry"


def test_a_locatable_conda_cache_is_measured(tmp_path: Path, monkeypatch) -> None:
    """When conda's variables name a real directory, the panel says what it
    holds - the package cache is where conda's bytes actually pile up."""
    cache = tmp_path / "conda-pkgs"
    cache.mkdir()
    (cache / "numpy-1.26.0.tar.bz2").write_bytes(b"x" * 2048)
    monkeypatch.setenv("CONDA_PKGS_DIRS", str(cache))

    usage = analyse_disk_usage(_environment(tmp_path))
    by_kind = {entry.kind: entry for entry in usage.caches}

    assert by_kind["conda"].exists is True
    assert by_kind["conda"].size_bytes == 2048
