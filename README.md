# sergei-mobile-test

AI-driven self-healing mobile test agent for **iOS and Android**.

Instead of fragile YAML scripts, describe what you want in plain language. The agent sees the device screen via a vision LLM, decides what to tap/type/swipe, executes the action, and adapts automatically if the UI changes.

```bash
mobile-test run "open Settings and enable Dark Mode"
mobile-test run "login with email test@example.com" --save login_flow
mobile-test replay login_flow   # fast replay, no AI needed
```

---

## How self-learning works

The agent builds a library of tested flows called **skills**. No ML training involved — it learns at the behavior level.

```
First run:  AI sees screen → figures out steps → executes → saves as skill
Next run:   replay skill instantly (no AI, no API cost, ~3s)
UI changed: smart mode detects failure → AI re-learns → saves updated skill
```

**Three modes:**

| Command | What happens | Speed |
|---|---|---|
| `run "goal"` | AI sees screen, decides each action | ~15–30s |
| `run "goal" --save NAME` | Same, but saves successful path as skill | ~15–30s (once) |
| `replay NAME` | Replays saved steps without AI | ~3s |
| `smart NAME "goal"` | Tries replay → if broken, re-learns automatically | ~3s or ~30s |

Over time, your skill library grows. Regression testing becomes fast and free — no AI calls until the UI actually changes.

---

## Why not Maestro?

| | Maestro | sergei-mobile-test |
|---|---|---|
| UI changed after redesign | Breaks permanently | Self-heals automatically |
| Write a new test | Hand-write YAML | `run "do this"` |
| Understand screen context | No | Yes (VLM vision) |
| Replay cached flow | ~5s | ~3s (no AI) |
| Run new test | n/a | ~15–30s |
| iOS 18 support | Needs maintenance | Works |
| Android support | Separate tool | Same CLI |

---

## Requirements

**iOS:**
- macOS
- [idb](https://github.com/facebook/idb) — `brew install idb-companion`
- iPhone with Developer Mode enabled (Settings → Privacy & Security → Developer Mode)
- `pymobiledevice3` — `pip install pymobiledevice3` (for silent screenshots)

**Android:**
- [adb](https://developer.android.com/tools/adb) — `brew install android-platform-tools`
- Android device with USB debugging enabled

**AI model — any OpenAI-compatible vision API:**
- [OpenRouter](https://openrouter.ai) — easiest, 100+ models
- [Ollama](https://ollama.ai) — local, free (use `llava` or `minicpm-v`)
- Any other OpenAI-compatible endpoint with vision support

---

## Installation

```bash
git clone https://github.com/YOUR_USERNAME/mobile-test-ai
cd mobile-test-ai
pip install -r requirements.txt
chmod +x bin/mobile-test
export PATH="$PATH:$(pwd)/bin"
```

---

## Configuration

Set three environment variables (add to `~/.zshrc` or `~/.bashrc`):

```bash
# Option A: OpenRouter (cloud, many models)
export MOBILE_TEST_BASE=https://openrouter.ai/api/v1
export MOBILE_TEST_KEY=sk-or-v1-...
export MOBILE_TEST_MODEL=openai/gpt-4o

# Option B: Local Ollama (free, private)
export MOBILE_TEST_BASE=http://localhost:11434/v1
export MOBILE_TEST_KEY=ollama
export MOBILE_TEST_MODEL=minicpm-v:latest
```

---

## Quick Start

### iOS

```bash
# 1. Connect iPhone via USB, trust this Mac on device
# 2. Start tunneld (needed once per session for silent screenshots)
mobile-test tunneld    # enter sudo password once

# 3. Verify connection
mobile-test test       # takes screenshot, opens it

# 4. Run a test
mobile-test run "open Settings and enable Dark Mode"

# 5. Save as skill for fast future replay
mobile-test run "login with email test@example.com password 123" --save login
mobile-test replay login   # replays instantly without AI
```

### Android

```bash
# 1. Enable USB debugging on device (Settings → Developer Options)
# 2. Connect via USB and accept the trust prompt on device

# 3. Verify connection
mobile-test test --platform android

# 4. Run a test
mobile-test run "open Settings and enable Dark Mode" --platform android

# 5. Save and replay
mobile-test run "open Gmail" --save open_gmail --platform android
mobile-test replay open_gmail --platform android
```

---

## Commands

```
mobile-test run "goal"              Run AI agent
mobile-test run "goal" --save NAME  Run and save as skill
mobile-test smart NAME "goal"       Try replay, re-learn if UI changed
mobile-test replay NAME             Fast replay (no AI)
mobile-test skills                  List saved skills
mobile-test screenshot [file]       Save screenshot
mobile-test tap X Y                 Tap at coordinates
mobile-test devices                 List connected iOS + Android devices
mobile-test test                    Verify connection + screenshot
mobile-test tunneld                 Start iOS tunneld (silent screenshots)
mobile-test config                  Show current config
```

**Flags:**
```
--platform ios|android    Target platform (default: ios)
--serial DEVICE_ID        Target specific Android device
```

---

## Skills

Skills are saved action sequences. When a test passes, save it as a skill — future runs replay it instantly without calling the AI (3–5x faster, no API costs).

```bash
# Record
mobile-test run "login with email user@example.com" --save login

# Replay (fast, no AI)
mobile-test replay login

# Auto-heal: try replay, re-learn if UI changed
mobile-test smart login "login with email user@example.com"
```

Skills are stored as JSON in `~/.mobile-test/skills/`. You can edit them manually or commit them to your project repo.

---

## iOS Silent Screenshots Setup

By default, the first screenshot starts `tunneld` (needs sudo once per session). To auto-start at boot:

```bash
# Install as system daemon (one-time setup)
sudo bash docs/setup-tunneld.sh
```

After this, screenshots are completely silent — no windows, no prompts.

See [docs/setup-ios.md](docs/setup-ios.md) for full iOS setup guide.

---

## Architecture

```
Device (USB)
  │
  ├── iOS:     idb → tap/swipe/type
  │            pymobiledevice3 DVT → screenshot (silent)
  │
  └── Android: adb shell input → tap/swipe/type
               adb exec-out screencap → screenshot (always silent)
  │
  ▼
agent.py
  1. screenshot() → base64 PNG
  2. ask_vlm(goal, screenshot, history) → next_action JSON
  3. execute_action(action)
  4. repeat until done | failed | max_steps
  5. save_skill() if --save
  │
  ▼
VLM (any OpenAI-compatible vision model)
  OpenRouter / Ollama / custom endpoint
```

---

## License

MIT
