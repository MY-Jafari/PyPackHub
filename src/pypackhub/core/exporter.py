"""Exporting the installed state as a requirements file.

The import side of requirements files already exists - ``parse_requirements``
in the security core classifies every line of an untrusted file and marks
what needs a human decision. This module is the other direction: rendering
what is installed as a file ``pip install -r`` (or a colleague, or PyPackHub
itself on another machine) can consume.

Every emitted line is validated before it is written, the same way an
imported one is: the installed listing is not more trusted than the network
it came from, and a name that cannot round-trip through
``validate_package_spec`` is skipped with the reason rather than emitted
broken.

Only packages with a known, parsable version are pinned: a row the listing
could not version becomes a comment saying so, never a line that silently
means "any version".
"""

from __future__ import annotations

from collections.abc import Iterable

from pypackhub.core.validation import ValidationError, validate_package_spec

__all__ = ["export_requirements"]


def export_requirements(packages: Iterable[tuple[str, str]]) -> str:
    """Render ``(name, version)`` pairs as requirements.txt lines.

    Output is deterministic - names sorted, one ``name==version`` per line -
    because a requirements file a person commits should not churn between
    two exports of the same environment. A row whose name or version does
    not survive validation becomes a flagged comment instead of a line
    nobody can install.
    """
    rows = sorted({(name.strip(), version.strip()) for name, version in packages})
    lines: list[str] = []

    for name, version in rows:
        if not name or not version:
            lines.append("# skipped: no name or version recorded")
            continue
        try:
            validate_package_spec(f"{name}=={version}")
        except ValidationError:
            # A name or version that cannot round-trip is a broken row, and
            # broken rows are reported in the file rather than dropped: the
            # user sees exactly what was left out and why.
            lines.append(f"# skipped: unparsable entry {name} {version}")
            continue
        lines.append(f"{name}=={version}")

    return "\n".join(lines) + ("\n" if lines else "")
