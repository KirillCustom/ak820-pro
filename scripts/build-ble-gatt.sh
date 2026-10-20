#!/usr/bin/env bash
# build-ble-gatt.sh -- build, sign and install the GATT lister
# (scripts/ble_gatt_list.swift) as a background app bundle, then run it.
#
#   scripts/build-ble-gatt.sh            # build, sign, install
#   open -W --stdout "$LOG" --stderr "$LOG" -a "$HOME/Applications/AK820 BLE Check.app" --args AK820
#
# An app of its own for the same reason as build-led-probe.sh: macOS grants
# Bluetooth to an app that declares NSBluetoothAlwaysUsageDescription, and a
# script run from a terminal never hears back from CoreBluetooth. Signed with
# the Developer ID Application identity and a fixed identifier, so the grant
# holds across builds.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HOME/Applications/AK820 BLE Check.app"
ID=com.jdlien.ak820pro.blecheck
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

mkdir -p "$BUILD/app/Contents/MacOS"
xcrun swiftc -O -o "$BUILD/app/Contents/MacOS/ak820-ble-check" "$ROOT/scripts/ble_gatt_list.swift"
cat > "$BUILD/app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>$ID</string>
  <key>CFBundleName</key><string>AK820 BLE Check</string>
  <key>CFBundleExecutable</key><string>ak820-ble-check</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSBluetoothAlwaysUsageDescription</key><string>Lists the GATT services the AK820 Pro offers. Read-only.</string>
</dict>
</plist>
PLIST

SHA1="$(security find-identity -v -p codesigning | awk '/Developer ID Application/ {print $2; exit}')"
[ -n "$SHA1" ] || { echo "no Developer ID Application identity in the keychain" >&2; exit 1; }
codesign --force --options runtime --timestamp --identifier "$ID" --sign "$SHA1" "$BUILD/app"
codesign --verify --strict "$BUILD/app"

pkill -x ak820-ble-check 2>/dev/null || true
mkdir -p "$HOME/Applications"
rm -rf "$APP"
cp -R "$BUILD/app" "$APP"
echo "installed $APP ($ID)"
