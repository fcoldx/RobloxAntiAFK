# Roblox Anti-AFK

A lightweight Windows desktop application designed to prevent being disconnected from Roblox due to the default 20-minute idle kick.

It works by momentarily focusing the Roblox window and sending a Shift keypress using hardware-level scan codes. The window focus switch takes only a fraction of a second and occurs at long intervals (every 10–15 minutes by default) to ensure zero disruption to your daily tasks, web browsing, or typing.

The focus switch uses `AttachThreadInput` to bypass the Windows foreground lock, so it works even when another app (browser, Discord, editor) currently owns the foreground. The previously active window is restored immediately after the keypress. If Windows still refuses the switch (e.g. the Start menu or a UAC prompt is open), no keystroke is sent to the wrong window and the attempt is retried after 30 seconds.

## Features

- **Safe Foreground Focus Simulation**: Momentarily brings Roblox to the foreground for 0.2 seconds to send a genuine keystroke, which is 100% effective against Byfron/Hyperion anti-cheat.
- **Harmless Keypress**: Sends Shift by default, which registers user activity without moving your character or camera.
- **Keybind Selector**: Rebind to any key the Minecraft way — click the key button, press a key, done. Esc cancels, and keys that would move you (Space, WASD, arrows) are labelled as such. Caps/Num/Scroll Lock are refused, since they would leave the keyboard switched.
- **Countdown Ring**: A live progress ring shows exactly how long until the next keypress, so you can tell at a glance that it is still working.
- **Adjustable Intervals**: Set the random interval range with +/− steppers, no code editing. The range is clamped to 1–19 minutes so it always stays under the 20-minute Roblox idle limit.
- **Send One Keypress Now**: Fire a single keypress on demand to confirm the setup works, without waiting for a cycle.
- **Colour-coded Activity Log**: Every keypress sent (green), every skip or failure (red), and the Roblox window title and handle currently targeted.
- **Persistent Log**: The activity log is written to `anti_afk.log` next to the script and reloaded on startup, so the full history survives restarts. "open file" opens it in your default editor.
- **Custom App Icon**: `icon.ico` (the countdown ring wrapped around the Shift glyph) is used for the window, dialogs and taskbar button, instead of the stock Python feather.
- **Native Dark Window**: Dark title bar and themed scrollbar, with Segoe UI Variable for text and a monospaced face for the timer so the digits do not jitter as they count down.

## Prerequisites

- Windows OS
- Python 3.x (Tkinter is included in the standard Windows installer)
- `pywin32` library

## Installation

1. Install the dependencies:

    ```bash
    pip install -r requirements.txt
    ```

    That is just `pywin32` (the Windows API bindings); Tkinter comes with Python.

2. Download `anti_afk_gui.py`, `roblox_window.py`, `requirements.txt` and `icon.ico` into the same directory. The app runs fine without the icon, just with the default Tk one.

## Usage

1. Open Roblox. Ensure it is in windowed mode (not fullscreen).
2. You can leave the Roblox window running in the background behind other applications. A minimized window is restored automatically when an action is due.
3. Run the app:
    ```bash
    python anti_afk_gui.py
    ```

4. To use a key other than Shift, click the key button next to **Key to send** and press the key you want. Rebinding is locked while the loop runs.
5. Set the interval range, then click **Start**. The first keypress is sent after ~10 seconds so you can verify it immediately; after that it follows your interval.
6. Click **Stop** to halt execution.

## Project layout

| File               | Purpose                                                          |
| ------------------ | ---------------------------------------------------------------- |
| `anti_afk_gui.py`  | Tk front end, activity log, and the timed worker thread          |
| `roblox_window.py` | Win32 layer: finding the Roblox window and delivering keystrokes |
| `icon.ico`         | App icon, in sizes 16-256                                        |
| `requirements.txt` | Runtime dependency (`pywin32`)                                   |

The log helpers have a built-in self-check:

```bash
python anti_afk_gui.py --selftest
```

## Credits

Based on and inspired by [DarRahman/roblox-anti-afk](https://github.com/DarRahman/roblox-anti-afk).

## License

This project is open-source and available under the MIT License.
