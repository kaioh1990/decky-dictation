# Dictation for Decky Loader

Offline voice typing for SteamOS. Adds a **microphone key** to Steam's on-screen
keyboard and a **hotkey** to start and stop dictation, with speech recognition
running locally through [whisper.cpp](https://github.com/ggerganov/whisper.cpp).
Nothing is sent to the cloud.

## Features

- **Mic key on the on-screen keyboard.** It sits next to Paste. Tap it, speak,
  tap it again, and the text is typed into whatever field the keyboard is
  typing into (games, Steam's own search boxes, browsers).
- **Hotkey.** Press it to start listening and press it again to type what you
  said. It works in Game Mode and on the KDE desktop, even when the on-screen
  keyboard is closed. The default is `KEY_F23`, which the Copilot key on recent
  laptop keyboards sends (Meta+Shift+F23, for example on the ROG Flow Z13 2025).
- **Feedback.** Start/stop sounds, the key turns red while listening, a toast
  in Game Mode and a notification on the desktop.
- **Quick Access panel** with a test button and settings (hotkey on/off,
  sounds, trailing space).

## Requirements

- Decky Loader 3.x
- `pw-record` (PipeWire) and the system `python3` with `python-evdev`, both
  shipped with SteamOS
- Read access to `/dev/input/event*` and `/dev/uinput` for the desktop user,
  which SteamOS grants to the logged-in seat
- `whisper-cli` and `ggml-base.en.bin` in `~/.local/share/decky-dictation/`.
  The plugin copies them from the Claude Decky plugin if it's installed, and
  downloads the model from Hugging Face otherwise. You need to provide
  `whisper-cli` yourself (build whisper.cpp, or copy the binary in).

## Install

There's no build step: `dist/index.js` is hand-written. Zip the folder as
`Dictation/` and install it from Decky's **Developer → Install Plugin from
ZIP**.

## How it works

- `dist/index.js` patches Steam's keyboard layouts to add a `VKDictate` key,
  and wraps `VirtualKeyboardManager.HandleVirtualKeyDown` to catch it. Text is
  inserted through the manager's `DispatchKeypress`, the same path the
  on-screen keys use.
- `main.py` records with `pw-record` and transcribes with `whisper-cli`.
- `helper.py` runs under the system Python. It watches keyboards for the
  hotkey and types hotkey dictation through a uinput virtual keyboard.

## Settings

Stored in `~/homebrew/settings/Dictation/settings.json`: `hotkey_enabled`,
`hotkey` (an evdev key name such as `KEY_F23`), `sounds`, `trailing_space`.

## License

MIT
