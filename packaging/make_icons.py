"""Turn the application's SVG mark into the icon files each desktop expects.

The release workflow runs this before PyInstaller, so the installer, the window
manager and the task bar all show the same artwork without any of it being
checked in as an opaque binary blob. One vector source, three containers:

``icon.ico``
    Windows. A PNG-compressed ICO holding 16, 24, 32, 48, 64, 128 and 256 pixel
    frames. Windows 7 and later read PNG frames natively, and Explorer picks the
    frame that matches the current display scaling.
``icon.icns``
    macOS. Written directly rather than shelling out to ``iconutil``, which is
    macOS-only: the container is a flat list of length-prefixed chunks, each
    holding a PNG, so the whole format fits in twenty lines and the file can be
    produced on the Linux and Windows runners too (which is how CI verifies it).
``icon.png``
    256 pixels, for Linux desktop entries and for anything that wants a plain
    image instead of a container.

Rendering goes through Qt's SVG renderer, which is already a runtime
dependency, so the images are the real artwork at every size instead of a
downscaled bitmap. The Qt imports live inside the functions because
``QT_QPA_PLATFORM`` has to be set before the toolkit is loaded.

Usage::

    python packaging/make_icons.py                 # write next to this file
    python packaging/make_icons.py --check         # verify only, write nothing
    python packaging/make_icons.py --out DIR       # write somewhere else
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path
from typing import Final

PACKAGING_DIR: Final = Path(__file__).resolve().parent
REPOSITORY_ROOT: Final = PACKAGING_DIR.parent
DEFAULT_SOURCE: Final = REPOSITORY_ROOT / "src" / "pypackhub" / "assets" / "icons" / "app.svg"

#: Frames written into the ICO, smallest first - the order Explorer expects.
ICO_SIZES: Final = (16, 24, 32, 48, 64, 128, 256)

#: The PNG payload embedded in the ICNS, in the order ``iconutil`` emits them.
#: Each entry maps a four-byte chunk type to the pixel size of its image.
ICNS_CHUNKS: Final = (
    ("icp4", 16),
    ("icp5", 32),
    ("icp6", 64),
    ("ic07", 128),
    ("ic08", 256),
    ("ic09", 512),
    ("ic10", 1024),
    ("ic11", 32),
    ("ic12", 64),
    ("ic13", 256),
    ("ic14", 512),
)

ICNS_MAGIC: Final = b"icns"
PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"

#: Enough for every frame plus headers, with room to spare. The largest source
#: image is 1024 pixels, so a truncated or bit-flipped file is caught here.
MAX_PNG_BYTES: Final = 4 * 1024 * 1024


def _render_pngs(source: Path, sizes: list[int]) -> dict[int, bytes]:
    """Render ``source`` once per distinct size and return PNG bytes per size."""

    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    # Qt wants an application object before it will set up fonts and palettes.
    # The offscreen plugin keeps this usable on a headless CI runner. The
    # instance is owned by Qt for the lifetime of the process.
    if QGuiApplication.instance() is None:
        QGuiApplication(sys.argv[:1])

    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise SystemExit(f"not a renderable SVG: {source}")

    images: dict[int, bytes] = {}

    for size in sizes:
        image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)

        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        # The drawing is scaled to the frame explicitly instead of relying on
        # the painter's default viewport, so every size is exactly square.
        renderer.render(painter, QRectF(0.0, 0.0, float(size), float(size)))
        painter.end()

        payload = QByteArray()
        buffer = QBuffer(payload)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise SystemExit(f"Qt could not encode the {size} pixel frame as PNG")
        buffer.close()

        data = bytes(payload)
        if not data.startswith(PNG_SIGNATURE):
            raise SystemExit(f"the {size} pixel frame is not a PNG")
        if len(data) > MAX_PNG_BYTES:
            raise SystemExit(f"the {size} pixel frame is implausibly large")

        images[size] = data

    return images


def build_ico(images: dict[int, bytes], sizes: tuple[int, ...] = ICO_SIZES) -> bytes:
    """Assemble a PNG-compressed ICO container."""

    directory = struct.pack("<HHH", 0, 1, len(sizes))
    offset = len(directory) + 16 * len(sizes)
    entries: list[bytes] = []
    payloads: list[bytes] = []

    for size in sizes:
        data = images[size]
        # A dimension of 256 is encoded as 0 in the one-byte width/height fields.
        dimension = 0 if size >= 256 else size
        entries.append(
            struct.pack(
                "<BBBBHHII",
                dimension,
                dimension,
                0,  # palette size: 0 for a true-colour image
                0,  # reserved
                1,  # colour planes
                32,  # bits per pixel
                len(data),
                offset,
            )
        )
        payloads.append(data)
        offset += len(data)

    return directory + b"".join(entries) + b"".join(payloads)


def build_icns(images: dict[int, bytes]) -> bytes:
    """Assemble an ICNS container from PNG chunks.

    The format is a magic number, the total length, then a sequence of chunks:
    a four-byte type, a four-byte big-endian length that counts the header
    itself, and the image. Everything here is big-endian, unlike ICO.
    """

    body = bytearray()

    for chunk_type, size in ICNS_CHUNKS:
        data = images[size]
        body += chunk_type.encode("ascii")
        body += struct.pack(">I", len(data) + 8)
        body += data

    return ICNS_MAGIC + struct.pack(">I", len(body) + 8) + bytes(body)


def verify(path: Path, expected_prefix: bytes, minimum_bytes: int) -> int:
    """Return the file size, or exit when the artifact is not usable."""

    if not path.is_file():
        raise SystemExit(f"expected {path} to exist")

    data = path.read_bytes()

    if not data.startswith(expected_prefix):
        raise SystemExit(f"{path} does not start with {expected_prefix!r}")
    if len(data) < minimum_bytes:
        raise SystemExit(f"{path} is only {len(data)} bytes, which looks truncated")

    return len(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="make_icons",
        description="Render the PyPackHub SVG mark into ICO, ICNS and PNG icons.",
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="the SVG to render")
    parser.add_argument("--out", type=Path, default=PACKAGING_DIR, help="where to write the icons")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify existing icons instead of generating them",
    )
    arguments = parser.parse_args(argv)

    # Must be set before PySide6 is imported, which happens inside _render_pngs.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    ico_path = arguments.out / "icon.ico"
    icns_path = arguments.out / "icon.icns"
    png_path = arguments.out / "icon.png"

    if arguments.check:
        for path, prefix, minimum in (
            (ico_path, b"\x00\x00\x01\x00", 1024),
            (icns_path, ICNS_MAGIC, 4096),
            (png_path, PNG_SIGNATURE, 512),
        ):
            size = verify(path, prefix, minimum)
            print(f"{path.name}: {size} bytes")

        return 0

    if not arguments.source.is_file():
        raise SystemExit(f"the icon source is missing: {arguments.source}")

    arguments.out.mkdir(parents=True, exist_ok=True)

    sizes = sorted({*ICO_SIZES, *(size for _, size in ICNS_CHUNKS), 512, 1024})
    images = _render_pngs(arguments.source, sizes)

    ico_path.write_bytes(build_ico(images))
    icns_path.write_bytes(build_icns(images))
    png_path.write_bytes(images[256])

    for path in (ico_path, icns_path, png_path):
        print(f"{path}: {path.stat().st_size} bytes")

    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the release workflow
    raise SystemExit(main())
