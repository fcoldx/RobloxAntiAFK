"""Tk front end: UI, activity log, and the timed worker that triggers keystrokes.

All Win32 work lives in roblox_window.py.
Run `python anti_afk_gui.py --selftest` for the log helpers' self-check.
"""

import ctypes
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont
from tkinter import messagebox
import os
import queue
import sys
import threading
import time
import random
import pywintypes
from pathlib import Path
from datetime import datetime

import roblox_window as rw

FIRST_DELAY = 10            # first action is quick so it is easy to verify
DEFAULT_MIN = 10            # minutes
DEFAULT_MAX = 15
MIN_MINUTES = 1
MAX_MINUTES = 19            # Roblox kicks at 20
RETRY_DELAY = 30            # wait before retrying after a blocked focus switch
UI_POLL_MS = 60             # how often the UI thread drains worker updates
MAX_LOG_LINES = 2000        # pane trim point; the file on disk keeps everything
HISTORY_BYTES = 60_000      # how much of the log file is reloaded on startup
ACTIVITY_LOG = Path(__file__).with_name("anti_afk.log")
ICON = Path(__file__).with_name("icon.ico")        # built by make_icon.py
APP_ID = "bludx.robloxantiafk"                     # taskbar identity

# Palette
BG = "#0e0f13"
CARD = "#16181d"
FIELD = "#0b0c0f"
LINE = "#23262d"
TRACK = "#272b34"          # ring track, a touch brighter than LINE
FG = "#eef1f6"
MUTED = "#8b93a1"
GREEN = "#22c55e"
RED = "#f87171"
AMBER = "#fbbf24"

RING = 230                  # hero canvas size
# Tk on Windows puts the Windows virtual-key code straight into
# event.keycode, so a captured key needs no lookup table -- only a nicer name
# than Tk's keysym for the handful of keys whose keysym reads badly.
# Windows reports one generic code for either Shift, Ctrl or Alt, so the
# sides are not named apart here -- the name would not match what is sent.
KEY_NAMES = {
    "Shift_L": "Shift", "Shift_R": "Shift",
    "Control_L": "Ctrl", "Control_R": "Ctrl",
    "Alt_L": "Alt", "Alt_R": "Alt",
    "space": "Space", "Prior": "Page Up", "Next": "Page Down",
    "Up": "Arrow Up", "Down": "Arrow Down",
    "Left": "Arrow Left", "Right": "Arrow Right",
    "Return": "Enter", "BackSpace": "Backspace", "Tab": "Tab",
    "Delete": "Delete", "Insert": "Insert", "Home": "Home", "End": "End",
}
# Bindable, but they move the character or camera, so the UI says so.
KEY_WARNINGS = {
    0x20: "also jumps", 0x57: "also walks forward", 0x41: "also walks left",
    0x53: "also walks back", 0x44: "also walks right",
    0x26: "also moves you", 0x28: "also moves you",
    0x25: "also turns you", 0x27: "also turns you",
}
# These flip keyboard state and would leave it flipped, so they are refused.
LOCK_KEYS = {0x14: "Caps Lock", 0x90: "Num Lock", 0x91: "Scroll Lock"}

RING_PAD = 18
RING_WIDTH = 13


def key_name(keysym):
    """A readable name for a Tk keysym: "Shift_L" -> "Left Shift", "a" -> "A"."""
    if keysym in KEY_NAMES:
        return KEY_NAMES[keysym]
    if keysym.startswith("KP_"):
        return "Numpad " + keysym[3:]
    if len(keysym) == 1:
        return keysym.upper()
    return keysym


def classify(line):
    """Tag name for a log line, so sent / failed / plain lines read apart."""
    low = line.lower()
    if "failed" in low or "skipped" in low or "not found" in low or "retrying" in low:
        return "bad"
    if "simulated" in low:
        return "good"
    return "info"


def tail(path, max_bytes=HISTORY_BYTES):
    """Last max_bytes of a text file as str; "" if it is not readable yet."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - max_bytes))
            data = f.read()
    except OSError:
        return ""
    text = data.decode("utf-8", "replace")
    # A mid-character or mid-line start would show a broken first line.
    return text.split("\n", 1)[1] if len(data) == max_bytes and "\n" in text else text


class AntiAFKApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Roblox Anti-AFK")
        self.root.geometry("470x730")
        self.root.minsize(430, 620)
        self.root.configure(bg=BG)

        # State
        self.is_running = False
        self.hwnd = None
        self.worker_thread = None
        self.run_token = 0
        self.stop_event = None
        self.ui_queue = queue.Queue()
        self.interval = (DEFAULT_MIN * 60, DEFAULT_MAX * 60)   # snapshot for the worker
        self.key_name, self.key_code = "Shift", rw.VK_SHIFT
        self.capturing = False      # True while waiting for a key to be pressed

        self.ui_font, self.mono_font = self._pick_fonts()
        self._set_icon()
        self._dark_titlebar()
        self._setup_scrollbar_style()
        self._build_ui()
        self.root.after(UI_POLL_MS, self._drain_ui)

    # ---------- fonts ----------

    def _pick_fonts(self):
        """(ui, mono) family names, best available first.

        Segoe UI Variable ships with Windows 11 and is noticeably cleaner at
        small sizes than plain Segoe UI; the timer uses a monospaced family so
        the digits do not shift width as they count down.
        """
        families = set(tkfont.families(self.root))

        def pick(*names):
            return next((n for n in names if n in families), "TkDefaultFont")

        ui = pick("Segoe UI Variable Text", "Segoe UI", "Inter", "Arial")
        mono = pick("Cascadia Mono", "Consolas", "Courier New")
        return ui, mono

    def f(self, size, weight="normal"):
        return (self.ui_font, size, weight)

    def m(self, size, weight="normal"):
        return (self.mono_font, size, weight)

    # ---------- chrome ----------

    def _set_icon(self):
        """Use icon.ico for the window, title bar and dialogs, if it is there.

        `default=` applies it to message boxes too. A missing or unreadable
        icon is cosmetic only, so fall back to the stock Tk feather.
        """
        if not ICON.exists():
            return
        try:
            self.root.iconbitmap(default=str(ICON))
        except tk.TclError:
            pass

    def _dark_titlebar(self):
        """Ask DWM for a dark title bar so it matches the window.

        Attribute 20 is DWMWA_USE_IMMERSIVE_DARK_MODE on current Windows, 19
        on early Windows 10 builds; both are no-ops elsewhere, and the whole
        thing is cosmetic, so any failure is ignored.
        """
        try:
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            flag = ctypes.c_int(1)
            for attribute in (20, 19):
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(flag), ctypes.sizeof(flag)
                ) == 0:
                    break
            # the bar only repaints on the next map
            self.root.withdraw()
            self.root.deiconify()
        except Exception:
            pass

    def _setup_scrollbar_style(self):
        """Tk's own scrollbar stays light on Windows; ttk/clam can be themed."""
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Dark.Vertical.TScrollbar",
            background=LINE, troughcolor=FIELD, bordercolor=FIELD,
            lightcolor=LINE, darkcolor=LINE, arrowcolor=FIELD,
            gripcount=0, arrowsize=1, relief="flat", borderwidth=0,
        )
        style.map("Dark.Vertical.TScrollbar", background=[("active", MUTED)])

    # ---------- small widget builders ----------

    def _flat_button(self, parent, text, command, bg, fg, active, font, **kw):
        return tk.Button(
            parent, text=text, command=command, bg=bg, fg=fg,
            activebackground=active, activeforeground=fg, font=font,
            bd=0, relief="flat", highlightthickness=0, cursor="hand2", **kw
        )

    def _stepper(self, parent, var):
        """A compact -/value/+ control over an int-valued StringVar."""
        frame = tk.Frame(parent, bg=FIELD)

        def bump(step):
            value = int(var.get()) + step
            var.set(str(max(MIN_MINUTES, min(MAX_MINUTES, value))))

        self._flat_button(
            frame, "−", lambda: bump(-1), FIELD, MUTED, LINE, self.f(12), width=2
        ).pack(side="left", ipady=1)
        tk.Label(
            frame, textvariable=var, bg=FIELD, fg=FG, font=self.m(13, "bold"), width=3
        ).pack(side="left")
        self._flat_button(
            frame, "+", lambda: bump(1), FIELD, MUTED, LINE, self.f(12), width=2
        ).pack(side="left", ipady=1)
        return frame

    # ---------- layout ----------

    def _build_ui(self):
        pad = 26

        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=pad, pady=(20, 0))
        tk.Label(
            header, text="Roblox Anti-AFK", bg=BG, fg=FG, font=self.f(15, "bold")
        ).pack(side="left")
        self.dot = tk.Label(header, text="●", bg=BG, fg=RED, font=self.f(11))
        self.dot.pack(side="right", padx=(6, 0))
        self.state_label = tk.Label(header, text="Idle", bg=BG, fg=MUTED, font=self.f(10))
        self.state_label.pack(side="right")

        # Hero: countdown ring
        self.canvas = tk.Canvas(
            self.root, width=RING, height=RING, bg=BG, highlightthickness=0, bd=0
        )
        self.canvas.pack(pady=(14, 4))
        box = (RING_PAD, RING_PAD, RING - RING_PAD, RING - RING_PAD)
        self.canvas.create_oval(*box, outline=TRACK, width=RING_WIDTH)
        self.arc = self.canvas.create_arc(
            *box, start=90, extent=0, style="arc", width=RING_WIDTH, outline=GREEN
        )
        self.ring_time = self.canvas.create_text(
            RING / 2, RING / 2 - 8, text="--:--", fill=MUTED, font=self.m(34, "bold")
        )
        self.ring_sub = self.canvas.create_text(
            RING / 2, RING / 2 + 26, text="not running", fill=MUTED, font=self.f(9)
        )

        self.window_label = tk.Label(
            self.root, text="Roblox window: not detected", bg=BG, fg=MUTED, font=self.f(9)
        )
        self.window_label.pack(pady=(0, 16))

        # Interval
        card = tk.Frame(self.root, bg=CARD)
        card.pack(fill="x", padx=pad)
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=16, pady=(14, 10))
        tk.Label(
            inner, text="Keypress every", bg=CARD, fg=MUTED, font=self.f(10)
        ).pack(side="left")
        tk.Label(inner, text="min", bg=CARD, fg=MUTED, font=self.f(10)).pack(
            side="right", padx=(8, 0)
        )
        self.min_var = tk.StringVar(value=str(DEFAULT_MIN))
        self.max_var = tk.StringVar(value=str(DEFAULT_MAX))
        self._stepper(inner, self.max_var).pack(side="right")
        tk.Label(inner, text="–", bg=CARD, fg=MUTED, font=self.f(10)).pack(
            side="right", padx=8
        )
        self._stepper(inner, self.min_var).pack(side="right")

        key_row = tk.Frame(card, bg=CARD)
        key_row.pack(fill="x", padx=16, pady=(0, 2))
        tk.Label(
            key_row, text="Key to send", bg=CARD, fg=MUTED, font=self.f(10)
        ).pack(side="left")
        self.key_btn = self._flat_button(
            key_row, self.key_name, self._begin_capture,
            FIELD, FG, LINE, self.f(10, "bold"), width=16, pady=5,
        )
        self.key_btn.pack(side="right")
        self.key_hint = tk.Label(card, text="", bg=CARD, fg=MUTED, font=self.f(8))
        self.key_hint.pack(anchor="e", padx=16, pady=(0, 12))
        self._refresh_key_btn()
        # one permanent binding; the handler is inert unless a capture is open
        self.root.bind_all("<KeyPress>", self._capture_key, add="+")

        # Actions
        actions = tk.Frame(self.root, bg=BG)
        actions.pack(fill="x", padx=pad, pady=(12, 0))
        self.toggle_btn = self._flat_button(
            actions, "Start", self.toggle_anti_afk, GREEN, "#06260f", "#16a34a",
            self.f(11, "bold"), pady=10,
        )
        self.toggle_btn.pack(fill="x")
        self.test_btn = self._flat_button(
            actions, "Send one keypress now", self.test_now, BG, MUTED, CARD, self.f(9), pady=8
        )
        self.test_btn.pack(fill="x", pady=(6, 0))

        # Activity log
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=pad, pady=(18, 6))
        tk.Label(bar, text="Activity", bg=BG, fg=FG, font=self.f(10, "bold")).pack(side="left")
        self._flat_button(
            bar, "open file", self._open_activity_file, BG, MUTED, CARD, self.f(9)
        ).pack(side="right")
        self._flat_button(
            bar, "clear", self._clear_activity, BG, MUTED, CARD, self.f(9)
        ).pack(side="right", padx=(0, 10))

        wrap = tk.Frame(self.root, bg=FIELD)
        wrap.pack(fill="both", expand=True, padx=pad, pady=(0, 22))
        scroll = ttk.Scrollbar(wrap, orient="vertical", style="Dark.Vertical.TScrollbar")
        scroll.pack(side="right", fill="y", padx=(0, 3), pady=3)
        self.activity = tk.Text(
            wrap, bg=FIELD, fg=MUTED, insertbackground=FG, selectbackground=LINE,
            bd=0, highlightthickness=0, padx=12, pady=10, wrap="word",
            font=self.m(9), yscrollcommand=scroll.set, state="disabled",
        )
        self.activity.pack(side="left", fill="both", expand=True)
        scroll.config(command=self.activity.yview)
        self.activity.tag_config("good", foreground=GREEN)
        self.activity.tag_config("bad", foreground=RED)
        self.activity.tag_config("info", foreground=MUTED)

        self._load_history()
        self.add_log("Application ready.")
        self._refresh_window_label()

    # ---------- activity log ----------

    def add_log(self, text, persist=True):
        line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {text}"
        self._append(line + "\n")
        if persist:
            try:
                with open(ACTIVITY_LOG, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass    # a read-only folder must not take the app down

    def _append(self, content):
        self.activity.config(state="normal")
        for line in content.splitlines(keepends=True):
            self.activity.insert("end", line, classify(line))
        count = int(self.activity.index("end-1c").split(".")[0])
        if count > MAX_LOG_LINES:
            self.activity.delete("1.0", f"{count - MAX_LOG_LINES}.0")
        self.activity.config(state="disabled")
        self.activity.see("end")

    def _load_history(self):
        """Show the tail of earlier sessions so the pane is never blank."""
        text = tail(ACTIVITY_LOG)
        if text:
            self._append(text)

    def _clear_activity(self):
        self.activity.config(state="normal")
        self.activity.delete("1.0", "end")
        self.activity.config(state="disabled")

    def _open_activity_file(self):
        if not ACTIVITY_LOG.exists():
            messagebox.showinfo("Activity log", "Nothing has been logged yet.")
            return
        os.startfile(ACTIVITY_LOG)

    # ---------- control ----------

    def _refresh_window_label(self):
        self.hwnd = rw.resolve(self.hwnd)
        if self.hwnd:
            title = rw.win32gui.GetWindowText(self.hwnd) or "(untitled)"
            self.window_label.config(text=f"Roblox window: {title}  ·  0x{self.hwnd:X}")
        else:
            self.window_label.config(text="Roblox window: not detected")

    def _read_interval(self):
        """Interval bounds in seconds. The steppers cannot produce bad input,
        and crossed values are simply read as a range."""
        lo, hi = int(self.min_var.get()), int(self.max_var.get())
        return min(lo, hi) * 60, max(lo, hi) * 60

    def _refresh_key_btn(self):
        """Paint the button for the bound key, or for the capture in progress."""
        if self.capturing:
            self.key_btn.config(text="> press a key <", fg=AMBER, bg=LINE)
            self.key_hint.config(text="Esc cancels")
            return
        self.key_btn.config(text=self.key_name, fg=FG, bg=FIELD)
        note = KEY_WARNINGS.get(self.key_code)
        if self.is_running:
            self.key_hint.config(text="stop to rebind")
        else:
            self.key_hint.config(text=note or "click to rebind")

    def _begin_capture(self):
        """Arm the keybind button; the next keypress becomes the bound key."""
        if self.is_running:
            return
        self.capturing = not self.capturing     # clicking again cancels
        if self.capturing:
            # focus the window, NOT the button: a focused Tk button answers
            # Space and Enter by invoking itself, swallowing the capture.
            self.root.focus_set()
        self._refresh_key_btn()

    def _capture_key(self, event):
        """Bind whatever was pressed. Tk gives the virtual-key code directly."""
        if not self.capturing:
            return None
        self.capturing = False
        self._refresh_key_btn()
        if event.keysym == "Escape" or not event.keycode:
            return "break"
        if event.keycode in LOCK_KEYS:
            messagebox.showinfo(
                "Key not usable",
                f"{LOCK_KEYS[event.keycode]} toggles keyboard state, so it would "
                "leave the keyboard switched. Please pick another key.",
            )
            return "break"
        self.key_name, self.key_code = key_name(event.keysym), event.keycode
        self._refresh_key_btn()
        self.add_log(f"Key to send set to {self.key_name}.")
        return "break"

    def _set_state(self, label, color, sub):
        self.state_label.config(text=label, fg=color)
        self.dot.config(fg=color)
        self.canvas.itemconfig(self.ring_sub, text=sub)

    def toggle_anti_afk(self):
        if not self.is_running:
            self.hwnd = rw.find_window()
            if not self.hwnd:
                messagebox.showerror(
                    "Error", "Roblox window not found! Please open the game first."
                )
                return

            self.interval = self._read_interval()
            self.capturing = False
            self.is_running = True
            self._refresh_key_btn()     # rebinding is locked while a run is on
            self.run_token += 1
            self.stop_event = threading.Event()
            self._set_state("Active", GREEN, "until next keypress")
            self.toggle_btn.config(
                text="Stop", bg=RED, fg="#2b0b0b", activebackground="#ef4444"
            )
            self._refresh_window_label()
            self.add_log(
                f"Anti-AFK activated ({self.key_name} every "
                f"{self.interval[0] // 60}-{self.interval[1] // 60} min)."
            )

            self.worker_thread = threading.Thread(
                target=self.afk_worker, args=(self.run_token, self.stop_event), daemon=True
            )
            self.worker_thread.start()
        else:
            self.is_running = False
            self._refresh_key_btn()
            if self.stop_event:
                self.stop_event.set()  # stop this run, not the next one
            self._set_state("Idle", MUTED, "not running")
            self.dot.config(fg=RED)
            self.canvas.itemconfig(self.ring_time, text="--:--", fill=MUTED)
            self.canvas.itemconfig(self.arc, extent=0)
            self.toggle_btn.config(
                text="Start", bg=GREEN, fg="#06260f", activebackground="#16a34a"
            )
            self.add_log("Anti-AFK deactivated.")

    def test_now(self):
        """Send one keystroke right away, so a setup can be verified."""
        self._refresh_window_label()
        if not self.hwnd:
            messagebox.showerror("Error", "Roblox window not found! Please open the game first.")
            return
        if self.send_keypress(self.key_code):
            self.add_log(f"Manual test: activity simulated ({self.key_name} sent).")

    # ---------- worker ----------

    def send_keypress(self, key_code, stop_event=None):
        """One anti-AFK action. True only if a real keystroke was delivered."""
        if stop_event is not None and stop_event.is_set():
            return False
        self.hwnd = rw.resolve(self.hwnd)
        if not self.hwnd:
            self.log_async("Action failed: Roblox window not found.")
            return False
        try:
            if rw.send_key(self.hwnd, key_code):
                return True
            self.log_async("Action skipped: another window kept focus.")
            return False
        except pywintypes.error as e:
            self.log_async(f"Action failed: {e.func} -> {e.strerror} (code {e.winerror})")
            return False

    def _countdown(self, seconds, stop_event, run_token):
        """Deadline-based countdown (no drift). False if this run was stopped."""
        deadline = time.monotonic() + seconds
        while run_token == self.run_token and not stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._ui(self.update_timer_ui, int(remaining + 0.999), seconds, run_token)
            if stop_event.wait(min(1.0, remaining)):
                break
        return not stop_event.is_set() and run_token == self.run_token

    def afk_worker(self, run_token, stop_event):
        # Shift is the default because it does not affect character movement.
        delay = FIRST_DELAY
        while run_token == self.run_token and not stop_event.is_set():
            if not self._countdown(delay, stop_event, run_token):
                break
            delay = random.randint(*self.interval)

            self._ui(self.update_timer_ui, 0, 1, run_token)
            if self.send_keypress(self.key_code, stop_event):
                self.log_async(f"Activity simulated ({self.key_name} sent).")
            else:
                self.log_async(f"Retrying in {RETRY_DELAY}s.")
                if not self._countdown(RETRY_DELAY, stop_event, run_token):
                    break
                if self.send_keypress(self.key_code, stop_event):
                    self.log_async(f"Activity simulated ({self.key_name} sent).")

        self._ui(self.update_timer_ui, 0, 1, run_token)

    # ---------- UI plumbing ----------

    def _ui(self, fn, *args):
        """Queue a widget update for the main thread.

        The worker thread must never touch Tk widgets, and root.after called
        from another thread can be dropped silently, so go through a queue.
        """
        self.ui_queue.put((fn, args))

    def _drain_ui(self):
        while True:
            try:
                fn, args = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args)
            except tk.TclError:
                return  # window already closed; stop rescheduling
        try:
            self.root.after(UI_POLL_MS, self._drain_ui)
        except tk.TclError:
            pass

    def update_timer_ui(self, remaining, total=1, run_token=None):
        if run_token is not None and run_token != self.run_token:
            return  # update from a stale run
        if not self.is_running:
            self.canvas.itemconfig(self.ring_time, text="--:--", fill=MUTED)
            self.canvas.itemconfig(self.arc, extent=0)
            return
        if remaining > 0:
            self.canvas.itemconfig(
                self.ring_time, fill=FG,
                text=f"{remaining // 60:02d}:{remaining % 60:02d}",
            )
            self.canvas.itemconfig(
                self.arc, extent=-359.99 * min(1.0, remaining / max(total, 1)), outline=GREEN
            )
            self.canvas.itemconfig(self.ring_sub, text="until next keypress")
        else:
            self.canvas.itemconfig(self.ring_time, text="00:00", fill=AMBER)
            self.canvas.itemconfig(self.arc, extent=-359.99, outline=AMBER)
            self.canvas.itemconfig(self.ring_sub, text="sending keypress")

    def log_async(self, text):
        self._ui(self.add_log, text)


def demo():
    """Self-check for the log helpers (python anti_afk_gui.py --selftest)."""
    import tempfile

    assert classify("[t] Activity simulated (Shift key sent).") == "good"   # old logs
    assert classify("[t] Activity simulated (F15 sent).") == "good"
    assert classify("[t] Manual test: activity simulated (Space SENT).") == "good"
    assert classify("[t] Action failed: SetForegroundWindow -> denied") == "bad"
    assert classify("[t] Action skipped: another window kept focus.") == "bad"
    assert classify("[t] Action failed: Roblox window not found.") == "bad"
    assert classify("[t] Retrying in 30s.") == "bad"
    assert classify("[t] Anti-AFK activated (every 10-15 min).") == "info"

    assert key_name("Shift_L") == key_name("Shift_R") == "Shift"   # one VK
    assert key_name("Control_R") == "Ctrl"
    assert key_name("space") == "Space"
    assert key_name("a") == "A"
    assert key_name("F15") == "F15"
    assert key_name("KP_7") == "Numpad 7"
    assert key_name("Pause") == "Pause"        # unmapped keysyms pass through

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "a.log"
        assert tail(Path(d) / "missing.log") == ""          # unreadable -> empty
        p.write_bytes(b"one" + b"\n" + b"two" + b"\n")
        assert tail(p) == "one\ntwo\n"
        # an oversized file is cut back to whole lines only
        p.write_bytes(b"aaaa" + b"\n" + b"bbbb" + b"\n")
        assert tail(p, 7) == "bbbb\n", tail(p, 7)
        # a cut landing with no newline left still returns something printable
        p.write_bytes(b"x" * 20)
        assert tail(p, 5) == "xxxxx"

    print("self-check: ok")


def main():
    # Without its own AppUserModelID the app shares Python's taskbar button
    # (and so Python's icon); harmless to fail on non-Windows shells.
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass
    root = tk.Tk()
    AntiAFKApp(root)
    root.mainloop()


if __name__ == "__main__":
    demo() if "--selftest" in sys.argv else main()
