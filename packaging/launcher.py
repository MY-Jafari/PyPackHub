"""Entry point for the stand-alone (PyInstaller) builds.

The spec file bundles this module instead of the ``pypackhub`` console script.
There is no ``sys.path`` juggling here on purpose: ``packaging/pypackhub.spec``
adds ``src/`` to ``pathex``, and the wheel metadata is copied into the bundle,
so ``import pypackhub`` and ``importlib.metadata.version("pypackhub")`` both
resolve exactly the way they do in an installed interpreter.

Keep this file free of application logic. Everything it can do, ``python -m
pypackhub`` can do too - that equivalence is what the release smoke test
asserts.
"""

from __future__ import annotations

import sys


def main() -> int:
    from pypackhub.__main__ import main as app_main

    return app_main()


if __name__ == "__main__":
    sys.exit(main())
