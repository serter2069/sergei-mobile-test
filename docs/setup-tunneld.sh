#!/bin/bash
# One-time setup: install tunneld as LaunchDaemon (auto-start at boot as root)
# Run: sudo bash docs/setup-tunneld.sh

set -e

PLIST="/Library/LaunchDaemons/com.mobile-test-ai.tunneld.plist"
PYTHON=$(which python3)

if [ "$(id -u)" != "0" ]; then
    echo "ERROR: run as root: sudo bash $0"
    exit 1
fi

cat > "$PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.mobile-test-ai.tunneld</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>-m</string>
        <string>pymobiledevice3</string>
        <string>remote</string>
        <string>tunneld</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/var/log/mobile-test-tunneld.log</string>
    <key>StandardErrorPath</key>
    <string>/var/log/mobile-test-tunneld.log</string>
    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
EOF

chmod 644 "$PLIST"
chown root:wheel "$PLIST"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"

echo "tunneld daemon installed and started."
echo "Verify: curl -s http://127.0.0.1:49151"
echo "Logs:   tail -f /var/log/mobile-test-tunneld.log"
