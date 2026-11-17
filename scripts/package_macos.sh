#!/usr/bin/env bash
# Wrap the PyInstaller binary into a .app bundle and publish a .dmg + .zip.
# Usage: scripts/package_macos.sh <arm64|x64>
set -euo pipefail

cd "$(dirname "$0")/.."
ARCH="${1:-$(uname -m)}"
case "$ARCH" in
  x64|x86_64) ARCH="x64" ;;
  arm64|aarch64) ARCH="arm64" ;;
  *) echo "unknown arch: $ARCH" >&2; exit 1 ;;
esac
VERSION="$(${PYTHON:-python3} -c 'import pyshield; print(pyshield.__version__)')"

APP="build/PyShield.app"
BIN="dist/PyShieldBuilder"
ICON="gui/assets/icon.png"
[[ -x "$BIN" ]] || { echo "missing $BIN; build PyShieldBuilder.spec first" >&2; exit 1; }

rm -rf "$APP" build/icon.iconset
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
install -m 0755 "$BIN" "$APP/Contents/MacOS/PyShield"

mkdir -p build/icon.iconset
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$ICON" --out "build/icon.iconset/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z "$double" "$double" "$ICON" --out "build/icon.iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns build/icon.iconset -o "$APP/Contents/Resources/pyshield.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>PyShield</string>
  <key>CFBundleDisplayName</key><string>PyShield</string>
  <key>CFBundleIdentifier</key><string>dev.pyshield.builder</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleExecutable</key><string>PyShield</string>
  <key>CFBundleIconFile</key><string>pyshield.icns</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSRequiresAquaSystemAppearance</key><false/>
</dict>
</plist>
PLIST

codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || echo "ad-hoc codesign skipped"

hdiutil create -volname "PyShield" -srcfolder "$APP" -ov -format UDZO \
  "dist/PyShieldBuilder-${VERSION}-macos-${ARCH}.dmg" >/dev/null
ditto -c -k --keepParent "$APP" "dist/PyShieldBuilder-${VERSION}-macos-${ARCH}.zip"

shasum -a 256 dist/PyShieldBuilder-*.dmg dist/PyShieldBuilder-*.zip \
  > "dist/SHA256SUMS-macos-${ARCH}.txt"
cat "dist/SHA256SUMS-macos-${ARCH}.txt"
