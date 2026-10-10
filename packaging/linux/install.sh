#!/bin/sh
# Install PyPackHub from an unpacked release archive.
#
# The Linux release is a tarball of a self-contained directory rather than a
# .deb or an .rpm, because a .deb would pin one distribution's layout and one
# packaging convention; this script does the equivalent of installing one, and
# it can be read in a minute - which matters for an archive people download
# from GitHub and run directly.
#
# Usage:
#   ./install.sh                 install for the current user (default)
#   ./install.sh --system         install for every user; needs root
#   PREFIX=/opt/py ./install.sh   choose the destination
#   ./install.sh --uninstall      remove what this script installed
#
# It copies the bundle rather than moving it, so the unpacked archive stays
# usable as an uninstallable source of truth, and everything it writes is
# inside one prefix - nothing outside ${prefix}/lib/pypackhub is touched except
# the launcher symlink and the desktop entry, both of which are named
# pypackhub so they can never collide with something else.

set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
bundle="${here}/PyPackHub"

die() {
  echo "error: $*" >&2
  exit 1
}

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
}

mode="user"
action="install"

for argument in "$@"; do
  case "${argument}" in
    --system) mode="system" ;;
    --user) mode="user" ;;
    --uninstall) action="uninstall" ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: ${argument} (try --help)" ;;
  esac
done

if [ "${mode}" = "system" ] && [ -z "${PREFIX:-}" ]; then
  prefix="/usr/local"
  if [ "$(id -u)" != "0" ]; then
    die "--system installs into ${prefix}, which needs root: re-run with sudo, or set PREFIX"
  fi
else
  prefix="${PREFIX:-${HOME}/.local}"
fi

libdir="${prefix}/lib/pypackhub"
bindir="${prefix}/bin"
desktop_dir="${prefix}/share/applications"
icon_dir="${prefix}/share/icons/hicolor/256x256/apps"

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

if [ "${action}" = "uninstall" ]; then
  removed=0
  for path in \
    "${libdir}" \
    "${bindir}/pypackhub" \
    "${desktop_dir}/pypackhub.desktop" \
    "${icon_dir}/pypackhub.png"; do
    if [ -e "${path}" ] || [ -L "${path}" ]; then
      rm -rf -- "${path}"
      echo "removed ${path}"
      removed=$((removed + 1))
    fi
  done
  if [ "${removed}" -eq 0 ]; then
    echo "nothing to remove under ${prefix}"
  else
    echo "PyPackHub uninstalled from ${prefix}"
  fi
  exit 0
fi

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

[ -x "${bundle}/PyPackHub" ] || \
  die "PyPackHub/ is not next to this script - run this from the unpacked release archive"

# Qt on Linux needs a handful of system libraries that a bundle cannot ship:
# they come from the distribution. Checking here turns "it does not start, with
# no message at all" into one clear sentence, which is the usual first-run
# experience people report for packaged Qt applications.
if command -v ldconfig >/dev/null 2>&1; then
  missing=""
  for library in libGL.so libxcb-cursor.so.0 libxkbcommon.so.0 libfontconfig.so.1; do
    if ! ldconfig -p 2>/dev/null | grep -q "${library}"; then
      missing="${missing} ${library}"
    fi
  done
  if [ -n "${missing}" ]; then
    echo "warning: missing system libraries:${missing}" >&2
    echo "  Debian/Ubuntu: sudo apt install libgl1 libxcb-cursor0 libxkbcommon0 libfontconfig1" >&2
    echo "  Fedora:        sudo dnf install mesa-libGL xcb-util-cursor xkbcommon fontconfig" >&2
  fi
fi

echo "Installing to ${libdir}"
mkdir -p "${libdir}" "${bindir}" "${desktop_dir}" "${icon_dir}"

# Copy in, then replace atomically enough for a desktop application: build the
# new tree beside the old one and swap it, so an interrupted install cannot
# leave a half-written application in place.
staging="${libdir}.new"
rm -rf -- "${staging}"
cp -R "${bundle}" "${staging}"
rm -rf -- "${libdir}.old"
if [ -d "${libdir}" ]; then
  mv "${libdir}" "${libdir}.old"
fi
mv "${staging}" "${libdir}"
rm -rf -- "${libdir}.old"

binary="${libdir}/PyPackHub"
chmod +x "${binary}"

# Replace the symlink rather than failing when it already exists.
rm -f -- "${bindir}/pypackhub"
ln -sf "${binary}" "${bindir}/pypackhub"

# Desktop entries require a quoted Exec value whenever the path contains a
# space, and prefixes are allowed to contain them (PREFIX="/opt/my tools").
# Quoting unconditionally is always valid and never depends on what the user
# chose as PREFIX.
cat > "${desktop_dir}/pypackhub.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Version=1.0
Name=PyPackHub
GenericName=Python package manager
Comment=View and manage Python packages and environments
Exec="${binary}"
Icon=pypackhub
Terminal=false
Categories=Development;Utility;
Keywords=python;pip;uv;packages;venv;virtualenv;
StartupNotify=true
DESKTOP

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "${desktop_dir}" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "${icon_dir}/.." >/dev/null 2>&1 || true
fi

if [ -f "${here}/icon.png" ]; then
  cp "${here}/icon.png" "${icon_dir}/pypackhub.png"
elif [ -f "${bundle}/_internal/pypackhub/assets/icons/app.svg" ]; then
  # No raster icon beside the script: the SVG is the artwork's source, and most
  # desktop environments render it directly.
  scalable="${prefix}/share/icons/hicolor/scalable/apps"
  mkdir -p "${scalable}"
  cp "${bundle}/_internal/pypackhub/assets/icons/app.svg" "${scalable}/pypackhub.svg"
fi

echo
echo "Installed PyPackHub."
echo "  Launcher: ${bindir}/pypackhub"
echo "  Desktop:  ${desktop_dir}/pypackhub.desktop"
echo
echo "Run it with:  pypackhub        (or find PyPackHub in your application menu)"
echo "Uninstall:    $0 --uninstall   (same PREFIX if you chose one)"
