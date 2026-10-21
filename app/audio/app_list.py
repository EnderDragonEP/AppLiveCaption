"""Lists the apps the user can caption individually.

Two sources are combined:

* apps with an audio session on any active output device (pycaw /
  ``IAudioSessionManager2``), and
* apps with a visible top-level window, because many apps only open an audio
  session once they start playing.

Processes are grouped under their root process, so a browser or Electron app
that plays audio from a child process shows up once, under its main
executable. Process loopback then captures the whole tree from that root.
"""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes
from dataclasses import dataclass, field

import comtypes
import psutil

from .com import com_apartment

log = logging.getLogger(__name__)

# Never climb above these when looking for an app's root process.
_STOP_PARENTS = {
    "explorer.exe", "svchost.exe", "services.exe", "wininit.exe", "winlogon.exe",
    "userinit.exe", "sihost.exe", "smss.exe", "csrss.exe", "system", "cmd.exe",
    "powershell.exe", "pwsh.exe", "windowsterminal.exe", "conhost.exe",
    "openconsole.exe", "runtimebroker.exe", "applicationframehost.exe",
    "taskhostw.exe", "dllhost.exe", "wmiprvse.exe", "bash.exe", "wsl.exe",
}

# Processes that never make sense as a caption target.
_HIDDEN_APPS = {
    "explorer.exe", "textinputhost.exe", "shellexperiencehost.exe", "searchhost.exe",
    "startmenuexperiencehost.exe", "applicationframehost.exe", "lockapp.exe",
    "systemsettingsbroker.exe", "audiodg.exe", "svchost.exe", "csrss.exe",
    "dwm.exe", "nvcontainer.exe", "widgets.exe", "searchapp.exe",
}

# Child processes that are hosts/helpers of their parent app.
_HELPER_HINTS = ("helper", "webview", "crashpad", "subprocess", "renderer", "cefsharp", "qtwebengine")


@dataclass
class AppEntry:
    key: str  # lower-case executable name, used to remember the selection
    name: str  # friendly name for display
    exe_path: str
    root_pids: set[int] = field(default_factory=set)
    has_session: bool = False  # has an audio session (playing or not)
    playing: bool = False  # has an *active* audio session right now
    has_window: bool = False


# --------------------------------------------------------------------------
# Friendly names from version resources
# --------------------------------------------------------------------------
_version = ctypes.WinDLL("version")
_version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
_version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
_version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
_version.GetFileVersionInfoW.restype = wintypes.BOOL
_version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
_version.VerQueryValueW.restype = wintypes.BOOL

_name_cache: dict[str, str] = {}


def _clean_name(raw: str) -> str:
    """Cut a version-resource string at its terminator and drop control characters.

    Some executables (Spotify, for one) report a length that runs past the
    string's end, so the raw value carries a NUL and stray bytes after it,
    which render as boxes.
    """
    text = raw.split("\0", 1)[0]
    return "".join(c for c in text if c.isprintable()).strip()


def friendly_name(exe_path: str, fallback: str) -> str:
    """FileDescription (or ProductName) from the executable's version info."""
    if not exe_path:
        return fallback
    if exe_path in _name_cache:
        return _name_cache[exe_path]
    name = fallback
    try:
        size = _version.GetFileVersionInfoSizeW(exe_path, None)
        if size:
            buf = ctypes.create_string_buffer(size)
            if _version.GetFileVersionInfoW(exe_path, 0, size, buf):
                ptr = ctypes.c_void_p()
                length = wintypes.UINT()
                langs = []
                if _version.VerQueryValueW(buf, r"\VarFileInfo\Translation", ctypes.byref(ptr), ctypes.byref(length)) and length.value:
                    arr = ctypes.cast(ptr, ctypes.POINTER(wintypes.WORD * (length.value // 2))).contents
                    langs = [f"{arr[i]:04x}{arr[i + 1]:04x}" for i in range(0, len(arr) - 1, 2)]
                langs += ["040904b0", "040904e4", "000004b0"]

                def query(field_name: str) -> str:
                    for lang in langs:
                        sub = rf"\StringFileInfo\{lang}\{field_name}"
                        if _version.VerQueryValueW(buf, sub, ctypes.byref(ptr), ctypes.byref(length)) and length.value > 1:
                            value = _clean_name(ctypes.wstring_at(ptr, length.value))
                            if value:
                                return value
                    return ""

                desc, product = query("FileDescription"), query("ProductName")
                # Some apps put a sentence in FileDescription; prefer the product name then.
                if desc and (len(desc) <= 40 or not product):
                    name = desc
                elif product:
                    name = product
    except Exception:  # noqa: BLE001
        log.debug("version info failed for %s", exe_path, exc_info=True)
    _name_cache[exe_path] = name
    return name


def _pretty_exe(exe: str) -> str:
    stem = os.path.splitext(exe)[0]
    return stem[:1].upper() + stem[1:]


# --------------------------------------------------------------------------
# Process tree
# --------------------------------------------------------------------------
def root_process(proc: psutil.Process) -> psutil.Process:
    """Walk up to the app's main process (e.g. chrome.exe's browser process)."""
    cur = proc
    for _ in range(16):
        try:
            parent = cur.parent()  # psutil guards against reused parent PIDs
            if parent is None:
                break
            pname = parent.name().lower()
            cname = cur.name().lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            break
        if pname in _STOP_PARENTS:
            break
        if pname == cname or any(h in cname for h in _HELPER_HINTS):
            cur = parent
            continue
        break
    return cur


def _own_tree() -> set[int]:
    pids = {os.getpid()}
    try:
        me = psutil.Process()
        pids.update(p.pid for p in me.children(recursive=True))
    except psutil.Error:
        pass
    return pids


# --------------------------------------------------------------------------
# Audio sessions
# --------------------------------------------------------------------------
def _audio_session_pids() -> dict[int, bool]:
    """PID -> whether its session is currently active, across all output devices.

    COM must already be initialised on the calling thread.
    """
    from pycaw.api.audiopolicy import IAudioSessionControl2, IAudioSessionManager2
    from pycaw.constants import DEVICE_STATE, EDataFlow
    from pycaw.pycaw import AudioUtilities

    result: dict[int, bool] = {}
    enumerator = AudioUtilities.GetDeviceEnumerator()
    devices = enumerator.EnumAudioEndpoints(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)
    for i in range(devices.GetCount()):
        try:
            dev = devices.Item(i)
            mgr = dev.Activate(IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None).QueryInterface(IAudioSessionManager2)
            sessions = mgr.GetSessionEnumerator()
            for j in range(sessions.GetCount()):
                ctl = sessions.GetSession(j).QueryInterface(IAudioSessionControl2)
                pid = ctl.GetProcessId()
                state = ctl.GetState()
                if pid == 0 or state == 2:  # system sounds / expired
                    continue
                result[pid] = result.get(pid, False) or state == 1
        except comtypes.COMError:
            log.debug("session enumeration failed on device %d", i, exc_info=True)
    return result


# --------------------------------------------------------------------------
# Visible windows
# --------------------------------------------------------------------------
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_dwmapi = ctypes.WinDLL("dwmapi")
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
_user32.EnumChildWindows.argtypes = [wintypes.HWND, _WNDENUMPROC, wintypes.LPARAM]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetWindow.restype = wintypes.HWND
_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

_GW_OWNER = 4
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_DWMWA_CLOAKED = 14


def _window_pid(hwnd) -> int:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _visible_window_pids() -> set[int]:
    pids: set[int] = set()
    frame_hosts: list = []

    def on_window(hwnd, _):
        if not _user32.IsWindowVisible(hwnd) or _user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        if _user32.GetWindow(hwnd, _GW_OWNER):
            return True
        if _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TOOLWINDOW:
            return True
        cloaked = wintypes.DWORD()
        if _dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), 4) == 0 and cloaked.value:
            return True
        frame_hosts.append(hwnd)
        return True

    _user32.EnumWindows(_WNDENUMPROC(on_window), 0)
    for hwnd in frame_hosts:
        pid = _window_pid(hwnd)
        pids.add(pid)
        # UWP apps are hosted by ApplicationFrameHost; the real process owns a child window.
        def on_child(child, _, host_pid=pid):
            cpid = _window_pid(child)
            if cpid != host_pid:
                pids.add(cpid)
            return True

        _user32.EnumChildWindows(hwnd, _WNDENUMPROC(on_child), 0)
    return pids


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def list_apps(include_windows: bool = True) -> list[AppEntry]:
    """Return one entry per app, playing apps first. Initialises COM itself."""
    with com_apartment():
        try:
            sessions = _audio_session_pids()
        except Exception:  # noqa: BLE001
            log.exception("Could not enumerate audio sessions")
            sessions = {}
    windows = _visible_window_pids() if include_windows else set()
    own = _own_tree()

    apps: dict[str, AppEntry] = {}
    for pid in set(sessions) | windows:
        if pid in own or pid <= 4:
            continue
        try:
            root = root_process(psutil.Process(pid))
            exe = root.name()
            key = exe.lower()
            if key in _HIDDEN_APPS or root.pid in own:
                continue
            try:
                path = root.exe()
            except (psutil.AccessDenied, psutil.ZombieProcess):
                path = ""
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
            continue
        entry = apps.get(key)
        if entry is None:
            entry = apps[key] = AppEntry(key=key, name=friendly_name(path, _pretty_exe(exe)), exe_path=path)
        entry.root_pids.add(root.pid)
        if pid in sessions:
            entry.has_session = True
            entry.playing = entry.playing or sessions[pid]
        if pid in windows:
            entry.has_window = True

    return sorted(apps.values(), key=lambda a: (not a.playing, not a.has_session, a.name.lower()))


def find_running(keys: set[str]) -> dict[str, set[int]]:
    """Root PIDs of running processes whose executable is in ``keys``.

    Used to re-attach remembered apps even when they have no window or audio
    session yet.
    """
    found: dict[str, set[int]] = {}
    own = _own_tree()
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if name not in keys or proc.pid in own:
            continue
        try:
            root = root_process(proc)
            if root.name().lower() == name:
                found.setdefault(name, set()).add(root.pid)
        except psutil.Error:
            continue
    return found
