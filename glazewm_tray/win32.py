"""Win32 constants, structures, and helper functions."""

import os
import ctypes
from ctypes import wintypes

# --- Win32 constants ---
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
GWL_EXSTYLE = -20
SW_RESTORE = 9

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH = 260
DI_NORMAL = 0x0003
SHGFI_ICON = 0x000000100
SHGFI_SMALLICON = 0x000000001
SHGFI_LARGEICON = 0x000000000


# --- Win32 structures ---

class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * MAX_PATH),
    ]


class SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", ctypes.c_wchar * MAX_PATH),
        ("szTypeName", ctypes.c_wchar * 80),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


# --- Helper functions ---

def make_process_windows_unfocusable():
    """Set WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW on all windows belonging to
    this process, preventing the pystray hidden window from ever stealing
    focus when another window closes (which confuses GlazeWM)."""
    pid = os.getpid()
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_callback(hwnd, _lparam):
        proc_id = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
        if proc_id.value == pid:
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            new_style = style | WS_EX_NOACTIVATE
            if new_style != style:
                ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
                found.append(hwnd)
        return True

    ctypes.windll.user32.EnumWindows(enum_callback, 0)
    if found:
        print(f"Set WS_EX_NOACTIVATE on {len(found)} process window(s)")


def is_fullscreen_active():
    """Check if the foreground window is fullscreen (covers entire monitor).

    Excludes desktop windows (Progman, WorkerW) and the taskbar
    (Shell_TrayWnd) so that "Show Desktop" doesn't trigger fullscreen hiding.
    """
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False

    # Exclude desktop and taskbar — these cover the full monitor but aren't
    # fullscreen apps.  "Show Desktop" (Win+D / bottom-right click) makes
    # the desktop the foreground window and would otherwise hide the bar.
    class_buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, class_buf, 256)
    if class_buf.value in ("Progman", "WorkerW", "Shell_TrayWnd",
                           "Shell_SecondaryTrayWnd"):
        return False

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))

    monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    if not monitor:
        return False

    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(monitor, ctypes.byref(mi))

    mr = mi.rcMonitor
    return (rect.left <= mr.left and rect.top <= mr.top and
            rect.right >= mr.right and rect.bottom >= mr.bottom)


def restore_minimized_by_process(process_names):
    """Find minimized windows belonging to given process names and restore them.
    process_names should be a set of lowercase process name strings."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    target_pids = set()
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == -1:
        return
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                exe = entry.szExeFile.lower()
                if exe in process_names or exe.replace('.exe', '') in process_names:
                    target_pids.add(entry.th32ProcessID)
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snapshot)

    if not target_pids:
        return

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_callback(hwnd, _lparam):
        if user32.IsIconic(hwnd) and user32.IsWindowVisible(hwnd):
            proc_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
            if proc_id.value in target_pids:
                user32.ShowWindow(hwnd, SW_RESTORE)
        return True

    user32.EnumWindows(enum_callback, 0)
