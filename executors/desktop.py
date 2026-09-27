"""Visible desktop actions. Keyboard input is bound to an explicit app window."""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

from tinyagent.executor import ExecutionError
from tinyagent.schema import ROOT
from .common import bind, command, output_path, profile

SITES = {"google": "https://www.google.com", "youtube": "https://www.youtube.com", "gmail": "https://mail.google.com",
         "google_drive": "https://drive.google.com", "google_maps": "https://maps.google.com", "github": "https://github.com",
         "chatgpt": "https://chatgpt.com", "claude": "https://claude.ai", "wikipedia": "https://www.wikipedia.org",
         "stackoverflow": "https://stackoverflow.com", "twitter": "https://x.com", "facebook": "https://www.facebook.com",
         "instagram": "https://www.instagram.com", "linkedin": "https://www.linkedin.com", "amazon": "https://www.amazon.in",
         "flipkart": "https://www.flipkart.com", "netflix": "https://www.netflix.com", "hotstar": "https://www.hotstar.com",
         "irctc": "https://www.irctc.co.in", "whatsapp_web": "https://web.whatsapp.com"}
KEYS = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "enter": 0x0D, "return": 0x0D,
        "tab": 9, "escape": 27, "esc": 27, "backspace": 8, "delete": 46, "space": 32,
        "left": 37, "up": 38, "right": 39, "down": 40, "home": 36, "end": 35,
        "page_up": 33, "page_down": 34, "plus": 0xBB, "minus": 0xBD,
        "media_next": 0xB0, "media_prev": 0xB1, "media_stop": 0xB2, "media_play_pause": 0xB3}
KEYS.update({f"f{i}": 0x6F + i for i in range(1, 25)})
SHORTCUTS = {"copy": "ctrl+c", "cut": "ctrl+x", "paste": "ctrl+v", "undo": "ctrl+z", "redo": "ctrl+y",
             "select_all": "ctrl+a", "save_file": "ctrl+s", "new_tab": "ctrl+t", "close_tab": "ctrl+w",
             "reopen_tab": "ctrl+shift+t", "next_tab": "ctrl+tab", "prev_tab": "ctrl+shift+tab",
             "browser_back": "alt+left", "browser_forward": "alt+right", "bookmark_page": "ctrl+d",
             "refresh": "f5", "fullscreen": "f11", "zoom_in": "ctrl+plus", "zoom_out": "ctrl+minus",
             "zoom_reset": "ctrl+0", "print_page": "ctrl+p", "close_window": "alt+f4",
             "snap_left": "win+left", "snap_right": "win+right", "show_desktop": "win+d",
             "task_view": "win+tab", "new_virtual_desktop": "ctrl+win+d", "switch_input_language": "win+space",
             "clipboard_history": "win+v", "emoji_panel": "win+.", "open_notification_center": "win+n"}
GLOBAL_SHORTCUTS = {"show_desktop", "task_view", "new_virtual_desktop", "switch_input_language", "clipboard_history", "emoji_panel", "open_notification_center"}
WINDOWS_APPS = {"notepad": "notepad.exe", "calculator": "calc.exe", "paint": "mspaint.exe", "cmd": "cmd.exe",
                "powershell": "powershell.exe", "terminal": "wt.exe", "task_manager": "Taskmgr.exe",
                "file_explorer": "explorer.exe", "control_panel": "control.exe", "disk_management": "diskmgmt.msc",
                "word": "WINWORD.EXE", "excel": "EXCEL.EXE", "powerpoint": "POWERPNT.EXE", "outlook": "OUTLOOK.EXE",
                "onenote": "ONENOTE.EXE", "chrome": "chrome.exe", "edge": "msedge.exe", "firefox": "firefox.exe",
                "chromium": "chrome.exe", "vscode": "Code.exe", "vlc": "vlc.exe", "obs": "obs64.exe",
                "steam": "steam.exe", "spotify": "Spotify.exe", "discord": "Discord.exe", "slack": "slack.exe",
                "telegram": "Telegram.exe", "signal": "Signal.exe", "zoom": "Zoom.exe", "thonny": "thonny.exe"}
WINDOWS_URIS = {"clock": "ms-clock:", "calendar": "outlookcal:", "camera": "microsoft.windows.camera:",
                "photos": "ms-photos:", "store": "ms-windows-store:", "snipping_tool": "ms-screenclip:",
                "sticky_notes": "ms-stickynotes:", "media_player": "mswindowsmusic:", "whatsapp": "whatsapp:",
                "teams": "msteams:", "sms": "ms-phone:"}
LINUX_APPS = {"notepad": ["mousepad", "leafpad", "gedit", "kate"], "calculator": ["galculator", "gnome-calculator"],
              "file_explorer": ["pcmanfm", "nautilus", "dolphin"], "terminal": ["lxterminal", "x-terminal-emulator", "gnome-terminal"],
              "browser": ["chromium", "firefox"], "chrome": ["google-chrome", "chromium"], "chromium": ["chromium"],
              "vscode": ["code"], "paint": ["pinta", "kolourpaint"], "media_player": ["vlc"], "task_manager": ["lxtask", "gnome-system-monitor"]}
SETTINGS_PAGES = {"display": "display", "sound": "sound", "bluetooth": "bluetooth", "wifi": "network-wifi", "network": "network",
                  "battery": "batterysaver", "power": "powersleep", "apps": "appsfeatures", "default_apps": "defaultapps",
                  "accounts": "yourinfo", "date_time": "dateandtime", "language": "regionlanguage", "keyboard": "typing",
                  "mouse": "mousetouchpad", "notifications": "notifications", "personalization": "personalization",
                  "printers": "printers", "privacy": "privacy", "storage": "storagesense", "about": "about", "update": "windowsupdate"}


class Desktop:
    def __init__(self):
        self.target = None
        self.lock = threading.RLock()
        self.last_capture = None
        self.opened = []
        if os.name == "nt":
            from ctypes import wintypes as w
            self.user = ctypes.WinDLL("user32", use_last_error=True)
            self.user.GetForegroundWindow.restype = w.HWND
            self.user.IsWindow.argtypes = [w.HWND]
            self.user.IsWindowVisible.argtypes = [w.HWND]
            self.user.GetWindowTextLengthW.argtypes = [w.HWND]
            self.user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
            self.user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
            self.user.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
            self.user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
            self.user.ShowWindow.argtypes = [w.HWND, ctypes.c_int]

    def windows(self):
        if os.name != "nt":
            if not shutil.which("wmctrl"):
                return []
            result = subprocess.run(["wmctrl", "-lp"], capture_output=True, text=True)
            return [{"id": parts[0], "pid": int(parts[2]), "title": parts[4]} for line in result.stdout.splitlines() if len(parts := line.split(None, 4)) == 5]
        from ctypes import wintypes as w
        rows = []
        callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        def visit(hwnd, param):
            if self.user.IsWindowVisible(hwnd):
                size = self.user.GetWindowTextLengthW(hwnd)
                if size:
                    text = ctypes.create_unicode_buffer(size + 1); self.user.GetWindowTextW(hwnd, text, size + 1)
                    pid = w.DWORD(); self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    rows.append({"id": str(hwnd), "title": text.value, "pid": pid.value})
            return True
        callback = callback_type(visit)
        self.user.EnumWindows(callback, 0)
        return rows

    def select(self, window_id):
        if window_id in (None, "", "auto"):
            self.target = None
            return {"target": None}
        match = next((w for w in self.windows() if w["id"] == str(window_id)), None)
        if not match:
            raise ExecutionError("The selected window no longer exists")
        self.target = match
        return {"target": match}

    def require_target(self, args=None):
        if not self.target or not any(w["id"] == self.target["id"] for w in self.windows()):
            raise ExecutionError("Open an app/file first, or select a target window in the UI.")
        return self.target

    def focus(self):
        target = self.require_target()
        if os.name == "nt":
            from pywinauto import Desktop as WinDesktop
            WinDesktop(backend="win32").window(handle=int(target["id"])).set_focus()
            if int(self.user.GetForegroundWindow() or 0) != int(target["id"]):
                raise ExecutionError("Windows did not focus the selected target; no keyboard input was sent")
        else:
            subprocess.run(["wmctrl", "-ia", target["id"]], check=True)
        time.sleep(.12)
        return target

    def _input(self, key=0, scan=0, flags=0):
        from ctypes import wintypes as w
        class Keyboard(ctypes.Structure):
            _fields_ = [("vk", w.WORD), ("scan", w.WORD), ("flags", w.DWORD), ("time", w.DWORD), ("extra", ctypes.c_size_t)]
        class Mouse(ctypes.Structure):
            _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("data", w.DWORD), ("flags", w.DWORD), ("time", w.DWORD), ("extra", ctypes.c_size_t)]
        class Hardware(ctypes.Structure):
            _fields_ = [("msg", w.DWORD), ("lo", w.WORD), ("hi", w.WORD)]
        class Data(ctypes.Union):
            _fields_ = [("keyboard", Keyboard), ("mouse", Mouse), ("hardware", Hardware)]
        class Input(ctypes.Structure):
            _fields_ = [("type", w.DWORD), ("data", Data)]
        item = Input(type=1, data=Data(keyboard=Keyboard(key, scan, flags, 0, 0)))
        count = self.user.SendInput(1, ctypes.byref(item), ctypes.sizeof(item))
        if count != 1:
            raise ExecutionError("Windows blocked input (check the target application's elevation level)")

    def keys(self, shortcut, focus=True):
        with self.lock:
            target = self.focus() if focus else self.target
            if os.name == "nt":
                parts = shortcut.lower().split("+")
                codes = [KEYS[p] if p in KEYS else ord(p.upper()) if len(p) == 1 else None for p in parts]
                if any(c is None for c in codes):
                    raise ExecutionError("Unsupported key: " + shortcut)
                pressed = []
                try:
                    for code in codes:
                        self._input(code); pressed.append(code)
                    time.sleep(.04)
                finally:
                    for code in reversed(pressed):
                        self._input(code, flags=2)
            else:
                xkeys = shortcut.replace("win", "super").replace("enter", "Return").replace("plus", "plus").replace("page_down", "Next")
                subprocess.run(["xdotool", "key", "--clearmodifiers", xkeys], check=True)
            return {"keys_sent": shortcut, "target": target}

    def type_text(self, text):
        with self.lock:
            target = self.focus()
            if os.name == "nt":
                units = str(text).encode("utf-16-le")
                for i in range(0, len(units), 2):
                    unit = int.from_bytes(units[i:i + 2], "little")
                    self._input(scan=unit, flags=4); self._input(scan=unit, flags=6)
                    time.sleep(.003)
            else:
                subprocess.run(["xdotool", "type", "--clearmodifiers", "--", str(text)], check=True)
            return {"characters_typed": len(text), "target": target}

    def track_open(self, opener, title_hint=None, process_hint=None):
        before = {w["id"]: w for w in self.windows()}
        opener()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            rows = self.windows()
            changed = [w for w in rows if w["id"] not in before or w["title"] != before[w["id"]]["title"]]
            matches = [w for w in changed if not title_hint or title_hint.casefold() in w["title"].casefold()]
            # Some single-instance apps reuse a window without changing its title.
            if not matches and title_hint and time.monotonic() > deadline - 7:
                matches = [w for w in rows if title_hint.casefold() in w["title"].casefold()]
            if matches:
                self.target = matches[0]; self.opened.append(matches[0])
                self.focus()
                return {"opened": True, "target": self.target}
            time.sleep(.2)
        self.target = None
        return {"launched": True, "target": None, "message": "Launch requested; no matching desktop window appeared within 10 seconds. Select its window before keyboard input."}

    def capture(self, full=False):
        from PIL import ImageGrab
        bbox = None
        if not full:
            target = self.require_target()
            if os.name == "nt":
                from ctypes import wintypes as w
                rect = w.RECT()
                if not self.user.GetWindowRect(int(target["id"]), ctypes.byref(rect)):
                    raise ExecutionError("Could not read target window bounds")
                bbox = (rect.left, rect.top, rect.right, rect.bottom)
                if rect.right <= rect.left or rect.bottom <= rect.top:
                    raise ExecutionError("The target window is minimized")
            else:
                raw = subprocess.run(["xdotool", "getwindowgeometry", "--shell", target["id"]], capture_output=True, text=True, check=True).stdout
                geometry = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
                x, y, width, height = (int(geometry[k]) for k in ("X", "Y", "WIDTH", "HEIGHT"))
                bbox = (x, y, x + width, y + height)
        picture = ImageGrab.grab(bbox=bbox, all_screens=True)
        picture.thumbnail((1280, 800))
        dest = ROOT / "runtime/captures"
        dest.mkdir(parents=True, exist_ok=True)
        import secrets
        name = secrets.token_hex(16) + ".jpg"
        picture.convert("RGB").save(dest / name, quality=82)
        self.last_capture = name
        return {"screenshot": "/api/captures/" + name, "path": str(dest / name), "target": self.target}

    def notify(self, text):
        if os.name == "nt":
            import winsound
            winsound.MessageBeep()
        elif shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "Command Lab", str(text)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def setting_toggle(self, uri, labels, desired):
        if os.name != "nt":
            raise ExecutionError("This control requires Windows Settings")
        from pywinauto import Desktop as WinDesktop
        self.track_open(lambda: os.startfile("ms-settings:" + uri), title_hint="Settings")
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            try:
                window = WinDesktop(backend="uia").window(handle=int(self.require_target()["id"]))
                controls = window.descendants(control_type="CheckBox")
                match = next((c for c in controls if c.window_text().casefold() in [s.casefold() for s in labels]), None)
                if match:
                    if match.get_toggle_state() != int(desired):
                        match.toggle()
                    time.sleep(.4)
                    actual = match.get_toggle_state()
                    if actual != int(desired):
                        raise ExecutionError("Windows did not apply the requested toggle state")
                    return {"setting": labels[0], "enabled": bool(actual), "verified": True}
            except ExecutionError:
                raise
            except Exception:
                pass
            time.sleep(.3)
        raise ExecutionError("Could not find the Settings toggle: " + ", ".join(labels) + ". Check Windows version/language or configure the matching label.")


_DESKTOP = None
def shared_desktop():
    global _DESKTOP
    if _DESKTOP is None:
        _DESKTOP = Desktop()
    return _DESKTOP


def app_executable(e, app):
    configured = e.settings.get("applications", {}).get(app)
    if configured:
        return str(configured)
    name = WINDOWS_APPS.get(app, app + ".exe")
    found = e._executable(name)
    if found:
        return found
    import winreg
    for hive in [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]:
        for view in [winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY]:
            try:
                with winreg.OpenKey(hive, "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + name, 0, winreg.KEY_READ | view) as key:
                    value = winreg.QueryValue(key, None)
                    if Path(value).is_file():
                        return value
            except OSError:
                pass
    known = {"chrome": "Google/Chrome/Application/chrome.exe", "edge": "Microsoft/Edge/Application/msedge.exe", "firefox": "Mozilla Firefox/firefox.exe",
             "vscode": "Microsoft VS Code/Code.exe", "vlc": "VideoLAN/VLC/vlc.exe"}
    for root in [os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""), str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs")]:
        candidate = Path(root) / known.get(app, "__not_found__")
        if candidate.is_file():
            return str(candidate)
    raise ExecutionError(f"Application '{app}' is not installed or discoverable. Set applications.{app} to its executable path.")


def open_app(e, a):
    app = str(a["app"]).casefold()
    if app == "browser":
        return e.desktop.track_open(lambda: webbrowser.open("about:blank"))
    if e.platform == "windows":
        if app in WINDOWS_URIS:
            return e.desktop.track_open(lambda: os.startfile(WINDOWS_URIS[app]))
        executable = app_executable(e, app)
        # Application launches are intentionally visible; helper processes remain hidden.
        return e.desktop.track_open(lambda: subprocess.Popen([executable]), title_hint=app.replace("_", " "))
    candidates = LINUX_APPS.get(app, [app])
    executable = next((shutil.which(x) for x in candidates if shutil.which(x)), None)
    if not executable:
        raise ExecutionError("Install a supported application: " + ", ".join(candidates))
    return e.desktop.track_open(lambda: subprocess.Popen([executable]), title_hint=app)


def open_file(e, a):
    path = e.path(a["path"])
    if e.platform == "windows":
        # Text tests use a visible text editor even if .txt associations differ.
        opener = (lambda: subprocess.Popen([app_executable(e, "notepad"), str(path)])) if path.suffix.lower() == ".txt" else (lambda: os.startfile(str(path)))
    else:
        opener = lambda: subprocess.Popen(["xdg-open", str(path)])
    return e.desktop.track_open(opener, title_hint=path.name) | {"path": str(path)}


def open_folder(e, a):
    name = str(a["folder"])
    folders = {"home": Path.home(), **{k.lower(): Path.home() / k for k in ["Desktop", "Documents", "Downloads", "Music", "Pictures", "Videos"]},
               "c_drive": Path("C:/"), "d_drive": Path("D:/")}
    path = folders.get(name) or e.path(name)
    if not path.is_dir():
        raise ExecutionError("Folder does not exist: " + str(path))
    return e.desktop.track_open(lambda: os.startfile(str(path)) if e.platform == "windows" else subprocess.Popen(["xdg-open", str(path)])) | {"path": str(path)}


def close_app(e, a):
    app = str(a["app"]).casefold()
    windows = [w for w in e.desktop.windows() if app.replace("_", " ") in w["title"].casefold()]
    if not windows:
        raise ExecutionError("No visible matching application window: " + app)
    for window in windows:
        if e.platform == "windows":
            e.desktop.user.PostMessageW(int(window["id"]), 0x0010, 0, 0)
        else:
            e.command(["wmctrl", "-ic", window["id"]])
    return {"close_requested": windows, "message": "The application may show its own unsaved-work dialog."}


def open_url(e, url):
    if "://" not in url:
        url = "https://" + url
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ExecutionError("Use an HTTP(S) URL without embedded credentials")
    return e.desktop.track_open(lambda: webbrowser.open(url)) | {"url": url}


def register(e):
    e.desktop = shared_desktop()
    programs = ("xdotool", "wmctrl") if e.platform == "pi" else ()
    modules = ("pywinauto",) if e.platform == "windows" else ()
    bind(e, "open_app", lambda a: open_app(e, a), modules=modules)
    bind(e, "open_file", lambda a: open_file(e, a), modules=modules)
    bind(e, "open_folder", lambda a: open_folder(e, a), modules=modules)
    bind(e, "close_app", lambda a: close_app(e, a))
    for action, shortcut in SHORTCUTS.items():
        bind(e, action, lambda a, key=shortcut, global_key=action in GLOBAL_SHORTCUTS: e.desktop.keys(key, focus=not global_key),
             programs=programs, modules=modules, preflight=None if action in GLOBAL_SHORTCUTS else e.desktop.require_target,
             description=f"Send {shortcut} to " + ("the desktop" if action in GLOBAL_SHORTCUTS else "the selected target window"))
    bind(e, "press_key", lambda a: e.desktop.keys(a["key"]), programs=programs, modules=modules, preflight=e.desktop.require_target)
    bind(e, "type_text", lambda a: e.desktop.type_text(a["text"]), programs=programs, modules=modules, preflight=e.desktop.require_target)
    bind(e, "find_in_page", lambda a: find(e, a), programs=programs, modules=modules, preflight=e.desktop.require_target)
    bind(e, "switch_window", lambda a: switch_window(e), programs=programs, modules=modules)
    for action, key in [("scroll_down", "page_down"), ("scroll_up", "page_up")]:
        bind(e, action, lambda a, k=key: e.desktop.keys(k), programs=programs, modules=modules, preflight=e.desktop.require_target)
    for action, key in [("media_next", "media_next"), ("media_prev", "media_prev"), ("media_play_pause", "media_play_pause")]:
        bind(e, action, lambda a, k=key: e.desktop.keys(k, focus=False), platforms=("windows",))
    bind(e, "open_url", lambda a: open_url(e, a["url"]))
    bind(e, "open_site", lambda a: open_url(e, e.settings.get("sites", {}).get(a["site"], SITES.get(a["site"], a["site"]))))
    bind(e, "web_search", lambda a: open_url(e, "https://www.google.com/search?" + urllib.parse.urlencode({"q": a["query"]})))
    bind(e, "youtube_search", lambda a: open_url(e, "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": a["query"]})))
    bind(e, "incognito_window", lambda a: incognito(e))
    bind(e, "screenshot", lambda a: e.desktop.capture(full=True), modules=("PIL",))
    bind(e, "open_settings", lambda a: settings(e, a))
    for action, state in [("maximize_window", 3), ("minimize_window", 6)]:
        bind(e, action, lambda a, s=state: window_state(e, s), modules=modules, preflight=e.desktop.require_target)
    bind(e, "recent_files", lambda a: recent(e))


def find(e, a):
    e.desktop.keys("ctrl+f"); e.desktop.type_text(a["query"])
    return {"find_text": a["query"], "target": e.desktop.target}


def switch_window(e):
    windows = e.desktop.windows()
    if not windows:
        raise ExecutionError("No visible windows")
    current = e.desktop.target["id"] if e.desktop.target else ""
    chosen = next((w for w in windows if w["id"] != current and "Command Lab" not in w["title"]), windows[0])
    e.desktop.target = chosen; e.desktop.focus()
    return {"focused": chosen}


def window_state(e, state):
    target = e.desktop.require_target()
    if e.platform == "windows":
        e.desktop.user.ShowWindow(int(target["id"]), state)
    elif state == 3:
        e.command(["wmctrl", "-ir", target["id"], "-b", "add,maximized_vert,maximized_horz"])
    else:
        e.command(["xdotool", "windowminimize", target["id"]])
    return {"target": target, "state": "maximized" if state == 3 else "minimized"}


def incognito(e):
    executable = app_executable(e, "chrome") if e.platform == "windows" else (shutil.which("chromium") or shutil.which("firefox"))
    if not executable:
        raise ExecutionError("Chrome/Chromium or Firefox is required")
    flag = "-private-window" if "firefox" in executable.lower() else "--incognito"
    return e.desktop.track_open(lambda: subprocess.Popen([executable, flag]))


def settings(e, a):
    if e.platform == "windows":
        page = a.get("page", "")
        if page and page not in SETTINGS_PAGES:
            raise ExecutionError("Unknown Settings page: " + page)
        return e.desktop.track_open(lambda: os.startfile("ms-settings:" + SETTINGS_PAGES.get(page, "")), title_hint="Settings")
    executable = shutil.which("gnome-control-center") or shutil.which("lxappearance")
    if not executable:
        raise ExecutionError("Install gnome-control-center or lxappearance for desktop settings")
    return e.desktop.track_open(lambda: subprocess.Popen([executable]))


def recent(e):
    path = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Recent" if e.platform == "windows" else Path.home() / ".local/share/recently-used.xbel"
    return {"path": str(path), "entries": [p.name for p in path.iterdir()][:100] if path.is_dir() else path.read_text("utf-8")[:16000]}
