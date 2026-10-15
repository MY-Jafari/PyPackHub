# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the stand-alone PyPackHub desktop builds.

Run it from the repository root::

    pyinstaller packaging/pypackhub.spec --noconfirm

``dist/PyPackHub`` then holds a self-contained directory: a bootloader
executable, a Python runtime, the Qt libraries and the bundled fonts, icons and
translations. No interpreter has to be installed on the target machine, which is
the whole point of shipping an "installer" rather than only a wheel.

Three things this spec is careful about:

* **The user's own packages are not involved.** The app manages *other*
  environments through their interpreters; it never imports them, so nothing
  from site-packages beyond PyPackHub's own dependencies is collected.
* **Only the Qt modules the app imports.** PySide6 ships QtWebEngine, Quick and
  the rest of the toolkit, worth several hundred megabytes that a widget
  application never touches. They are excluded by name, and the smoke test in
  the release workflow is what proves the exclusions are safe.
* **Data files are collected from the installed metadata,** not from a
  hand-written list, so adding a font or a translation file cannot be forgotten
  here and only discovered by a user.

The build is a one-directory bundle on every platform, wrapped in a ``.app`` on
macOS so Finder treats it as an application instead of a loose binary.
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

APP_NAME = "PyPackHub"
APP_IDENTIFIER = "pypackhub"

SPEC_DIR = Path(SPECPATH).resolve()  # noqa: F821 - provided by PyInstaller
PROJECT_ROOT = SPEC_DIR.parent
SOURCE_ROOT = PROJECT_ROOT / "src"


def build_version() -> str:
    """The version baked into the bundle, so the About box is truthful."""

    from_env = os.environ.get("PYPACKHUB_VERSION", "").strip()
    if from_env:
        return from_env

    try:
        from importlib.metadata import version

        return version(APP_IDENTIFIER)
    except Exception:  # pragma: no cover - only when the venv is unusual
        return "0.0.0"


VERSION = build_version()


def _version_tuple(text: str, width: int = 4) -> list[int]:
    """Render a PEP 440 version as the integer tuple Windows resources want.

    ``0.2.0rc1`` becomes ``[0, 2, 0, 0]``: the fields Windows understands are
    the numeric release segments, and anything else (pre-release tags, local
    versions) is simply not representable there.
    """

    numbers: list[int] = []
    for part in text.split("."):
        digits = ""
        for character in part:
            if not character.isdigit():
                break
            digits += character
        numbers.append(int(digits) if digits else 0)

    numbers = numbers[:width]
    numbers.extend([0] * (width - len(numbers)))
    return numbers


# --------------------------------------------------------------------------- #
# Data files                                                                   #
# --------------------------------------------------------------------------- #

datas = []

# Fonts, icons and any future non-Python file inside the package. Derived from
# the package contents rather than listed by hand: PyInstaller walks nested
# subdirectories, so a new asset ships without this file being touched.
datas += collect_data_files(APP_IDENTIFIER)

# The distribution metadata, so `importlib.metadata.version("pypackhub")` keeps
# answering correctly inside the frozen app and the About dialog shows the real
# number instead of the "0.0.0+unknown" fallback in pypackhub/__init__.py.
datas += copy_metadata(APP_IDENTIFIER)

# httpx verifies TLS against certifi's CA bundle. If the bundle were missing,
# every HTTPS request would fail certificate validation on a machine where the
# interpreter finds no system CA store - exactly the "clean machine" case this
# build exists for. `pyinstaller-hooks-contrib` has a certifi hook, but the
# guarantee matters enough to state it here instead of inferring it.
datas += collect_data_files("certifi")

# Empty on purpose. The two dynamic-lookup dependencies are covered by hooks:
# PyInstaller ships ``hook-keyring.py``, which collects ``keyring.backends`` and
# copies its metadata so entry-point discovery still works, and
# ``pyinstaller-hooks-contrib`` ships the certifi hook. Listing either again
# here would be dead weight.
hiddenimports: list[str] = []


# --------------------------------------------------------------------------- #
# Exclusions                                                                   #
# --------------------------------------------------------------------------- #

# A widget application uses QtCore, QtGui, QtWidgets and QtSvg. Everything else
# PySide6 ships is dead weight; the release smoke test launches the built
# application, so a wrong entry here fails the build rather than a user's launch.
EXCLUDED_QT_MODULES = [
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNfc",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtStateMachine",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtUiTools",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
]

EXCLUDES = EXCLUDED_QT_MODULES + [
    # Not used by the application, and dragging unittest's machinery into a
    # frozen bundle invites surprising imports.
    "doctest",
    "pydoc",
    "tkinter",
    "unittest",
]


# --------------------------------------------------------------------------- #
# Icon and Windows version resource                                            #
# --------------------------------------------------------------------------- #

# `packaging/make_icons.py` writes these; the release workflow runs it first. A
# missing icon is not an error - a developer building locally straight from a
# clone should not have to render icons to get a working bundle.
WIN_ICON = SPEC_DIR / "icon.ico"
MAC_ICON = SPEC_DIR / "icon.icns"


def version_resource() -> "object | None":
    """A Windows version resource, so the .exe has real file properties."""

    if sys.platform != "win32" or not WIN_ICON.is_file():
        return None

    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    numbers = _version_tuple(VERSION)
    dotted = ".".join(str(number) for number in numbers)

    return VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=tuple(numbers),
            prodvers=tuple(numbers),
            mask=0x3F,
            flags=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",
                        [
                            StringStruct("CompanyName", "PyPackHub contributors"),
                            StringStruct("FileDescription", f"{APP_NAME} package manager"),
                            StringStruct("FileVersion", dotted),
                            StringStruct("InternalName", APP_NAME),
                            StringStruct("LegalCopyright", "GPL-3.0-only"),
                            StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
                            StringStruct("ProductName", APP_NAME),
                            StringStruct("ProductVersion", VERSION),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )


# --------------------------------------------------------------------------- #
# Analysis                                                                     #
# --------------------------------------------------------------------------- #

analysis = Analysis(
    [str(SPEC_DIR / "launcher.py")],
    pathex=[str(SOURCE_ROOT), str(SPEC_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

python_archive = PYZ(analysis.pure)  # noqa: F821 - provided by PyInstaller

executable = EXE(  # noqa: F821 - provided by PyInstaller
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(WIN_ICON) if sys.platform == "win32" and WIN_ICON.is_file() else None,
    version=version_resource(),
)

collection = COLLECT(  # noqa: F821 - provided by PyInstaller
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)

if sys.platform == "darwin":
    # Finder only treats a directory as an application when it is a bundle, and
    # the Dock needs the plist to name it correctly. BUNDLE accepts the result of
    # COLLECT, which is what makes a one-directory bundle into a real .app.
    BUNDLE(  # noqa: F821 - provided by PyInstaller
        collection,
        name=f"{APP_NAME}.app",
        icon=str(MAC_ICON) if MAC_ICON.is_file() else None,
        bundle_identifier=APP_IDENTIFIER,
        version=VERSION,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "CFBundlePackageType": "APPL",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "LSApplicationCategoryType": "public.app-category.developer-tools",
            "NSRequiresAquaSystemAppearance": False,
        },
    )
