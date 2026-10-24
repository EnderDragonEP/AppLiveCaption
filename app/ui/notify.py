"""Tray notifications that can be silent.

Qt's QSystemTrayIcon.showMessage always lets Windows play its notification sound. Qt shows the message with
Shell_NotifyIcon(NIM_MODIFY) on its own tray window ("QTrayIconMessageWindow", icon id 0; qtbase
qwindowssystemtrayicon.cpp), so the same call with NIIF_NOSOUND on that window shows it silently. If anything
about that fails, Qt's own call is used.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QSystemTrayIcon

log = logging.getLogger(__name__)

_QT_TRAY_WINDOW = "QTrayIconMessageWindow"
NIM_MODIFY, NIF_INFO, NIF_SHOWTIP = 0x1, 0x10, 0x80
NIIF_USER, NIIF_NOSOUND, NIIF_LARGE_ICON = 0x4, 0x10, 0x20


def show_tray_message(tray: QSystemTrayIcon, title: str, text: str, icon, msecs: int = 6000,
                      silent: bool = False) -> None:
    """Show a tray notification (with the tray icon's picture); ``silent``: without Windows' sound."""
    if silent and sys.platform == "win32":
        try:
            if _show_silently(title, text, msecs):
                return
        except Exception:  # noqa: BLE001 - fall back to Qt's message, with sound
            log.debug("silent tray message failed", exc_info=True)
    tray.showMessage(title, text, icon, msecs)


def _qt_tray_window():
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    pid = ctypes.windll.kernel32.GetCurrentProcessId()
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def check(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            title = ctypes.create_unicode_buffer(64)
            user32.GetWindowTextW(hwnd, title, 64)
            if title.value == _QT_TRAY_WINDOW:
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(check, 0)
    return found[0] if found else None


def _show_silently(title: str, text: str, msecs: int) -> bool:
    import ctypes
    from ctypes import wintypes

    hwnd = _qt_tray_window()
    if not hwnd:
        return False

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                    ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                    ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                    ("szInfo", wintypes.WCHAR * 256), ("uTimeout", wintypes.UINT),
                    ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD), ("guidItem", GUID),
                    ("hBalloonIcon", wintypes.HICON)]

    data = NOTIFYICONDATAW()
    data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    data.hWnd = hwnd
    data.uID = 0
    data.uFlags = NIF_INFO | NIF_SHOWTIP
    data.szInfo = (text or " ")[:255]
    data.szInfoTitle = title[:63]
    data.uTimeout = msecs
    # NIIF_USER without a balloon icon: Windows shows the tray icon's own picture.
    data.dwInfoFlags = NIIF_USER | NIIF_LARGE_ICON | NIIF_NOSOUND
    return bool(ctypes.windll.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data)))
