# iOS Setup Guide

## Requirements

- macOS 12+
- iPhone with iOS 16+ (tested on iOS 18)
- Developer Mode enabled on device: **Settings → Privacy & Security → Developer Mode**
- idb installed: `brew install idb-companion`

## Install

```bash
pip install pymobiledevice3 openai Pillow
```

## Pair the device

```bash
idb list-targets   # should show your iPhone
```

If not visible, run:
```bash
idb_companion --udid <UDID> &
```

## Screenshots

On iOS 18, the legacy `screenshotr` service was removed. `mobile-test-ai` uses the DVT instruments hub instead, which requires `tunneld`.

### Option A: Start tunneld manually (per session)

```bash
mobile-test tunneld
# Enter sudo password once. Keep running in background or a separate tab.
```

### Option B: Auto-start at boot (recommended)

```bash
sudo bash docs/setup-tunneld.sh
```

This installs a LaunchDaemon that starts tunneld automatically when the Mac boots. No more manual steps.

### Verify

```bash
mobile-test test
# Should print: iOS tunneld: running
# Opens a screenshot of your iPhone screen
```

## Troubleshooting

**"device not found via tunneld"**
- Make sure iPhone is unlocked and trusted on this Mac
- Check cable connection
- Try: `idb list-targets`

**"tunneld didn't start"**
- Run manually: `sudo python3 -m pymobiledevice3 remote tunneld`
- Check for errors in the output

**idb issues**
```bash
# Kill stale companion
pkill idb_companion
# Restart
idb_companion --udid <UDID> &
```
