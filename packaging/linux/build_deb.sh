#!/bin/sh
# Build a Debian package (.deb) from a built PyInstaller bundle.
#
# Usage:
#   packaging/linux/build_deb.sh --bundle dist/PyPackHub --version 0.1.0 \
#       --output release [--icon packaging/icon.png]
#
# The package installs the bundle under /opt/pypackhub - the conventional home
# of a self-contained third-party application that must not be scattered into
# /usr/lib - and touches nothing outside its own files except the launcher
# symlink, the desktop entry and the icon, each named "pypackhub" so nothing
# of another package's can collide with them.
#
# It is built with plain dpkg-deb rather than a higher-level packager: one
# staging tree, one control file, no extra dependencies to install on the
# build machine, and every byte of the package can be read in a minute from
# this script alone.
#
# The runtime dependencies are everything the bundle loads from the system,
# verified by installing the package into a clean Debian and launching the
# application; packaging/linux/install.sh warns about the loudest four of them
# at first run, but a package manager can and should declare the whole set.

set -eu

usage() {
  sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
}

die() {
  echo "build_deb: error: $*" >&2
  exit 1
}

bundle=""
version=""
output=""
icon=""

while [ $# -gt 0 ]; do
  case "$1" in
    --bundle) bundle="${2:?}"; shift 2 ;;
    --version) version="${2:?}"; shift 2 ;;
    --output) output="${2:?}"; shift 2 ;;
    --icon) icon="${2:?}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
done

[ -n "${bundle}" ] || { usage; die "--bundle is required"; }
[ -n "${version}" ] || { usage; die "--version is required"; }
[ -n "${output}" ] || { usage; die "--output is required"; }

[ -x "${bundle}/PyPackHub" ] || \
  die "${bundle}/PyPackHub is not executable - build the bundle first (PyInstaller, one-directory)"

# A Debian version may only contain [A-Za-z0-9.+~-]; the upstream version is
# plain numbers and dots today, and refusing anything else keeps the package
# from being un-installable rather than discovering it later.
case "${version}" in
  *[!A-Za-z0-9.+~-]*) die "version '${version}' contains characters a .deb cannot carry" ;;
esac

if [ -z "${icon}" ]; then
  here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
  icon="${here}/../icon.png"
fi
[ -f "${icon}" ] || die "icon '${icon}' not found"

command -v dpkg-deb >/dev/null 2>&1 || die "dpkg-deb not found - build this on a Debian-based system"

staging=$(mktemp -d)
trap 'rm -rf -- "${staging}"' EXIT

libdir="${staging}/opt/pypackhub"
mkdir -p "${libdir}" \
  "${staging}/usr/bin" \
  "${staging}/usr/share/applications" \
  "${staging}/usr/share/icons/hicolor/256x256/apps"

# The bundle goes in whole. cp -a keeps the executable bit PyInstaller set;
# a final no-op chmod states what is being promised about the launcher.
cp -a "${bundle}" "${libdir}/PyPackHub"
chmod 0755 "${libdir}/PyPackHub/PyPackHub"

ln -sf /opt/pypackhub/PyPackHub/PyPackHub "${staging}/usr/bin/pypackhub"

# The desktop entry matches the one install.sh writes, so the menu behaves
# the same whether the app arrived from the tarball, the .deb or pacman.
cat > "${staging}/usr/share/applications/pypackhub.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Version=1.0
Name=PyPackHub
GenericName=Python package manager
Comment=View and manage Python packages and environments
Exec=/opt/pypackhub/PyPackHub/PyPackHub
Icon=pypackhub
Terminal=false
Categories=Development;Utility;
Keywords=python;pip;uv;packages;venv;virtualenv;
StartupNotify=true
DESKTOP

cp "${icon}" "${staging}/usr/share/icons/hicolor/256x256/apps/pypackhub.png"

mkdir -p "${staging}/DEBIAN"
size_kib=$(du -sk --exclude=DEBIAN "${staging}" | cut -f1)
cat > "${staging}/DEBIAN/control" <<CONTROL
Package: pypackhub
Version: ${version}
Section: utils
Priority: optional
Architecture: amd64
Depends: libgl1, libegl1, libxcb-cursor0, libxkbcommon0, libfontconfig1, libglib2.0-0, libdbus-1-3, libgssapi-krb5-2
Maintainer: PyPackHub contributors <https://github.com/MY-Jafari/PyPackHub>
Installed-Size: ${size_kib}
Description: One visual interface for every Python environment
 PyPackHub is a cross-platform desktop application for looking at and
 managing Python packages and environments. It speaks pip and uv, keeps an
 automatic snapshot before every change, and can undo any action it ran.
Homepage: https://github.com/MY-Jafari/PyPackHub
CONTROL

cat > "${staging}/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
# Refresh the caches the desktop uses to notice the new entry and icon. Every
# command is optional (a minimal container has none of them), which is why
# each failure is explicitly tolerated.
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi
POSTINST
chmod 0755 "${staging}/DEBIAN/postinst"

mkdir -p "${output}"
package="${output}/pypackhub_${version}_amd64.deb"

# --root-owner-group: the staging tree belongs to the building user, and a
# package must never ship their uid/gid.
dpkg-deb --root-owner-group --build "${staging}" "${package}"

echo "built ${package}"
