"""``python -m pypackhub`` - launch the desktop application.

The ``pypackhub`` console script calls the same entry point, so both routes share
one bootstrap and one set of identifiers.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    """Run the application and return its exit code."""
    from pypackhub.ui.app import run

    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
