"""Dictation: offline voice typing for Steam's on-screen keyboard, plus a hotkey.

Recording uses pw-record; speech-to-text uses whisper.cpp (whisper-cli + ggml-base.en).
Key watching and virtual-keyboard typing run in helper.py under the system Python,
which has python-evdev (Decky's bundled Python doesn't).
"""
import asyncio
import json
import os
import re
import shutil
import time
import wave

import decky

HOME = "/home/deck"
DATA = os.path.join(HOME, ".local/share/decky-dictation")
WHISPER_BIN = os.path.join(DATA, "whisper-cli")
WHISPER_MODEL = os.path.join(DATA, "ggml-base.en.bin")
CLAUDE_WHISPER = os.path.join(HOME, ".local/share/claude-deck/whisper")
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin"
WAV = os.path.join(DATA, "recording.wav")
SETTINGS = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")
MAX_SECONDS = 120
SOUNDS = {
    "start": ["/usr/share/sounds/ocean/stereo/outcome-success.oga",
              "/usr/share/sounds/freedesktop/stereo/message-new-instant.oga"],
    "stop": ["/usr/share/sounds/freedesktop/stereo/message.oga",
             "/usr/share/sounds/ocean/stereo/message-sent-instant.oga"],
}
DEFAULTS = {"hotkey_enabled": True, "hotkey": "KEY_F23", "trailing_space": True, "sounds": True}


def clean_env():
    env = {k: v for k, v in os.environ.items() if k not in ("LD_LIBRARY_PATH", "PYTHONHOME", "PYTHONPATH")}
    env.update({"HOME": HOME, "PATH": f"{HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin",
                "XDG_RUNTIME_DIR": "/run/user/1000",
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"})
    return env


def game_mode():
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    if b"start-gamescope-session" in f.read():
                        return True
            except OSError:
                pass
    return False


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS) as f:
            s.update(json.load(f))
    except (OSError, ValueError):
        pass
    return s


class Plugin:
    state = "idle"          # idle | recording | transcribing
    source = None           # "osk" | "hotkey" | "panel"
    rec = None
    rec_started = 0.0
    helper = None
    settings = None
    setup_error = None
    tasks = []

    # ---------- lifecycle ----------
    async def _main(self):
        self.settings = load_settings()
        os.makedirs(DATA, exist_ok=True)
        self.tasks = [asyncio.create_task(self._ensure_whisper()),
                      asyncio.create_task(self._helper_loop())]
        decky.logger.info("Dictation loaded")

    async def _unload(self):
        for t in self.tasks:
            t.cancel()
        await self._kill_rec()
        if self.helper and self.helper.returncode is None:
            self.helper.terminate()
        decky.logger.info("Dictation unloaded")

    async def _ensure_whisper(self):
        try:
            for name, dst in (("whisper-cli", WHISPER_BIN), ("ggml-base.en.bin", WHISPER_MODEL)):
                src = os.path.join(CLAUDE_WHISPER, name)
                if not os.path.exists(dst) and os.path.exists(src):
                    await asyncio.to_thread(shutil.copy2, src, dst)
            if not os.path.exists(WHISPER_MODEL):
                p = await asyncio.create_subprocess_exec(
                    "curl", "-fsSL", "-o", WHISPER_MODEL + ".part", MODEL_URL, env=clean_env())
                if await p.wait() == 0:
                    os.replace(WHISPER_MODEL + ".part", WHISPER_MODEL)
            if not os.path.exists(WHISPER_BIN):
                self.setup_error = "whisper-cli is missing (set up voice input in the Claude plugin once, or put whisper-cli in ~/.local/share/decky-dictation)"
            elif not os.path.exists(WHISPER_MODEL):
                self.setup_error = "Couldn't download the speech model; check the internet connection"
            else:
                os.chmod(WHISPER_BIN, 0o755)
                self.setup_error = None
        except Exception as ex:
            self.setup_error = f"Setup failed: {ex}"
        if self.setup_error:
            decky.logger.error(self.setup_error)

    # ---------- input helper (hotkey + typing) ----------
    async def _helper_loop(self):
        helper_py = os.path.join(decky.DECKY_PLUGIN_DIR, "helper.py")
        while True:
            try:
                self.helper = await asyncio.create_subprocess_exec(
                    "/usr/bin/python3", "-I", helper_py, self.settings.get("hotkey", "KEY_F23"),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, env=clean_env())
                while True:
                    line = await self.helper.stdout.readline()
                    if not line:
                        break
                    if line.strip() == b"HOTKEY" and self.settings.get("hotkey_enabled", True):
                        asyncio.create_task(self._hotkey())
                err = (await self.helper.stderr.read()).decode(errors="replace").strip()
                decky.logger.warning(f"helper exited: {err[-400:]}")
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                decky.logger.error(f"helper failed: {ex}")
            await asyncio.sleep(5)

    async def _type(self, text):
        if self.helper and self.helper.returncode is None:
            self.helper.stdin.write((json.dumps({"type": text}) + "\n").encode())
            await self.helper.stdin.drain()

    async def _hotkey(self):
        if self.state == "idle":
            await self.start("hotkey")
        elif self.state == "recording":
            src = self.source
            text = await self.stop()
            if src == "hotkey" and text:
                await self._type(text)
            elif src != "hotkey":
                # Started from the on-screen keyboard: let it insert the text itself.
                await decky.emit("dictation_text", text)

    # ---------- feedback ----------
    async def _set_state(self, state, **extra):
        self.state = state
        await decky.emit("dictation_state", {"state": state, "source": self.source, **extra})

    async def _sound(self, which):
        if not self.settings.get("sounds", True):
            return
        path = next((p for p in SOUNDS[which] if os.path.exists(p)), None)
        if path:
            await asyncio.create_subprocess_exec("pw-play", path, env=clean_env(),
                                                 stdout=asyncio.subprocess.DEVNULL,
                                                 stderr=asyncio.subprocess.DEVNULL)

    async def _desktop_note(self, body):
        if not game_mode():
            await asyncio.create_subprocess_exec(
                "notify-send", "-a", "Dictation", "-t", "2500", "-h", "string:x-canonical-private-synchronous:dictation",
                "Dictation", body, env=clean_env(),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)

    # ---------- recording ----------
    async def _kill_rec(self):
        rec, self.rec = self.rec, None
        if rec and rec.returncode is None:
            rec.terminate()
            await rec.wait()
            return True
        return False

    async def _watchdog(self, started):
        await asyncio.sleep(MAX_SECONDS)
        if self.state == "recording" and self.rec_started == started:
            await self._hotkey()

    async def start(self, source="osk"):
        if self.setup_error:
            await self._ensure_whisper()
            if self.setup_error:
                await decky.emit("dictation_state", {"state": "error", "error": self.setup_error})
                return {"ok": False, "error": self.setup_error}
        if self.state != "idle":
            return {"ok": False, "error": f"busy ({self.state})"}
        self.source = source
        self.rec = await asyncio.create_subprocess_exec(
            "pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", WAV,
            env=clean_env(), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        self.rec_started = time.time()
        asyncio.create_task(self._watchdog(self.rec_started))
        await self._sound("start")
        await self._set_state("recording")
        await self._desktop_note("Listening… press the key again to stop")
        return {"ok": True}

    async def stop(self):
        """Stop recording and return the transcript ("" if nothing was heard)."""
        if self.state != "recording":
            return ""
        await self._kill_rec()
        await self._sound("stop")
        await self._set_state("transcribing")
        try:
            text = await self._transcribe()
        except Exception as ex:
            decky.logger.error(f"transcribe failed: {ex}")
            await self._set_state("idle", error=str(ex))
            return ""
        if text and self.settings.get("trailing_space", True):
            text += " "
        await self._set_state("idle", text=text)
        await self._desktop_note(f"“{text.strip()}”" if text.strip() else "Didn't catch anything")
        return text

    async def cancel(self):
        await self._kill_rec()
        if self.state != "idle":
            await self._set_state("idle")
        return True

    async def _transcribe(self):
        try:
            with wave.open(WAV) as w:
                secs = w.getnframes() / w.getframerate()
        except Exception:
            secs = 0
        if secs < 0.3:
            return ""
        # Size whisper's audio context to the clip: much faster for short dictation.
        ctx = min(1500, max(256, -(-int(secs * 50 + 128) // 64) * 64))
        p = await asyncio.create_subprocess_exec(
            WHISPER_BIN, "-m", WHISPER_MODEL, "-f", WAV, "-l", "en", "-nt", "-np",
            "-t", str(min(8, os.cpu_count() or 4)), "-ac", str(ctx),
            env=clean_env(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(p.communicate(), 120)
        except asyncio.TimeoutError:
            p.kill()
            raise RuntimeError("whisper timed out")
        if p.returncode != 0:
            raise RuntimeError(err.decode(errors="replace").strip()[-300:])
        text = " ".join(l.strip() for l in out.decode(errors="replace").splitlines() if l.strip())
        text = re.sub(r"\([^)]*\)|\[[^\]]*\]|\*[^*]*\*|♪+", " ", text)
        text = re.sub(r"\s{2,}", " ", text).strip()
        if text.strip(" .!?").lower() in ("", "you", "thank you", "thanks for watching", "bye"):
            return ""
        return text

    # ---------- frontend API ----------
    async def toggle(self, source="osk"):
        """Start, or stop and return the text. Returns {"state", "text"}."""
        if self.state == "idle":
            r = await self.start(source)
            return {"state": self.state, "text": "", **r}
        if self.state == "recording":
            text = await self.stop()
            return {"state": self.state, "text": text, "ok": True}
        return {"state": self.state, "text": "", "ok": False}

    async def get_status(self):
        return {"state": self.state, "source": self.source, "error": self.setup_error,
                "game_mode": game_mode(), "settings": self.settings,
                "helper": bool(self.helper and self.helper.returncode is None)}

    async def set_setting(self, key, value):
        if key in DEFAULTS:
            self.settings[key] = value
            os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
            with open(SETTINGS, "w") as f:
                json.dump(self.settings, f, indent=2)
            if key == "hotkey" and self.helper and self.helper.returncode is None:
                self.helper.terminate()  # restarts with the new key
        return self.settings

    async def type_text(self, text):
        await self._type(text)
        return True
