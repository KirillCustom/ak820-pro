#!/usr/bin/env bash
# build-f19-listener.sh -- build, sign and install the wake rig's Mac-side
# listener (hostagent/f19listener/main.swift) as a background app bundle.
#
#   scripts/build-f19-listener.sh            # build, sign, install
#   open -a "$HOME/Applications/AK820 F19 Listener.app"   # start (asks for Input Monitoring once)
#   pkill -x ak820-f19-listener              # stop
#
# An app of its own, started by `open`, so macOS grants Input Monitoring to
# it, not to the shell or tool that started it. Signed with the Developer ID
# Application identity and a fixed identifier, as sign-agent-macos.sh does,
# because TCC keys the grant on the signature: an ad-hoc one changes with every
# build and would ask again each time.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HOME/Applications/AK820 F19 Listener.app"
ID=com.jdlien.ak820pro.f19listener
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

mkdir -p "$BUILD/app/Contents/MacOS"
xcrun swiftc -O -o "$BUILD/app/Contents/MacOS/ak820-f19-listener" "$ROOT/hostagent/f19listener/main.swift"
cat > "$BUILD/app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>$ID</string>
  <key>CFBundleName</key><string>AK820 F19 Listener</string>
  <key>CFBundleExecutable</key><string>ak820-f19-listener</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
</dict>
</plist>
EOF

SHA1="$(security find-identity -v -p codesigning | awk '/Developer ID Application/ {print $2; exit}')"
[ -n "$SHA1" ] || { echo "no Developer ID Application identity in the keychain" >&2; exit 1; }
codesign --force --options runtime --timestamp --identifier "$ID" --sign "$SHA1" "$BUILD/app"
codesign --verify --strict "$BUILD/app"

pkill -x ak820-f19-listener 2>/dev/null || true
mkdir -p "$HOME/Applications"
rm -rf "$APP"
cp -R "$BUILD/app" "$APP"
echo "installed $APP ($ID)"
