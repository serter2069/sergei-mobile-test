#!/usr/bin/env python3
"""
Mobile AI Agent — self-healing test agent for iOS and Android.
Uses idb (iOS) or adb (Android) for device control, VLM for vision.

Usage:
  python agent.py run "open Settings and turn on Dark Mode"
  python agent.py run "login" --save login_flow --platform android
  python agent.py replay login_flow
  python agent.py skills
"""
import os
import sys
import json
import base64
import shutil
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime

# ─── Device config ──────────────────────────────────────────────────────────

IOS_UDID   = os.environ.get("IOS_UDID", "")        # auto-detected if empty
SKILLS_DIR = Path(os.environ.get("SKILLS_DIR", "~/.mobile-test/skills")).expanduser()
SKILLS_DIR.mkdir(parents=True, exist_ok=True)

IDB_PATH = os.environ.get("IDB_PATH", shutil.which("idb") or "idb")
ADB_PATH = os.environ.get("ADB_PATH", shutil.which("adb") or "adb")

PLATFORM          = "ios"
ANDROID_SERIAL    = None
_ANDROID_LOGICAL_H = 896   # updated on each screenshot to match actual aspect ratio

client    = None
VLM_MODEL = None

# When MOBILE_TEST_MODEL=claude-* the Anthropic SDK is used directly.
# Set ANTHROPIC_API_KEY (already in env when running via Claude Code).
# Alternatively set MOBILE_TEST_MODEL=claude-haiku-4-5-20251001
_CLAUDE_MODE = False

# Path where every screenshot is also saved as a file (for agent vision via Read)
SCREENSHOT_PATH = os.environ.get("MOBILE_SCREENSHOT_PATH", "/tmp/phone_screen.png")


def set_platform(platform: str, serial: str = None):
    global PLATFORM, ANDROID_SERIAL
    PLATFORM = platform
    ANDROID_SERIAL = serial


def _action_delay(action_type: str):
    """Wait for UI to settle after an action."""
    import time
    delays = {
        "tap":        1.2,   # button press + possible screen transition
        "type":       0.5,   # keyboard input
        "swipe":      0.8,   # scroll animation
        "press_home": 1.5,   # home screen animation
        "press_back": 1.0,
    }
    time.sleep(delays.get(action_type, 0.8))


# ─── AI client ──────────────────────────────────────────────────────────────

def init_ai_client():
    """
    Configure AI client from environment variables.

    Claude mode (no external key needed — uses ANTHROPIC_API_KEY from env):
      MOBILE_TEST_MODEL=claude-haiku-4-5-20251001

    OpenAI-compatible (OpenRouter, Ollama, NVIDIA NIM, etc.):
      MOBILE_TEST_BASE=https://openrouter.ai/api/v1
      MOBILE_TEST_KEY=sk-or-v1-...
      MOBILE_TEST_MODEL=openai/gpt-4o
    """
    global client, VLM_MODEL, _CLAUDE_MODE
    if client is not None:
        return

    model = os.environ.get("MOBILE_TEST_MODEL", "")

    if model.startswith("claude-") or model == "claude":
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("Set ANTHROPIC_API_KEY to use Claude as vision model.")
        actual_model = model if model != "claude" else "claude-haiku-4-5-20251001"
        client    = anthropic.Anthropic(api_key=api_key)
        VLM_MODEL = actual_model
        _CLAUDE_MODE = True
        print(f"[ai] {actual_model} (Anthropic SDK)")
        return

    from openai import OpenAI
    base = os.environ.get("MOBILE_TEST_BASE")
    key  = os.environ.get("MOBILE_TEST_KEY")
    if not base or not key or not model:
        raise RuntimeError(
            "Set MOBILE_TEST_MODEL to a claude-* model (uses ANTHROPIC_API_KEY), or\n"
            "set MOBILE_TEST_BASE + MOBILE_TEST_KEY + MOBILE_TEST_MODEL for OpenAI-compatible.\n"
            "Example:\n"
            "  export MOBILE_TEST_MODEL=claude-haiku-4-5-20251001\n"
            "  export ANTHROPIC_API_KEY=sk-ant-..."
        )
    client    = OpenAI(base_url=base, api_key=key)
    VLM_MODEL = model
    print(f"[ai] {model} via {base}")


# ─── iOS transport (idb) ────────────────────────────────────────────────────

def _ios_udid() -> str:
    if IOS_UDID:
        return IOS_UDID
    result = subprocess.run(
        [IDB_PATH, "list-targets", "--json"],
        capture_output=True, text=True, timeout=10
    )
    for line in result.stdout.splitlines():
        try:
            t = json.loads(line)
            if t.get("type") == "device" and t.get("state") == "Booted":
                return t["udid"]
        except Exception:
            continue
    raise RuntimeError(
        "No iOS device found. Connect your iPhone and run: idb list-targets\n"
        "Or set IOS_UDID env var."
    )


def idb(cmd: list[str], timeout: int = 30) -> str:
    udid = _ios_udid()
    result = subprocess.run(
        [IDB_PATH] + cmd + ["--udid", udid],
        capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        raise RuntimeError(f"idb error: {result.stderr.strip()}")
    return result.stdout.strip()


_TUNNELD_ADDR = ("127.0.0.1", 49151)


def tunneld_running() -> bool:
    try:
        import urllib.request
        urllib.request.urlopen(f"http://{_TUNNELD_ADDR[0]}:{_TUNNELD_ADDR[1]}", timeout=1)
        return True
    except Exception:
        return False


async def _dvt_screenshot_async() -> bytes:
    from pymobiledevice3.tunneld.api import get_tunneld_device_by_udid
    from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
    from pymobiledevice3.services.dvt.instruments.screenshot import Screenshot

    udid = _ios_udid()
    rsd = await get_tunneld_device_by_udid(udid, _TUNNELD_ADDR)
    if rsd is None:
        raise RuntimeError("device not found via tunneld — is iPhone connected?")
    dvt = DvtProvider(rsd)
    async with Screenshot(dvt) as svc:
        return await svc.get_screenshot()


def _ensure_tunneld():
    if tunneld_running():
        return True
    print("[screenshot] Starting tunneld (requires sudo, enter password once)...")
    python = sys.executable
    subprocess.Popen(
        ["sudo", python, "-m", "pymobiledevice3", "remote", "tunneld"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    import time
    for _ in range(10):
        time.sleep(1)
        if tunneld_running():
            print("[screenshot] tunneld ready.")
            return True
    print("[screenshot] tunneld didn't start. Run: sudo python3 -m pymobiledevice3 remote tunneld")
    return False


def _ios_screenshot() -> str:
    import asyncio
    if not tunneld_running():
        _ensure_tunneld()
    if not tunneld_running():
        raise RuntimeError(
            "tunneld not running.\n"
            "  sudo python3 -m pymobiledevice3 remote tunneld\n"
            "See docs/setup-ios.md for auto-start setup."
        )
    data = asyncio.run(_dvt_screenshot_async())
    # Resize to 414px wide (matches logical coordinate space, keeps VLM payload small)
    try:
        from PIL import Image as PILImage
        import io
        img = PILImage.open(io.BytesIO(data))
        w, h = img.size
        target_w = 414
        target_h = int(h * target_w / w)
        img = img.resize((target_w, target_h), PILImage.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        png_bytes = buf.getvalue()
        # Always save to disk — lets Claude agents Read the file directly
        Path(SCREENSHOT_PATH).write_bytes(png_bytes)
        return base64.b64encode(png_bytes).decode()
    except ImportError:
        Path(SCREENSHOT_PATH).write_bytes(data)
        return base64.b64encode(data).decode()


# ─── Android transport (adb) ─────────────────────────────────────────────────

def adb(cmd: list[str], timeout: int = 30, binary: bool = False):
    base_cmd = [ADB_PATH]
    if ANDROID_SERIAL:
        base_cmd += ["-s", ANDROID_SERIAL]
    result = subprocess.run(base_cmd + cmd, capture_output=True, timeout=timeout)
    if result.returncode != 0:
        err = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"adb error: {err}")
    if binary:
        return result.stdout
    return result.stdout.decode(errors="replace").strip()


def android_devices() -> list[str]:
    result = subprocess.run(
        [ADB_PATH, "devices"], capture_output=True, text=True, timeout=10
    )
    devices = []
    for line in result.stdout.splitlines()[1:]:
        line = line.strip()
        if line and "\tdevice" in line:
            devices.append(line.split("\t")[0])
    return devices


def android_screen_size() -> tuple[int, int]:
    out = adb(["shell", "wm", "size"])
    for line in out.splitlines():
        if "size:" in line.lower():
            parts = line.split(":")[-1].strip().split("x")
            if len(parts) == 2:
                return int(parts[0]), int(parts[1])
    return 1080, 2400


def _android_screenshot() -> str:
    global _ANDROID_LOGICAL_H
    raw = adb(["exec-out", "screencap", "-p"], binary=True)
    if not raw or len(raw) < 100:
        raise RuntimeError("adb screencap returned empty data — is device connected?")
    from PIL import Image as PILImage
    import io
    img = PILImage.open(io.BytesIO(raw))
    w, h = img.size
    target_w = 414
    target_h = int(h * target_w / w)
    _ANDROID_LOGICAL_H = target_h          # store actual height for coordinate scaling
    img = img.resize((target_w, target_h), PILImage.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ─── Platform-dispatched actions ─────────────────────────────────────────────

def screenshot() -> str:
    """Returns base64-encoded PNG screenshot of device screen."""
    if PLATFORM == "android":
        return _android_screenshot()
    return _ios_screenshot()


def _android_scale() -> tuple[float, float]:
    """Scale factors from logical (414×_ANDROID_LOGICAL_H) → physical pixels."""
    sw, sh = android_screen_size()
    return sw / 414, sh / _ANDROID_LOGICAL_H


_MAESTRO_PORT = int(os.environ.get("MAESTRO_DRIVER_PORT", "7001"))


def _maestro(path: str, payload: dict, timeout: int = 10):
    """Send command to Maestro iOS driver HTTP server (port 7001)."""
    import urllib.request, json as _json
    data = _json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{_MAESTRO_PORT}/{path}",
        data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception as e:
        raise RuntimeError(f"Maestro driver error ({path}): {e}")


def _maestro_available() -> bool:
    try:
        import urllib.request
        urllib.request.urlopen(f"http://127.0.0.1:{_MAESTRO_PORT}/status", timeout=1)
        return True
    except Exception:
        return False


def tap(x: int, y: int):
    if PLATFORM == "android":
        sx, sy = _android_scale()
        adb(["shell", "input", "tap", str(int(x * sx)), str(int(y * sy))])
    elif _maestro_available():
        _maestro("touch", {"x": float(x), "y": float(y)})
    else:
        idb(["ui", "tap", str(x), str(y)])


def type_text(text: str):
    if PLATFORM == "android":
        # adb shell input text can't handle most special chars — use clipboard paste instead
        _android_type_safe(text)
    elif _maestro_available():
        _maestro("inputText", {"text": text, "appIds": []})
    else:
        idb(["ui", "text", text])


def _android_type_safe(text: str):
    """
    Type text on Android without special-char escaping issues.
    Uses ADB key events for ASCII, clipboard for complex text.
    """
    # Simple ASCII with no shell-special chars: use input text directly
    safe_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    if all(c in safe_chars or c == ' ' for c in text):
        escaped = text.replace(" ", "%s")
        adb(["shell", "input", "text", escaped])
        return

    # Complex text: push to clipboard via content provider, then paste
    # Works on Android 7+ without root
    import subprocess as _sp
    _sp.run(
        [ADB_PATH] + (["-s", ANDROID_SERIAL] if ANDROID_SERIAL else []) +
        ["shell", "am", "broadcast", "-a", "clipper.set", "-e", "text", text],
        capture_output=True, timeout=10
    )
    # If clipper not available, fall back to char-by-char with escaping
    import time
    time.sleep(0.3)
    # Try paste (Ctrl+V via key events: 279 = KEYCODE_PASTE)
    adb(["shell", "input", "keyevent", "279"])


def swipe(x1: int, y1: int, x2: int, y2: int, duration: float = 0.3):
    if PLATFORM == "android":
        sx, sy = _android_scale()
        ms = int(duration * 1000)
        adb(["shell", "input", "swipe",
             str(int(x1 * sx)), str(int(y1 * sy)),
             str(int(x2 * sx)), str(int(y2 * sy)), str(ms)])
    elif _maestro_available():
        _maestro("swipe", {
            "startX": float(x1), "startY": float(y1),
            "endX": float(x2), "endY": float(y2),
            "duration": float(duration)  # seconds
        })
    else:
        idb(["ui", "swipe",
             "--x", str(x1), "--y", str(y1),
             "--to-x", str(x2), "--to-y", str(y2),
             "--duration", str(duration)])


def press_home():
    if PLATFORM == "android":
        adb(["shell", "input", "keyevent", "3"])
    elif _maestro_available():
        _maestro("pressButton", {"button": "home"})
    else:
        idb(["ui", "key", "--key", "HOME"])


def press_back():
    """Android back button (keyevent 4)."""
    if PLATFORM == "android":
        adb(["shell", "input", "keyevent", "4"])


def launch_app(package_or_bundle: str):
    if PLATFORM == "android":
        adb(["shell", "monkey", "-p", package_or_bundle,
             "-c", "android.intent.category.LAUNCHER", "1"])
    else:
        idb(["launch", package_or_bundle])


# ─── WDA (iOS accessibility tree) ───────────────────────────────────────────

WDA_URL = os.environ.get("WDA_URL", "http://localhost:8100")


def wda_available() -> bool:
    if PLATFORM != "ios":
        return False
    try:
        import urllib.request
        urllib.request.urlopen(f"{WDA_URL}/status", timeout=1)
        return True
    except Exception:
        return False


def get_accessibility_tree() -> str:
    import urllib.request, json as _json
    r = urllib.request.urlopen(f"{WDA_URL}/source", timeout=5)
    data = _json.loads(r.read())
    return _json.dumps(data.get("value", data), indent=2)[:8000]


# ─── VLM reasoning ──────────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    coords = (
        "Use logical coordinates 414×896 (agent scales to device pixels automatically)."
        if PLATFORM == "android"
        else "iPhone XR screen: 414×896 logical points."
    )
    return f"""You are a mobile UI test agent controlling a {PLATFORM.upper()} device via screenshots.
Determine the NEXT SINGLE ACTION to accomplish the goal.

Respond with JSON ONLY (no markdown):
{{"action": "tap", "x": 100, "y": 200, "reason": "tap Login button"}}
{{"action": "type", "text": "hello", "reason": "enter username"}}
{{"action": "swipe", "x1": 200, "y1": 600, "x2": 200, "y2": 200, "reason": "scroll down"}}
{{"action": "press_home", "reason": "go home"}}
{{"action": "press_back", "reason": "go back (Android)"}}
{{"action": "done", "reason": "goal accomplished"}}
{{"action": "failed", "reason": "impossible because..."}}

Rules:
- Look EXACTLY at the current screen, don't assume previous state
- {coords}
- If UI changed — adapt to new coordinates
- Return ONLY valid JSON"""


A11Y_SYSTEM_PROMPT = """You are an iOS UI test agent. You receive a JSON accessibility tree.
Each element has: type, label, value, enabled, visible, x, y, width, height.
Respond with JSON only (no markdown):
{"action": "tap", "x": 207, "y": 448, "reason": "tap Login"}
{"action": "type", "text": "hello", "reason": "enter text"}
{"action": "swipe", "x1": 200, "y1": 600, "x2": 200, "y2": 200, "reason": "scroll"}
{"action": "press_home", "reason": "go home"}
{"action": "done", "reason": "accomplished"}
{"action": "failed", "reason": "why"}
Coordinates are iPhone logical points (414×896)."""


def _parse_action(text: str) -> dict:
    import re
    # Strip markdown code fences
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.lstrip("json").strip()
            if part.startswith("{"):
                text = part
                break
    text = text.strip()
    if not text:
        return {"action": "failed", "reason": "VLM returned empty response"}
    # If not valid JSON directly, try to extract first {...} block
    if not text.startswith("{"):
        m = re.search(r'\{[^{}]+\}', text, re.DOTALL)
        if m:
            text = m.group(0)
        else:
            return {"action": "failed", "reason": f"VLM returned non-JSON: {text[:80]}"}
    action = json.loads(text)
    # Normalize coordinates: some models return 0-1 floats instead of pixel ints
    for key in ("x", "y", "x1", "y1", "x2", "y2"):
        if key in action and isinstance(action[key], float) and action[key] <= 1.0:
            action[key] = int(action[key] * (896 if "y" in key else 414))
    return action


def ask_vlm(goal: str, screenshot_b64: str, history: list) -> dict:
    history_text = ""
    if history:
        recent = [h for h in history[-5:] if h["action"] != "error"]
        if recent:
            history_text = "\n\nPrevious actions:\n" + "\n".join(
                f"- {h['action']}: {h.get('reason', '')}" for h in recent
            )
    prompt = f"GOAL: {goal}{history_text}\n\nNext action?"

    if _CLAUDE_MODE:
        # Anthropic SDK — native multimodal, no external endpoint needed
        import anthropic
        response = client.messages.create(
            model=VLM_MODEL,
            max_tokens=300,
            system=_build_system_prompt(),
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/png", "data": screenshot_b64
                }},
                {"type": "text", "text": prompt},
            ]}]
        )
        return _parse_action(response.content[0].text.strip())

    # OpenAI-compatible (OpenRouter, Ollama, NVIDIA NIM, etc.)
    messages = [
        {"role": "system", "content": _build_system_prompt()},
        {"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{screenshot_b64}", "detail": "low"}}
        ]}
    ]
    response = client.chat.completions.create(
        model=VLM_MODEL, messages=messages, max_tokens=200, temperature=0
    )
    return _parse_action(response.choices[0].message.content.strip())


def ask_llm_a11y(goal: str, tree: str, history: list) -> dict:
    history_text = ""
    if history:
        recent = [h for h in history[-5:] if h["action"] != "error"]
        if recent:
            history_text = "\n\nPrevious actions:\n" + "\n".join(
                f"- {h['action']}: {h.get('reason', '')}" for h in recent
            )
    messages = [
        {"role": "system", "content": A11Y_SYSTEM_PROMPT},
        {"role": "user", "content":
            f"GOAL: {goal}{history_text}\n\nUI tree:\n{tree}\n\nNext action?"}
    ]
    response = client.chat.completions.create(
        model=VLM_MODEL, messages=messages, max_tokens=200, temperature=0
    )
    return _parse_action(response.choices[0].message.content.strip())


# ─── Action executor ────────────────────────────────────────────────────────

def execute_action(action: dict):
    a = action["action"]
    if a == "tap":
        tap(int(action["x"]), int(action["y"]))
    elif a == "type":
        type_text(action["text"])
    elif a == "swipe":
        swipe(int(action["x1"]), int(action["y1"]),
              int(action["x2"]), int(action["y2"]))
    elif a == "press_home":
        press_home()
    elif a == "press_back":
        press_back()
    elif a in ("done", "failed"):
        return
    else:
        raise ValueError(f"Unknown action: {a}")
    _action_delay(a)


# ─── Skills system ──────────────────────────────────────────────────────────

def save_skill(name: str, goal: str, steps: list):
    skill = {
        "name": name, "goal": goal, "platform": PLATFORM,
        "steps": steps, "saved_at": datetime.now().isoformat(), "uses": 0,
    }
    (SKILLS_DIR / f"{name}.json").write_text(
        json.dumps(skill, indent=2, ensure_ascii=False)
    )
    print(f"[skill saved] {SKILLS_DIR / f'{name}.json'}")


def load_skill(name: str) -> dict | None:
    path = SKILLS_DIR / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else None


def list_skills() -> list[str]:
    return [p.stem for p in sorted(SKILLS_DIR.glob("*.json"))]


def replay_skill(name: str) -> bool:
    skill = load_skill(name)
    if not skill:
        print(f"[skill not found] {name}")
        return False
    if skill.get("platform", "ios") != PLATFORM:
        print(f"[warn] Skill was recorded on {skill.get('platform')}, running on {PLATFORM}")
    print(f"[replaying] {name}: {skill['goal']}")
    for i, step in enumerate(skill["steps"], 1):
        print(f"  [{i}] {step['action']}: {step.get('reason', '')}")
        try:
            execute_action(step)
        except Exception as e:
            print(f"  [error] {e}")
            return False
    skill["uses"] += 1
    (SKILLS_DIR / f"{name}.json").write_text(json.dumps(skill, indent=2, ensure_ascii=False))
    return True


def smart_run(skill_name: str, goal: str, max_steps: int = 20) -> bool:
    if load_skill(skill_name):
        print(f"[smart] Trying cached skill: {skill_name}")
        if replay_skill(skill_name):
            return True
        print("[smart] Skill outdated — re-learning...")
    return run(goal, max_steps=max_steps, save_as=skill_name)


# ─── Main agent loop ────────────────────────────────────────────────────────

def run(goal: str, max_steps: int = 20, save_as: str = None, mode: str = "auto") -> bool:
    """
    Run AI agent to accomplish a goal on the connected device.

    Args:
        goal:      Natural language description, e.g. "open Settings and enable Dark Mode"
        max_steps: Max actions before giving up (default 20)
        save_as:   Save successful run as a reusable skill
        mode:      "auto" | "vision" | "a11y"
    """
    init_ai_client()

    use_a11y = (mode == "a11y") or (mode == "auto" and wda_available())
    if use_a11y:
        print(f"\n[agent] Mode: accessibility tree (WDA)")
    elif PLATFORM == "android":
        print(f"\n[agent] Mode: vision (adb screencap — silent)")
    elif tunneld_running():
        print(f"\n[agent] Mode: vision (DVT — silent)")
    else:
        print(f"\n[agent] Mode: vision (DVT via tunneld)")

    print(f"[agent] Platform: {PLATFORM.upper()}")
    print(f"[agent] Goal: {goal}")
    if save_as:
        print(f"[agent] Saving as: {save_as}")
    history = []

    for step in range(max_steps):
        print(f"\n[step {step + 1}/{max_steps}]", end=" ", flush=True)

        if use_a11y:
            try:
                tree = get_accessibility_tree()
                action = ask_llm_a11y(goal, tree, history)
            except Exception as e:
                print(f"[a11y failed → vision] {e}")
                use_a11y = False
                screen = screenshot()
                action = ask_vlm(goal, screen, history)
        else:
            screen = screenshot()
            action = ask_vlm(goal, screen, history)

        print(f"{action['action']}: {action.get('reason', '')}")
        history.append(action)

        if action["action"] == "done":
            print(f"\n[done] {action['reason']}")
            if save_as:
                steps = [h for h in history if h["action"] not in ("done", "failed", "error")]
                save_skill(save_as, goal, steps)
            return True

        if action["action"] == "failed":
            print(f"\n[failed] {action['reason']}")
            return False

        try:
            execute_action(action)
        except Exception as e:
            print(f"  [exec error] {e}")
            history.append({"action": "error", "reason": str(e)})

    print(f"\n[timeout] Reached max_steps={max_steps}")
    return False


# ─── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    if "--platform" in args:
        idx = args.index("--platform")
        set_platform(args[idx + 1])
        args = args[:idx] + args[idx + 2:]

    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]

    if cmd == "run":
        goal = args[1]
        save_as = args[args.index("--save") + 1] if "--save" in args else None
        sys.exit(0 if run(goal, save_as=save_as) else 1)
    elif cmd == "smart":
        sys.exit(0 if smart_run(args[1], args[2]) else 1)
    elif cmd == "replay":
        sys.exit(0 if replay_skill(args[1]) else 1)
    elif cmd == "skills":
        skills = list_skills()
        if skills:
            for name in skills:
                s = load_skill(name)
                print(f"  [{s.get('platform','ios')}] {name} — {s['goal']} (×{s['uses']})")
        else:
            print("No skills saved yet.")
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
