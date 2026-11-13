"""Checked Windows notification-area registration, with a Qt fallback for tests."""
from __future__ import annotations
import ctypes
import logging
import sys
from pathlib import Path
from ctypes import wintypes
from PySide6.QtCore import QObject, Qt, QTimer, QRect, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QWidget, QSystemTrayIcon
from tools.windows_integrity import process_integrity, MEDIUM

LOG = logging.getLogger("PetPal.tray")


class GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]


class NotifyData(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD), ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON)]


class IconIdentifier(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
               ("uID", wintypes.UINT), ("guidItem", GUID)]


class Message(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
        ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
        ("time", wintypes.DWORD), ("pt", wintypes.POINT)]


class _Receiver(QWidget):
    def __init__(self, tray):
        super().__init__()
        self.tray = tray
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        self.setWindowTitle("PetPal · 托盘消息")
        self.setAttribute(Qt.WA_DontShowOnScreen)
        self.winId()  # Real, hidden, top-level HWND receives TaskbarCreated broadcasts.

    def nativeEvent(self, event_type, pointer):
        message = Message.from_address(int(pointer))
        if message.message == self.tray._taskbar_created:
            self.tray._registered = False
            QTimer.singleShot(500, self.tray._ensure_registered)
        elif message.message == self.tray.CALLBACK:
            event = message.lParam & 0xffff
            if event in (0x007b, 0x0205):  # WM_CONTEXTMENU / WM_RBUTTONUP
                QTimer.singleShot(0, self.tray._open_menu)
                return True, 0
            if event in (0x0400, 0x0401, 0x0203):  # NIN_SELECT / KEYSELECT / double click
                QTimer.singleShot(0, self.tray.settings_requested.emit)
                return True, 0
        return super().nativeEvent(event_type, pointer)


class WindowsTray(QObject):
    settings_requested = Signal()
    unavailable = Signal(str)
    CALLBACK = 0x8000 + 41

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self._root = Path(root)
        self._wanted = False
        self._registered = False
        self._menu = None
        self._tooltip = "PetPal"
        self._failures = 0
        self._reported = False
        self._icon = None
        self._disposed = False
        self._integrity = process_integrity()
        self._shell = ctypes.WinDLL("shell32", use_last_error=True)
        self._user = ctypes.WinDLL("user32", use_last_error=True)
        self._shell.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NotifyData)]
        self._shell.Shell_NotifyIconW.restype = wintypes.BOOL
        self._shell.Shell_NotifyIconGetRect.argtypes = [ctypes.POINTER(IconIdentifier), ctypes.POINTER(wintypes.RECT)]
        self._shell.Shell_NotifyIconGetRect.restype = ctypes.c_long
        self._user.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        self._user.RegisterWindowMessageW.restype = wintypes.UINT
        self._user.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self._user.LoadImageW.restype = wintypes.HANDLE
        self._user.DestroyIcon.argtypes = [wintypes.HICON]
        self._user.SetForegroundWindow.argtypes = [wintypes.HWND]
        self._user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
        self._taskbar_created = self._user.RegisterWindowMessageW("TaskbarCreated")
        self._receiver = _Receiver(self)
        self._hicon = self._user.LoadImageW(None, str(self._root / "assets/petpal.ico"), 1, 32, 32, 0x0010)
        if not self._hicon:
            raise OSError("无法读取 PetPal 托盘图标，请检查 assets/petpal.ico")
        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self._ensure_registered)

    def _data(self):
        data = NotifyData()
        data.cbSize = ctypes.sizeof(data)
        data.hWnd = int(self._receiver.winId())
        data.uID = 1
        # HWND + uID avoids Shell's persisted GUID/executable-path binding,
        # which is unsuitable for a locally rebuilt, movable installation.
        data.uFlags = 0x0001 | 0x0002 | 0x0004 | 0x0080
        data.uCallbackMessage = self.CALLBACK
        data.hIcon = self._hicon
        data.szTip = self._tooltip[:127]
        return data

    def _ensure_registered(self):
        if not self._wanted:
            return
        data = self._data()
        # Check the operation result, not merely Qt's requested visibility flag.
        modified = bool(self._shell.Shell_NotifyIconW(1, ctypes.byref(data)))
        if modified:
            self._registered = True
            self._failures = 0
            self._reported = False
            return
        ctypes.set_last_error(0)
        self._registered = bool(self._shell.Shell_NotifyIconW(0, ctypes.byref(data)))
        if self._registered:
            data.uVersion = 4
            if not self._shell.Shell_NotifyIconW(4, ctypes.byref(data)):
                LOG.warning("Shell could not enable notification icon protocol v4")
            self._failures = 0
            self._reported = False
            LOG.info("PetPal notification icon registered")
        else:
            self._failures += 1
            LOG.warning("Notification icon registration failed, retry %s, Win32 error %s", self._failures, ctypes.get_last_error())
            low_integrity = getattr(self, "_integrity", MEDIUM) < MEDIUM
            if (self._failures >= 3 or low_integrity) and not self._reported:
                self._reported = True
                self.unavailable.emit("PetPal 当前以低完整性级别运行，无法访问 Windows 通知区。"
                    "请退出程序，再从资源管理器直接运行 first_run.bat 修复启动项。"
                    if low_integrity else "Windows 通知区暂不可用，PetPal 正在重试。")

    def show(self):
        self._wanted = True
        self._timer.start()
        # Defer registration until the Windows/Qt message loop is running.
        QTimer.singleShot(0, self._ensure_registered)

    def hide(self):
        self._wanted = False
        self._timer.stop()
        self._shell.Shell_NotifyIconW(2, ctypes.byref(self._data()))
        self._registered = False

    def dispose(self):
        if self._disposed:
            return
        self.hide()
        self._disposed = True
        if self._hicon:
            self._user.DestroyIcon(self._hicon)
            self._hicon = None
        self._receiver.deleteLater()

    def setIcon(self, icon):
        self._icon = icon

    def icon(self):
        return self._icon

    def setToolTip(self, text):
        self._tooltip = text

    def setContextMenu(self, menu):
        self._menu = menu
        menu.aboutToHide.connect(lambda: self._user.PostMessageW(int(self._receiver.winId()), 0, 0, 0))

    def _open_menu(self):
        if self._menu:
            self._user.SetForegroundWindow(int(self._receiver.winId()))
            self._menu.popup(QCursor.pos())
            self._user.SetForegroundWindow(int(self._menu.winId()))
            self._menu.raise_()

    def isVisible(self):
        return self._wanted and self._registered

    @property
    def registered(self):
        return self._registered

    def geometry(self):
        identifier = IconIdentifier()
        identifier.cbSize = ctypes.sizeof(identifier)
        identifier.hWnd = int(self._receiver.winId())
        identifier.uID = 1
        rect = wintypes.RECT()
        if self._shell.Shell_NotifyIconGetRect(ctypes.byref(identifier), ctypes.byref(rect)) == 0:
            return QRect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
        return QRect()  # Windows can keep a registered icon in the collapsed overflow area.


def create_tray(root, parent):
    if sys.platform == "win32" and QApplication.platformName() == "windows":
        return WindowsTray(root, parent)
    return QSystemTrayIcon(parent)
