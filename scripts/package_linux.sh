#!/usr/bin/env bash
# Build the Linux distribution packages from dist/PyShieldBuilder.
# Produces: tar.gz, .deb, .rpm and an AppImage in dist/.
set -euo pipefail

cd "$(dirname "$0")/.."
VERSION="$(${PYTHON:-python3} -c 'import pyshield; print(pyshield.__version__)')"
BIN="dist/PyShieldBuilder"
ICON="gui/assets/icon.png"
[[ -x "$BIN" ]] || { echo "missing $BIN; build PyShieldBuilder.spec first" >&2; exit 1; }

ROOT="build/pkgroot"
rm -rf "$ROOT" build/AppDir build/rpm build/tar build/appimagetool
mkdir -p "$ROOT/opt/pyshield" "$ROOT/usr/bin" \
  "$ROOT/usr/share/applications" \
  "$ROOT/usr/share/icons/hicolor/256x256/apps"
install -m 0755 "$BIN" "$ROOT/opt/pyshield/PyShieldBuilder"
ln -sf /opt/pyshield/PyShieldBuilder "$ROOT/usr/bin/pyshield"
install -m 0644 "$ICON" "$ROOT/usr/share/icons/hicolor/256x256/apps/pyshield.png"

cat > "$ROOT/usr/share/applications/pyshield.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=PyShield
GenericName=Django Source Protection
Comment=Build a protected copy of a Django project
Exec=pyshield
Icon=pyshield
Terminal=false
Categories=Development;Utility;
DESKTOP

echo "== tar.gz =="
mkdir -p build/tar/PyShieldBuilder
cp "$BIN" build/tar/PyShieldBuilder/
cp "$ICON" build/tar/PyShieldBuilder/pyshield.png
cp README.md build/tar/PyShieldBuilder/README.md
cp "$ROOT/usr/share/applications/pyshield.desktop" build/tar/PyShieldBuilder/
tar -C build/tar -czf "dist/PyShieldBuilder-${VERSION}-linux-x64.tar.gz" PyShieldBuilder

echo "== deb =="
mkdir -p "$ROOT/DEBIAN"
cat > "$ROOT/DEBIAN/control" <<DEB
Package: pyshield-builder
Version: ${VERSION}
Section: devel
Priority: optional
Architecture: amd64
Maintainer: PyShield <support@pyshield.local>
Depends: libgtk-3-0t64 | libgtk-3-0, libwebkit2gtk-4.1-0, python3-gi, gir1.2-webkit2-4.1
Description: PyShield desktop builder
 Build protected copies of Django projects from a desktop GUI.
DEB
dpkg-deb --root-owner-group --build "$ROOT" "dist/PyShieldBuilder_${VERSION}_amd64.deb"

echo "== rpm =="
TOPDIR="$PWD/build/rpm"
mkdir -p "$TOPDIR/BUILD" "$TOPDIR/RPMS" "$TOPDIR/SOURCES" "$TOPDIR/SPECS" "$TOPDIR/SRPMS"
tar -C "$ROOT" -czf "$TOPDIR/SOURCES/pyshield-${VERSION}.tar.gz" .
cat > "$TOPDIR/SPECS/pyshield.spec" <<SPEC
Name: pyshield-builder
Version: ${VERSION}
Release: 1
Summary: PyShield desktop builder
License: Proprietary
BuildArch: x86_64
AutoReqProv: no
Requires: gtk3, webkit2gtk4.1
%description
PyShield desktop builder: build protected copies of Django projects.
%install
mkdir -p %{buildroot}
tar -C %{buildroot} -xzf %{_sourcedir}/pyshield-${VERSION}.tar.gz
%files
/opt/pyshield/PyShieldBuilder
/usr/bin/pyshield
/usr/share/applications/pyshield.desktop
/usr/share/icons/hicolor/256x256/apps/pyshield.png
SPEC
rpmbuild -bb --define "_topdir $TOPDIR" "$TOPDIR/SPECS/pyshield.spec" >/dev/null
cp "$TOPDIR"/RPMS/x86_64/*.rpm "dist/PyShieldBuilder-${VERSION}-x86_64.rpm"

echo "== AppImage =="
curl -fsSL -o build/appimagetool \
  https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
chmod +x build/appimagetool
APP="build/AppDir"
mkdir -p "$APP/usr/bin" "$APP/usr/share/icons/hicolor/256x256/apps"
install -m 0755 "$BIN" "$APP/usr/bin/PyShieldBuilder"
install -m 0644 "$ICON" "$APP/pyshield.png"
cp "$APP/pyshield.png" "$APP/.DirIcon"
cp "$ROOT/usr/share/applications/pyshield.desktop" "$APP/pyshield.desktop"
cat > "$APP/AppRun" <<'APPRUN'
#!/bin/sh
HERE=$(dirname "$(readlink -f "$0")")
exec "$HERE/usr/bin/PyShieldBuilder" "$@"
APPRUN
chmod +x "$APP/AppRun"
ARCH=x86_64 ./build/appimagetool --appimage-extract-and-run "$APP" \
  "dist/PyShieldBuilder-${VERSION}-x86_64.AppImage"

rm -rf "$ROOT" build/tar build/AppDir
ls -lh dist
