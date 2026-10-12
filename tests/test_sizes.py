"""Package sizes: measured from the distribution's own record, never guessed.

The bug these tests exist for: sizes used to be measured by walking the location a
backend reported for a package, and pip and uv report the *environment* there. A
200-package environment therefore showed the same number on every row - the size of
the whole environment - and measured it 200 times. What is checked here is the fix
and the edges around it: the numbers come from the distribution's ``RECORD``, a
record cannot talk the reader out of its own directory, and a distribution with no
record is reported as unmeasured instead of as empty.
"""

from __future__ import annotations

from pathlib import Path

from pypackhub.core.inventory import Inventory, InventoryService, PackageRow
from pypackhub.core.sizes import distribution_size, package_sizes

RECORD_HEADER = "path,hash,size"


def _install(site_packages: Path, name: str, version: str, *, files: dict[str, int]) -> Path:
    """Write a distribution with the given files and a record that lists them.

    The record lists itself, exactly as pip and uv write it, so the numbers a test
    expects include the distribution's own metadata.
    """
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
    return directory


def _expected(directory: Path, *declared: int) -> int:
    """What the record describes: the declared files plus the record itself."""

    return sum(declared) + (directory / "RECORD").stat().st_size


def test_the_size_is_the_size_of_that_distribution(tmp_path: Path) -> None:
    site = tmp_path / "site-packages"
    small = _install(site, "small", "1.0", files={"small/__init__.py": 100})
    large = _install(
        site,
        "large",
        "2.0",
        files={"large/__init__.py": 300, "large/data.bin": 700},
    )

    assert distribution_size(small).size_bytes == _expected(small, 100)
    assert distribution_size(large).size_bytes == _expected(large, 300, 700)
    # The two files the record names, plus the record itself.
    assert distribution_size(large).files_count == 3


def test_two_packages_in_one_environment_do_not_report_the_same_size(tmp_path: Path) -> None:
    """The regression: every row used to show the environment's own size."""

    site = tmp_path / "site-packages"
    first = _install(site, "first", "1.0", files={"first/__init__.py": 10})
    second = _install(
        site,
        "second",
        "1.0",
        files={"second/__init__.py": 20, "second/extra.py": 30},
    )

    sizes = package_sizes([("first", first), ("second", second)])

    assert sizes["first"].size_bytes == _expected(first, 10)
    assert sizes["second"].size_bytes == _expected(second, 20, 30)
    assert sizes["first"].size_bytes != sizes["second"].size_bytes


def test_a_directory_that_is_not_a_distribution_measures_nothing(tmp_path: Path) -> None:
    empty = tmp_path / "not-installed"
    empty.mkdir()

    assert distribution_size(empty) is None


def test_a_missing_distribution_is_absent_rather_than_zero(tmp_path: Path) -> None:
    """A size of zero would be a claim; the table shows "unknown" instead."""

    sizes = package_sizes([("ghost", tmp_path / "nowhere.dist-info")])

    assert sizes == {}


def test_the_declared_size_is_believed_rather_than_re_measured(tmp_path: Path) -> None:
    """The record is the answer; a stat call per installed file is not.

    The file on disk is five bytes and the record claims 4096. Believing the record
    is what makes a 200-package environment one small read each instead of 20,000
    stat calls.
    """

    site = tmp_path / "site-packages"
    directory = _install(site, "box", "1.0", files={"box/__init__.py": 5})
    (directory / "RECORD").write_text(
        RECORD_HEADER + "\nbox/__init__.py,sha256=0,4096\n",
        encoding="utf-8",
    )

    # The rewritten record does not list itself, so the declared files are the
    # whole answer.
    assert distribution_size(directory).size_bytes == 4096


def test_a_record_with_no_size_column_falls_back_to_the_file(tmp_path: Path) -> None:
    """Not every installer writes sizes; the file itself is the second answer."""

    site = tmp_path / "site-packages"
    directory = _install(site, "box", "1.0", files={"box/__init__.py": 7})
    (directory / "RECORD").write_text(
        RECORD_HEADER + "\nbox/__init__.py,sha256=0,\n",
        encoding="utf-8",
    )

    assert distribution_size(directory).size_bytes == 7


def test_a_record_cannot_reach_outside_its_own_site_packages(tmp_path: Path) -> None:
    """A record is untrusted input: it comes from a wheel somebody else published."""

    site = tmp_path / "site-packages"
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"s" * 4096)

    directory = _install(site, "tricky", "1.0", files={"tricky/__init__.py": 10})
    (directory / "RECORD").write_text(
        RECORD_HEADER + "\n"
        "tricky/__init__.py,sha256=0,10\n"
        "../secret.txt,sha256=0,4096\n"
        "/etc/passwd,sha256=0,4096\n"
        "C:/Windows/notepad.exe,sha256=0,4096\n",
        encoding="utf-8",
    )

    measured = distribution_size(directory)

    assert measured.size_bytes == 10
    assert measured.files_count == 1
    assert all("secret" not in name for name, _ in measured.top_paths)


def test_a_distribution_without_a_record_is_measured_best_effort(tmp_path: Path) -> None:
    """No record, but the distribution's own directory is right there."""

    site = tmp_path / "site-packages"
    directory = site / "loose-1.0.dist-info"
    directory.mkdir(parents=True)
    module = site / "loose"
    module.mkdir()
    (module / "__init__.py").write_bytes(b"y" * 123)
    (site / "unrelated-package").mkdir()

    measured = distribution_size(directory, name="loose")

    assert measured is not None
    assert measured.exact is False
    assert measured.size_bytes == 123
    assert measured.top_paths == (("loose", 123),)


def test_a_malformed_record_still_describes_what_it_managed_to(tmp_path: Path) -> None:
    """A half-written record is a package manager that died mid-install, not a crash."""

    site = tmp_path / "site-packages"
    directory = _install(site, "torn", "1.0", files={"torn/__init__.py": 40})
    (directory / "RECORD").write_text(
        RECORD_HEADER + '\ntorn/__init__.py,sha256=0,40\n"unterminated\n',
        encoding="utf-8",
    )

    assert distribution_size(directory).size_bytes == 40


def test_the_service_measures_each_installed_distribution_separately(tmp_path: Path) -> None:
    """The wiring, end to end: index -> each distribution's own record.

    This is the shape the packages view sees: one size per installed package, and
    a package whose distribution directory cannot be found stays unmeasured rather
    than being reported as empty.
    """

    site = tmp_path / "lib" / "python3.13" / "site-packages"
    first = _install(site, "first", "1.0", files={"first/__init__.py": 10})
    second = _install(site, "second", "1.0", files={"second/__init__.py": 2048})

    inventory = Inventory(
        environment=tmp_path,
        backend="pip",
        rows=(
            PackageRow(name="first", installed="1.0", location=tmp_path),
            PackageRow(name="second", installed="1.0", location=tmp_path),
            PackageRow(name="ghost", installed="1.0", location=tmp_path),
        ),
    )

    measured = InventoryService(prefix=tmp_path).enrich_sizes(inventory)
    sizes = {row.name: row.size_bytes for row in measured.rows}

    assert sizes["first"] == _expected(first, 10)
    assert sizes["second"] == _expected(second, 2048)
    assert sizes["ghost"] is None


def test_package_sizes_keys_by_the_name_it_was_given(tmp_path: Path) -> None:
    site = tmp_path / "site-packages"
    directory = _install(site, "My_Package", "1.0", files={"my_package/__init__.py": 64})

    sizes = package_sizes([("My-Package", directory)])

    assert sizes["My-Package"].size_bytes == _expected(directory, 64)
    assert sizes["My-Package"].location == directory
    assert sizes["My-Package"].exact is True
