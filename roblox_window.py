"""Win32 layer: find the Roblox window and deliver keystrokes to it.

No Tk and no logging here, so it can be imported and tested on its own.
Functions raise pywintypes.error for genuine API failures; a False return
means "the window would not take focus", which is a normal, retryable state.
"""

import time
import win32gui
import win32con
import win32api
import win32process
import pywintypes

VK_SHIFT = 0x10
ROBLOX_CLASS = "WINDOWSCLIENT"
KEY_HOLD = 0.03         # key hold time (enough for Roblox, not noticeable)
FOCUS_TIMEOUT = 0.5     # how long to wait for a focus transition


def find_window():
    """Return a visible Roblox Player window handle, or None.

    The Roblox Player window class is WINDOWSCLIENT. Do not match any title
    containing "roblox" (an Explorer window would be caught), and do not rely
    on the title "Roblox" alone: in fullscreen/borderless the class is still
    WINDOWSCLIENT even when the title differs.
    """
    found = []

    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == ROBLOX_CLASS:
            found.append(hwnd)
        return True

    win32gui.EnumWindows(callback, None)
    for hwnd in found:
        if win32gui.GetWindowText(hwnd) == "Roblox":
            return hwnd
    return found[0] if found else None


def resolve(hwnd):
    """Return hwnd if it is still a live window, otherwise search again."""
    if hwnd and win32gui.IsWindow(hwnd):
        return hwnd
    return find_window()


def activate(hwnd):
    """Bring hwnd to the foreground. True only if it really got focus.

    Windows denies SetForegroundWindow when this process does not own the last
    input, so attach our thread to the foreground thread first.
    """
    foreground = win32gui.GetForegroundWindow()
    if foreground == hwnd:
        return True

    fg_thread = win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
    my_thread = win32api.GetCurrentThreadId()
    attached = False
    try:
        if fg_thread and fg_thread != my_thread:
            try:
                win32process.AttachThreadInput(my_thread, fg_thread, True)
                attached = True
            except pywintypes.error:
                pass  # still try the normal path
        # SW_RESTORE cancels maximize/fullscreen, so only use it when minimized.
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.BringWindowToTop(hwnd)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except pywintypes.error:
            pass  # focus refused; verified by the polling below
        # Polling beats a fixed sleep: usually settles in under 40 ms.
        deadline = time.monotonic() + FOCUS_TIMEOUT
        while time.monotonic() < deadline:
            if win32gui.GetForegroundWindow() == hwnd:
                return True
            time.sleep(0.02)
        return False
    finally:
        if attached:
            try:
                win32process.AttachThreadInput(my_thread, fg_thread, False)
            except pywintypes.error:
                pass


def key_lparam(scancode, up=False):
    """WM_KEY* lParam: repeat count 1, scancode in bits 16-23, and for a
    release the previous-state (30) and transition (31) bits set."""
    lparam = (scancode << 16) | 1
    if up:
        lparam |= (1 << 30) | (1 << 31)
    return lparam


def post_key(hwnd, key_code):
    """Keystroke through the window message queue: steals no focus and still
    arrives while Roblox is in the background. Best-effort complement to real
    input, not a replacement for it.
    """
    scancode = win32api.MapVirtualKey(key_code, 0)
    try:
        win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, key_code, key_lparam(scancode))
        win32gui.PostMessage(hwnd, win32con.WM_KEYUP, key_code, key_lparam(scancode, True))
    except pywintypes.error:
        pass


def send_input(key_code):
    """Hardware-level keyboard input to whatever window holds the foreground."""
    scancode = win32api.MapVirtualKey(key_code, 0)
    win32api.keybd_event(key_code, scancode, 0, 0)
    time.sleep(KEY_HOLD)
    win32api.keybd_event(key_code, scancode, win32con.KEYEVENTF_KEYUP, 0)


def send_key(hwnd, key_code):
    """Send key_code to hwnd. True only if a real keystroke was delivered.

    Fast path: when the window already holds the foreground (the usual
    fullscreen/borderless AFK case) send it straight away, with no thread
    attach, no z-order change and no flicker. Otherwise take focus, send, and
    hand focus back to the window the user was on.
    """
    post_key(hwnd, key_code)  # focus-free path, always attempted

    if win32gui.GetForegroundWindow() == hwnd:
        send_input(key_code)
        return True

    saved_hwnd = win32gui.GetForegroundWindow()
    try:
        if not activate(hwnd):
            return False  # never send the key to the wrong window
        send_input(key_code)
        return True
    finally:
        if saved_hwnd and saved_hwnd != hwnd and win32gui.IsWindow(saved_hwnd):
            try:
                activate(saved_hwnd)
            except pywintypes.error:
                pass
