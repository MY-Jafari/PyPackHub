"""Where installed-distribution metadata is looked for.

A base interpreter keeps packages in two places: the ``site-packages`` inside its
own prefix, and the *user site* directory that ``pip install --user`` writes to.
On an all-users Windows install the second one holds almost everything, because the
interpreter refuses to write into the first - and a reader that only looked inside
the prefix showed hundreds of installed packages with no metadata, no size and no
dependency edges at all.

A virtual environment is the opposite case: pip inside one ignores the user site by
design, so including it would attribute another environment's packages to this one.
"""

from __future__ import annotations

from pathlib import Path

from pypackhub.core import metadata


def _base_interpreter(prefix: Path) -> Path:
    """A prefix that looks like a real CPython installation, not a bare tree."""
    (prefix / "lib" / "python3.13" / "site-packages").mkdir(parents=True, exist_ok=True)
    (prefix / "python.exe").write_text("", encoding="utf-8")
    return prefix


def test_a_base_interpreter_reads_its_user_site(tmp_path: Path, monkeypatch) -> None:
    """Windows sends ``--user`` installs to ``%APPDATA%\\Python\\PythonXY``."""

    prefix = _base_interpreter(tmp_path / "python313")
    appdata = tmp_path / "AppData"
    user_site = appdata / "Python" / "Python313" / "site-packages"
    user_site.mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(appdata))

    directories = metadata.distribution_directories(prefix)

    assert prefix / "lib" / "python3.13" / "site-packages" in directories
    assert user_site in directories


def test_pythonuserbase_moves_both_user_site_layouts(tmp_path: Path, monkeypatch) -> None:
    prefix = _base_interpreter(tmp_path / "python313")
    base = tmp_path / "userbase"
    windows_layout = base / "Python313" / "site-packages"
    posix_layout = base / "lib" / "python3.13" / "site-packages"
    windows_layout.mkdir(parents=True)
    posix_layout.mkdir(parents=True)
    monkeypatch.setenv("PYTHONUSERBASE", str(base))

    directories = metadata.distribution_directories(prefix)

    assert windows_layout in directories
    assert posix_layout in directories


def test_a_user_site_that_does_not_exist_is_not_invented(tmp_path: Path, monkeypatch) -> None:
    prefix = _base_interpreter(tmp_path / "python313")
    monkeypatch.setenv("PYTHONUSERBASE", str(tmp_path / "nowhere"))

    directories = metadata.distribution_directories(prefix)

    assert all("nowhere" not in str(directory) for directory in directories)


def test_a_virtual_environment_is_left_alone(tmp_path: Path, monkeypatch) -> None:
    """pip inside a venv ignores the user site, so its packages are not this env's."""

    prefix = _base_interpreter(tmp_path / "venv")
    (prefix / "pyvenv.cfg").write_text("home = /usr/bin\nversion = 3.13.0\n", encoding="utf-8")
    appdata = tmp_path / "AppData"
    (appdata / "Python" / "Python313" / "site-packages").mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(appdata))

    directories = metadata.distribution_directories(prefix)

    assert directories == (prefix / "lib" / "python3.13" / "site-packages",)


def test_a_tree_with_no_interpreter_answers_only_for_itself(tmp_path: Path, monkeypatch) -> None:
    """A test fixture, or an environment whose interpreter was removed.

    Nothing is added for it: the user site belongs to an interpreter, and there is
    none here to belong to. Without this rule, reading a fixture would quietly mix
    the developer's own installed packages into it.
    """

    prefix = tmp_path / "unpacked"
    (prefix / "lib" / "python3.13" / "site-packages").mkdir(parents=True)
    appdata = tmp_path / "AppData"
    (appdata / "Python" / "Python313" / "site-packages").mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(appdata))

    directories = metadata.distribution_directories(prefix)

    assert directories == (prefix / "lib" / "python3.13" / "site-packages",)


def test_the_scan_finds_distributions_in_the_user_site(tmp_path: Path, monkeypatch) -> None:
    """The whole point: metadata, sizes and dependency edges for those packages."""

    prefix = _base_interpreter(tmp_path / "python313")
    appdata = tmp_path / "AppData"
    user_site = appdata / "Python" / "Python313" / "site-packages"
    directory = user_site / "user-installed-1.0.dist-info"
    directory.mkdir(parents=True)
    (directory / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: user-installed\nVersion: 1.0\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("APPDATA", str(appdata))

    index = metadata.scan(prefix)

    assert index.get("user-installed") is not None
    assert index.get("user-installed").version == "1.0"
