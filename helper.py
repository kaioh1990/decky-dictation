#!/usr/bin/env python3
"""Input helper for the Dictation plugin, run with the system Python (it has python-evdev).

- Watches keyboards for the hotkey and prints "HOTKEY" on stdout for each press.
- Reads JSON lines on stdin: {"type": "text"} types the text through a virtual keyboard
  (works in Game Mode and on the KDE desktop alike).
Usage: helper.py [KEY_NAME]   (default KEY_F23, which the Z13's Copilot key sends)
"""
import json, os, select, sys, time, unicodedata
import evdev
from evdev import ecodes as e, UInput

HOTKEY = getattr(e, sys.argv[1] if len(sys.argv) > 1 else "KEY_F23", e.KEY_F23)
UI_NAME = "Dictation virtual keyboard"

# US layout: char -> (keycode, shift)
KEYMAP = {" ": (e.KEY_SPACE, 0), "\n": (e.KEY_ENTER, 0), "\t": (e.KEY_TAB, 0)}
for c in "abcdefghijklmnopqrstuvwxyz":
    KEYMAP[c] = (getattr(e, "KEY_" + c.upper()), 0)
    KEYMAP[c.upper()] = (getattr(e, "KEY_" + c.upper()), 1)
for c, n in zip("1234567890", "1234567890"):
    KEYMAP[c] = (getattr(e, "KEY_" + n), 0)
for c, n in zip("!@#$%^&*()", "1234567890"):
    KEYMAP[c] = (getattr(e, "KEY_" + n), 1)
for plain, shifted, code in [("-", "_", e.KEY_MINUS), ("=", "+", e.KEY_EQUAL), ("[", "{", e.KEY_LEFTBRACE),
                             ("]", "}", e.KEY_RIGHTBRACE), ("\\", "|", e.KEY_BACKSLASH), (";", ":", e.KEY_SEMICOLON),
                             ("'", '"', e.KEY_APOSTROPHE), (",", "<", e.KEY_COMMA), (".", ">", e.KEY_DOT),
                             ("/", "?", e.KEY_SLASH), ("`", "~", e.KEY_GRAVE)]:
    KEYMAP[plain] = (code, 0)
    KEYMAP[shifted] = (code, 1)
FIXUPS = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
          "…": "...", " ": " "}


def ascii_text(t):
    t = "".join(FIXUPS.get(c, c) for c in t)
    return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()


ui = UInput({e.EV_KEY: sorted({k for k, _ in KEYMAP.values()} | {e.KEY_LEFTSHIFT})}, name=UI_NAME)


def type_text(text):
    for ch in ascii_text(text):
        k = KEYMAP.get(ch)
        if not k:
            continue
        code, shift = k
        if shift:
            ui.write(e.EV_KEY, e.KEY_LEFTSHIFT, 1)
        ui.write(e.EV_KEY, code, 1); ui.syn()
        ui.write(e.EV_KEY, code, 0)
        if shift:
            ui.write(e.EV_KEY, e.KEY_LEFTSHIFT, 0)
        ui.syn()
        time.sleep(0.004)


def scan(known):
    for p in evdev.list_devices():
        if p in known:
            continue
        try:
            d = evdev.InputDevice(p)
        except OSError:
            continue
        if d.name != UI_NAME and HOTKEY in d.capabilities().get(e.EV_KEY, []):
            known[p] = d
        else:
            d.close()


def main():
    known = {}
    scan(known)
    last_scan = time.time()
    stdin = sys.stdin.fileno()
    os.set_blocking(stdin, False)
    buf = b""
    while True:
        fds = {d.fd: p for p, d in known.items()}
        r, _, _ = select.select(list(fds) + [stdin], [], [], 3.0)
        for fd in r:
            if fd == stdin:
                chunk = os.read(stdin, 65536)
                if not chunk:
                    return  # plugin went away
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if "type" in msg:
                        type_text(msg["type"])
                continue
            p = fds[fd]
            try:
                for ev in known[p].read():
                    if ev.type == e.EV_KEY and ev.code == HOTKEY and ev.value == 1:
                        print("HOTKEY", flush=True)
            except OSError:  # keyboard detached
                known.pop(p).close()
        if time.time() - last_scan > 5:
            last_scan = time.time()
            scan(known)


if __name__ == "__main__":
    try:
        main()
    finally:
        ui.close()
