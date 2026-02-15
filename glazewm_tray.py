import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw, ImageFont
import websocket
import json
import time
import threading
import sys
import os
import ctypes
from ctypes import wintypes
import tkinter as tk
from PIL import ImageTk

# --- Configuration ---
COLORS = {
    "bg": (20, 20, 20),
    "text": (255, 255, 255),
    "active": (66, 192, 251),
    "inactive": (100, 100, 100),
    "error": (255, 100, 100)
}

GLAZEWM_WS_URL = "ws://127.0.0.1:6123"
AUTO_TOGGLE_TILING = True  # Set to False to disable auto-toggle feature
QUERY_DEBOUNCE = 0.3  # Seconds to wait after burst events (window managed/unmanaged) before querying
USE_FLOATING_BAR = True  # Set False to disable the floating bar
USE_TRAY_ICON = True     # Set False to disable the tray icon
BAR_BG_COLOR = None      # None = transparent background, or set to (r, g, b) tuple e.g. (20, 20, 20)

# Events to subscribe to
SUBSCRIBE_EVENTS = [
    "focus_changed", "workspace_activated", "workspace_deactivated",
    "workspace_updated", "window_managed", "window_unmanaged",
    "tiling_direction_changed", "binding_modes_changed",
    "focused_container_moved", "pause_changed"
]

# Events that should refresh immediately (no debounce)
IMMEDIATE_EVENTS = frozenset({
    'focus_changed', 'workspace_activated',
    'workspace_deactivated', 'workspace_updated',
    'focused_container_moved', 'tiling_direction_changed',
    'binding_modes_changed', 'pause_changed',
})

# Win32 constants
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
GWL_EXSTYLE = -20


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


# --- Win32 icon extraction ---
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH = 260
DI_NORMAL = 0x0003
SHGFI_ICON = 0x000000100
SHGFI_SMALLICON = 0x000000001
SHGFI_LARGEICON = 0x000000000


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


def _get_exe_path_for_process(process_name):
    """Find the full exe path for a process by name using toolhelp snapshots (Unicode)."""
    target = process_name.lower()
    if not target.endswith('.exe'):
        target += '.exe'

    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == -1:
        return None

    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)

        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return None

        while True:
            if entry.szExeFile.lower() == target:
                pid = entry.th32ProcessID
                handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if handle:
                    try:
                        buf = ctypes.create_unicode_buffer(MAX_PATH)
                        size = wintypes.DWORD(MAX_PATH)
                        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                            return buf.value
                    finally:
                        kernel32.CloseHandle(handle)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return None


def _hicon_to_pil(hicon, out_size=16):
    """Convert HICON to PIL Image using DrawIconEx into a DIB section."""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    # Use 32x32 render size for quality, then resize
    render_size = 32

    hdc_screen = user32.GetDC(0)
    hdc = gdi32.CreateCompatibleDC(hdc_screen)

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = render_size
    bmi.biHeight = -render_size  # top-down
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0

    bits = ctypes.c_void_p()
    hbm = gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    if not hbm:
        gdi32.DeleteDC(hdc)
        user32.ReleaseDC(0, hdc_screen)
        return None

    old_bm = gdi32.SelectObject(hdc, hbm)

    # Draw icon stretched to render_size
    user32.DrawIconEx(hdc, 0, 0, hicon, render_size, render_size, 0, 0, DI_NORMAL)

    # Read pixels
    buf_size = render_size * render_size * 4
    buf = (ctypes.c_byte * buf_size)()
    ctypes.memmove(buf, bits, buf_size)

    # Cleanup GDI
    gdi32.SelectObject(hdc, old_bm)
    gdi32.DeleteObject(hbm)
    gdi32.DeleteDC(hdc)
    user32.ReleaseDC(0, hdc_screen)

    # BGRA -> RGBA
    raw = bytearray(buf)
    for i in range(0, len(raw), 4):
        raw[i], raw[i + 2] = raw[i + 2], raw[i]

    img = Image.frombytes('RGBA', (render_size, render_size), bytes(raw))

    # Check if image is all transparent/black (failed render)
    if img.getextrema()[3][1] == 0:  # alpha channel max is 0
        return None

    if render_size != out_size:
        img = img.resize((out_size, out_size), Image.LANCZOS)
    return img


def _get_icon_via_shgetfileinfo(exe_path):
    """Get HICON using SHGetFileInfoW — reliable for most exe files."""
    info = SHFILEINFOW()
    result = ctypes.windll.shell32.SHGetFileInfoW(
        exe_path, 0, ctypes.byref(info), ctypes.sizeof(SHFILEINFOW),
        SHGFI_ICON | SHGFI_LARGEICON
    )
    if result and info.hIcon:
        return info.hIcon
    return None


def get_process_icon(process_name, size=16, _cache={}, _failures={}):
    """Get an app icon as a PIL Image for a given process name.
    Uses SHGetFileInfoW (most reliable) with ExtractIconExW as fallback.
    Retries up to 3 times for processes not yet ready."""
    if process_name in _cache:
        return _cache[process_name]

    if _failures.get(process_name, 0) >= 3:
        return None

    icon_img = None
    try:
        exe_path = _get_exe_path_for_process(process_name)
        if exe_path:
            # Method 1: SHGetFileInfoW (most reliable)
            hicon = _get_icon_via_shgetfileinfo(exe_path)
            if hicon:
                try:
                    icon_img = _hicon_to_pil(hicon, size)
                finally:
                    ctypes.windll.user32.DestroyIcon(hicon)

            # Method 2: ExtractIconExW (fallback - gets large icon)
            if not icon_img:
                hicon_large = wintypes.HICON()
                result = ctypes.windll.shell32.ExtractIconExW(
                    exe_path, 0, ctypes.byref(hicon_large), None, 1
                )
                if result > 0 and hicon_large.value:
                    try:
                        icon_img = _hicon_to_pil(hicon_large.value, size)
                    finally:
                        ctypes.windll.user32.DestroyIcon(hicon_large.value)
    except Exception as e:
        print(f"Icon extraction failed for {process_name}: {e}")

    if icon_img:
        _cache[process_name] = icon_img
    else:
        _failures[process_name] = _failures.get(process_name, 0) + 1
        if _failures[process_name] == 1:
            print(f"Icon not found for: {process_name}")
    return icon_img


_fallback_cache = {}

def _make_fallback_icon(letter, size=16):
    """Create a small colored circle with a letter as fallback icon (cached)."""
    key = (letter, size)
    if key in _fallback_cache:
        return _fallback_cache[key]
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([1, 1, size - 2, size - 2], fill=(80, 80, 80, 200))
    try:
        font = ImageFont.truetype("arial.ttf", size - 6)
    except Exception:
        font = ImageFont.load_default()
    bbox = d.textbbox((0, 0), letter, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((size - tw) / 2, (size - th) / 2 - 1), letter, fill=(255, 255, 255), font=font)
    _fallback_cache[key] = img
    return img


def _is_fullscreen_active():
    """Check if the foreground window is fullscreen (covers entire monitor)."""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False

    # Get foreground window rect
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))

    # Get the monitor this window is on
    monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    if not monitor:
        return False

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(monitor, ctypes.byref(mi))

    # Fullscreen = window rect covers entire monitor
    mr = mi.rcMonitor
    return (rect.left <= mr.left and rect.top <= mr.top and
            rect.right >= mr.right and rect.bottom >= mr.bottom)


SW_RESTORE = 9


def _restore_minimized_by_process(process_names):
    """Find minimized windows belonging to given process names and restore them.
    process_names should be a set of lowercase process name strings."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # Build pid -> process_name map for target processes
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
                # Match "explorer.exe" against "explorer" or "explorer.exe"
                if exe in process_names or exe.replace('.exe', '') in process_names:
                    target_pids.add(entry.th32ProcessID)
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snapshot)

    if not target_pids:
        return

    # Enumerate windows and restore minimized ones belonging to target pids
    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_callback(hwnd, _lparam):
        if user32.IsIconic(hwnd) and user32.IsWindowVisible(hwnd):
            proc_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
            if proc_id.value in target_pids:
                user32.ShowWindow(hwnd, SW_RESTORE)
        return True

    user32.EnumWindows(enum_callback, 0)


# --- Floating Bar ---

class FloatingBar:
    """A borderless always-on-top tkinter window showing workspace info."""

    BAR_HEIGHT = 32
    ICON_SIZE = 16
    PADDING = 6

    # Color key for transparent mode — a green nobody uses in the UI
    _TRANSPARENT_KEY = '#01fe01'

    def __init__(self, app):
        self.app = app
        self.root = tk.Tk()
        self.root.withdraw()  # Hide root window

        self.bar = tk.Toplevel(self.root)
        self.bar.overrideredirect(True)  # Borderless
        self.bar.attributes('-topmost', True)

        # Determine background: transparent or solid dark
        # _bg_hex = window/frame bg (transparent key or dark)
        # _widget_bg = label/content bg (always dark for clean text rendering)
        self._transparent = BAR_BG_COLOR is None
        self._position_right = True  # True = right side (near tray), False = left side
        self._icons_only = False    # True = hide process name text, show only icons
        self._widget_bg = self._rgb(COLORS["bg"])
        if self._transparent:
            self._bg_hex = self._TRANSPARENT_KEY
            self.bar.configure(bg=self._TRANSPARENT_KEY)
            self.bar.attributes('-transparentcolor', self._TRANSPARENT_KEY)
        else:
            self._bg_hex = self._widget_bg
            self.bar.configure(bg=self._bg_hex)

        # Container frame — uses transparent key in transparent mode
        self.frame = tk.Frame(self.bar, bg=self._bg_hex)
        self.frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        # Keep references to PhotoImages so they're not garbage collected
        self._photo_refs = []

        # Right-click context menu (disabled — kept for future use)
        self._context_menu = self._build_context_menu()
        # self.bar.bind('<Button-3>', self._show_context_menu)

        # Fullscreen tracking
        self._bar_hidden = False
        self._manually_hidden = False  # True when toggled off via tray menu

        # Position bar bottom-right, above taskbar
        self._position_bar()

        # Apply Win32 flags after window is mapped
        self.bar.after(100, self._apply_win32_flags)

        # Start fullscreen check loop (every 2 seconds)
        self._check_fullscreen()

    @staticmethod
    def _rgb(color_tuple):
        """Convert (r, g, b) to tkinter hex color."""
        return f'#{color_tuple[0]:02x}{color_tuple[1]:02x}{color_tuple[2]:02x}'

    def _position_bar(self, width=300):
        """Position bar on the taskbar (right side near tray, or left side)."""
        user32 = ctypes.windll.user32

        # Find the taskbar
        taskbar_hwnd = user32.FindWindowW("Shell_TrayWnd", None)
        if not taskbar_hwnd:
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            self.bar.geometry(f'{width}x{self.BAR_HEIGHT}+{screen_w - width - 8}+{screen_h - self.BAR_HEIGHT}')
            return

        # Get taskbar rect
        taskbar_rect = wintypes.RECT()
        user32.GetWindowRect(taskbar_hwnd, ctypes.byref(taskbar_rect))

        if self._position_right:
            # Right side: just left of the tray notification area
            tray_hwnd = user32.FindWindowExW(taskbar_hwnd, None, "TrayNotifyWnd", None)
            if tray_hwnd:
                tray_rect = wintypes.RECT()
                user32.GetWindowRect(tray_hwnd, ctypes.byref(tray_rect))
                x = tray_rect.left - width - 4
            else:
                x = taskbar_rect.right - width - 200
        else:
            # Left side: just right of the Start button area
            x = taskbar_rect.left + 4

        # Vertically center on the taskbar
        taskbar_h = taskbar_rect.bottom - taskbar_rect.top
        y = taskbar_rect.top + (taskbar_h - self.BAR_HEIGHT) // 2
        self.bar.geometry(f'{width}x{self.BAR_HEIGHT}+{x}+{y}')

    def _apply_win32_flags(self):
        """Set WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW on the bar window."""
        hwnd = int(self.bar.wm_frame(), 16) if self.bar.wm_frame() else None
        if not hwnd:
            # Fallback: find by title
            hwnd = self.bar.winfo_id()
        try:
            # Get the actual top-level HWND from the tk widget id
            hwnd = ctypes.windll.user32.GetParent(hwnd) or hwnd
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            new_style = style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
        except Exception as e:
            print(f"Failed to set bar Win32 flags: {e}")

    def _run_cmd_async(self, cmd):
        """Run a GlazeWM command in a background thread to avoid blocking tk."""
        threading.Thread(target=self.app.run_cmd, args=(cmd,), daemon=True).start()

    def _focus_workspace(self, name):
        """Focus workspace and restore any minimized windows on it."""
        # Get process names for this workspace before switching
        with self.app._lock:
            processes = set()
            for ws in self.app.all_workspaces:
                if ws['name'] == name:
                    for win in ws.get('windows', []):
                        p = win.get('process', '')
                        if p:
                            processes.add(p.lower())
                    break

        def _do():
            self.app.run_cmd(f"focus --workspace {name}")
            if not processes:
                return
            time.sleep(0.1)
            # Restore minimized windows belonging to workspace processes
            _restore_minimized_by_process(processes)

        threading.Thread(target=_do, daemon=True).start()

    def _build_context_menu(self):
        """Build right-click context menu matching pystray menu."""
        menu = tk.Menu(self.bar, tearoff=0,
                       bg=self._rgb(COLORS["bg"]),
                       fg=self._rgb(COLORS["text"]),
                       activebackground=self._rgb(COLORS["active"]),
                       activeforeground='white')
        menu.add_command(label="Toggle Floating", command=lambda: self._run_cmd_async("toggle-floating"))
        menu.add_command(label="Toggle Tiling (Alt+V)", command=lambda: self._run_cmd_async("toggle-tiling-direction"))
        menu.add_command(label="Close Window", command=lambda: self._run_cmd_async("close"))
        menu.add_separator()
        menu.add_command(label="Redraw Windows", command=lambda: self._run_cmd_async("wm-redraw"))
        menu.add_command(label="Reload GlazeWM", command=lambda: self._run_cmd_async("reload-config"))
        menu.add_separator()
        menu.add_command(label="Exit", command=self._on_exit)
        return menu

    def _show_context_menu(self, event):
        self._context_menu.tk_popup(event.x_root, event.y_root, 0)

    def _on_exit(self):
        self.app.running = False
        self.app._event.set()
        for ws in [self.app._ws_sub, self.app._ws_cmd]:
            if ws:
                try:
                    ws.close()
                except:
                    pass
        self.root.destroy()

    def update_bar(self):
        """Rebuild the bar contents from current workspace data."""
        if self._bar_hidden:
            return  # Skip rebuilding while hidden

        # Re-assert topmost so bar stays visible after monitor/workspace switches
        try:
            self.bar.attributes('-topmost', True)
            self.bar.lift()
        except tk.TclError:
            pass

        # Clear existing widgets
        for widget in self.frame.winfo_children():
            widget.destroy()
        self._photo_refs.clear()

        with self.app._lock:
            workspaces = list(self.app.all_workspaces)

        if not workspaces:
            lbl = tk.Label(self.frame, text="?" if self.app.error_count <= 3 else "!",
                           fg=self._rgb(COLORS["error"] if self.app.error_count > 3 else COLORS["text"]),
                           bg=self._widget_bg,
                           font=("Arial", 12, "bold"))
            lbl.pack(side=tk.LEFT, padx=4)
            self._position_bar(60)
            return

        total_width = self.PADDING
        for i, ws in enumerate(workspaces):
            name = ws['name']
            is_focused = ws['focused']
            has_windows = ws['resident']
            windows = ws.get('windows', [])

            # Separator between workspaces
            if i > 0:
                sep = tk.Frame(self.frame, width=1, bg=self._rgb(COLORS["inactive"]))
                sep.pack(side=tk.LEFT, fill=tk.Y, padx=3, pady=4)
                total_width += 7

            # Workspace number
            num_bg = self._rgb(COLORS["active"]) if is_focused else self._widget_bg
            num_fg = COLORS["text"] if has_windows or is_focused else COLORS["inactive"]
            num_label = tk.Label(self.frame, text=name, font=("Arial", 11, "bold"),
                                 fg=self._rgb(num_fg), bg=num_bg,
                                 padx=4, pady=0, cursor="hand2")
            num_label.pack(side=tk.LEFT, padx=(2, 1))
            num_label.bind('<Button-1>', lambda e, n=name: self._focus_workspace(n))
            total_width += 28

            # Window icons + short process name
            for win in windows:
                process = win.get('process', '')
                title = win.get('title', '') or process or '?'

                # Container for icon + label side by side
                win_frame = tk.Frame(self.frame, bg=self._widget_bg, cursor="hand2")
                win_frame.pack(side=tk.LEFT, padx=(2, 0))

                # Try to get real icon
                icon_img = get_process_icon(process, self.ICON_SIZE)
                if not icon_img:
                    icon_img = _make_fallback_icon(process[:1].upper() if process else '?', self.ICON_SIZE)
                photo = ImageTk.PhotoImage(icon_img)
                self._photo_refs.append(photo)
                icon_lbl = tk.Label(win_frame, image=photo, bg=self._widget_bg)
                icon_lbl.pack(side=tk.LEFT)

                # Short process name label (hidden in icons-only mode)
                if not self._icons_only:
                    short_name = process[:8] if process else '?'
                    name_lbl = tk.Label(win_frame, text=short_name, font=("Arial", 7),
                                        fg=self._rgb(COLORS["text"]),
                                        bg=self._widget_bg)
                    name_lbl.pack(side=tk.LEFT, padx=(1, 0))
                    total_width += self.ICON_SIZE + len(short_name) * 5 + 6
                else:
                    total_width += self.ICON_SIZE + 4

                # Click any part to switch workspace
                click_targets = [win_frame, icon_lbl]
                if not self._icons_only:
                    click_targets.append(name_lbl)
                for w in click_targets:
                    w.bind('<Button-1>', lambda e, n=name: self._focus_workspace(n))

        total_width += self.PADDING
        total_width = max(total_width, 60)
        self._position_bar(total_width)

    def _check_fullscreen(self):
        """Periodically check if a fullscreen app is active and hide/show bar.
        Also re-shows the bar if it was hidden by 'Show Desktop' or other
        external events (the bar.state() becomes 'withdrawn')."""
        try:
            if self._manually_hidden:
                pass  # Stay hidden when manually toggled off
            elif _is_fullscreen_active():
                if not self._bar_hidden:
                    self.bar.withdraw()
                    self._bar_hidden = True
            else:
                # Always re-assert visibility — covers Show Desktop hiding us
                # without setting _bar_hidden, and normal fullscreen recovery
                self.bar.deiconify()
                self.bar.attributes('-topmost', True)
                if self._bar_hidden:
                    self._bar_hidden = False
                    self.update_bar()  # Refresh position and content
        except Exception:
            pass
        # Check every 1 second for faster show/hide response
        self.root.after(1000, self._check_fullscreen)

    def toggle_icons_only(self):
        """Switch between icons+text and icons-only mode."""
        self._icons_only = not self._icons_only
        self.update_bar()

    def toggle_position(self):
        """Switch between right side (near tray) and left side of taskbar."""
        self._position_right = not self._position_right
        self.update_bar()

    def toggle_background(self):
        """Switch between transparent and dark background."""
        self._transparent = not self._transparent
        if self._transparent:
            self._bg_hex = self._TRANSPARENT_KEY
            self.bar.configure(bg=self._TRANSPARENT_KEY)
            self.bar.attributes('-transparentcolor', self._TRANSPARENT_KEY)
        else:
            self._bg_hex = self._widget_bg
            self.bar.configure(bg=self._bg_hex)
            self.bar.attributes('-transparentcolor', '')
        self.frame.configure(bg=self._bg_hex)
        self.update_bar()

    def schedule_update(self):
        """Thread-safe: schedule a bar update on the tk mainloop."""
        try:
            self.root.after_idle(self.update_bar)
        except tk.TclError:
            pass  # Window destroyed

    def run(self):
        """Start the tkinter mainloop."""
        self.update_bar()
        self.root.mainloop()


class GlazeTrayApp:
    def __init__(self):
        self.running = True
        self.current_ws = "?"
        self.all_workspaces = []
        self.icon = None
        self.bar = None  # FloatingBar instance
        self.last_error = None
        self.error_count = 0
        self._lock = threading.Lock()
        self.window_count = 0

        # WebSocket connections
        self._ws_sub = None
        self._ws_cmd = None
        self._cmd_lock = threading.Lock()

        # Debounced refresh: query only after events settle
        self._last_event_time = 0
        self._dirty = False
        self._immediate = False
        self._event = threading.Event()

        # Cached font (loaded once)
        self._font = self._load_font()

        # State cache to skip redundant redraws
        self._last_state = None

    @staticmethod
    def _load_font():
        try:
            return ImageFont.truetype("arialbd.ttf", 32)
        except Exception:
            try:
                return ImageFont.truetype("arial.ttf", 32)
            except Exception:
                return ImageFont.load_default()

    def _get_cmd_ws(self):
        """Get or create the query/command WebSocket connection."""
        if self._ws_cmd is None or not self._ws_cmd.connected:
            try:
                self._ws_cmd = websocket.WebSocket()
                self._ws_cmd.connect(GLAZEWM_WS_URL, timeout=2)
            except Exception:
                self._ws_cmd = None
                raise
        return self._ws_cmd

    def _ws_query(self, message):
        """Send a query/command over WebSocket and return the parsed response."""
        with self._cmd_lock:
            ws = self._get_cmd_ws()
            ws.send(message)
            raw = ws.recv()
            return json.loads(raw)

    def query_glaze(self):
        """Query GlazeWM state via WebSocket."""
        try:
            response = self._ws_query("query monitors")

            if not response.get('success'):
                self.error_count += 1
                self.last_error = response.get('error', 'Query failed')
                return

            data = response.get('data', {})
            new_ws_list = []
            total_windows = 0

            def collect_windows(node):
                """Recursively collect window titles from a container tree."""
                wins = []
                if isinstance(node, dict):
                    if node.get('type') == 'window':
                        wins.append({
                            "title": node.get('title', ''),
                            "process": node.get('processName', '')
                        })
                        # Don't recurse into window children
                        return wins
                    # Recurse into split containers and other non-window nodes
                    for v in node.values():
                        if isinstance(v, (dict, list)):
                            wins.extend(collect_windows(v))
                elif isinstance(node, list):
                    for el in node:
                        if isinstance(el, (dict, list)):
                            wins.extend(collect_windows(el))
                return wins

            stack = [data]
            while stack:
                obj = stack.pop()
                if isinstance(obj, dict):
                    obj_type = obj.get('type')
                    if obj_type == 'workspace':
                        windows = collect_windows(obj.get('children', []))
                        total_windows += len(windows)
                        new_ws_list.append({
                            "name": str(obj.get('name')),
                            "focused": obj.get('hasFocus', False),
                            "resident": len(windows) > 0,
                            "windows": windows
                        })
                    else:
                        for v in obj.values():
                            if isinstance(v, (dict, list)):
                                stack.append(v)
                elif isinstance(obj, list):
                    for el in obj:
                        if isinstance(el, (dict, list)):
                            stack.append(el)

            with self._lock:
                self.all_workspaces = sorted(new_ws_list, key=lambda x: x['name'])

                for ws in self.all_workspaces:
                    if ws['focused']:
                        self.current_ws = ws['name']
                        break

                self.window_count = total_windows

                if self.error_count > 0:
                    print("GlazeWM connection restored")
                self.error_count = 0
                self.last_error = None

        except Exception as e:
            self.error_count += 1
            self.last_error = str(e)
            if self._ws_cmd:
                try:
                    self._ws_cmd.close()
                except:
                    pass
                self._ws_cmd = None
            if self.error_count % 10 == 1:
                print(f"Query Error: {e}")

    def create_icon_image(self):
        """Draws a compact indicator of all active workspaces."""
        width, height = 64, 64
        img = Image.new('RGB', (width, height), COLORS["bg"])
        d = ImageDraw.Draw(img)

        font = self._font

        with self._lock:
            active_ws = [ws for ws in self.all_workspaces if ws['resident'] or ws['focused']]

        if not active_ws:
            if self.error_count > 3:
                d.text((20, 15), "!", fill=COLORS["error"], font=font)
            else:
                d.text((20, 15), "?", fill=COLORS["text"], font=font)
        else:
            x_offset = 6
            for ws in active_ws[:3]:
                color = COLORS["text"] if ws['resident'] else COLORS["inactive"]
                d.text((x_offset, 12), ws['name'][:1], fill=color, font=font)

                if ws['focused']:
                    d.rectangle([x_offset, 50, x_offset + 18, 56], fill=COLORS["active"])

                x_offset += 20

        return img

    def run_cmd(self, cmd):
        """Execute GlazeWM command via WebSocket."""
        try:
            self._ws_query(f"command {cmd}")
            # Signal immediate refresh (bypass debounce wait)
            self._dirty = True
            self._immediate = True
            self._event.set()
        except Exception as e:
            print(f"Command error: {e}")

    def toggle_tiling_direction(self):
        """Toggle tiling direction (equivalent to Alt+V)."""
        print("Auto-toggling tiling direction...")
        self.run_cmd("toggle-tiling-direction")

    def _refresh_icon(self):
        """Update tray icon/floating bar only if state has changed."""
        try:
            with self._lock:
                state_key = (
                    tuple(
                        (ws['name'], ws['focused'], ws['resident'],
                         tuple(w.get('title', '') for w in ws.get('windows', [])))
                        for ws in self.all_workspaces
                    ),
                    self.window_count,
                    self.error_count > 3,
                    self.last_error,
                )
            if state_key == self._last_state:
                return
            self._last_state = state_key

            if self.bar:
                self.bar.schedule_update()
            if self.icon:
                self.icon.icon = self.create_icon_image()
                self.icon.menu = self.generate_menu()
        except Exception as e:
            print(f"Icon update error: {e}")

    def event_loop(self):
        """Subscribe to GlazeWM events via WebSocket.

        Events only set a dirty flag - actual querying is done by the
        debounce loop to avoid hitting GlazeWM during critical moments
        like window recreation.
        """
        while self.running:
            try:
                self._ws_sub = websocket.WebSocket()
                self._ws_sub.connect(GLAZEWM_WS_URL, timeout=5)
                self._ws_sub.settimeout(None)  # Block indefinitely waiting for events

                sub_msg = "sub -e " + " ".join(SUBSCRIBE_EVENTS)
                self._ws_sub.send(sub_msg)

                ack = json.loads(self._ws_sub.recv())
                if not ack.get('success'):
                    raise Exception(f"Subscription failed: {ack.get('error')}")

                print("Connected to GlazeWM event stream (WebSocket)")

                while self.running:
                    raw = self._ws_sub.recv()
                    if not raw:
                        break

                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    event_data = event.get('data', {})
                    event_type = event_data.get('eventType', '')

                    # Focus/workspace events refresh immediately; others debounce
                    self._last_event_time = time.time()
                    self._dirty = True
                    if event_type in IMMEDIATE_EVENTS:
                        self._immediate = True
                    self._event.set()

                    # Auto-toggle tiling direction on new window (immediate)
                    if AUTO_TOGGLE_TILING and event_type == 'window_managed':
                        print("New window managed, auto-toggling...")
                        self.toggle_tiling_direction()

                if self.running:
                    self.error_count += 1
                    self.last_error = "Event stream disconnected"
                    self._refresh_icon()
                    print("GlazeWM event stream disconnected, reconnecting in 2s...")
                    time.sleep(2)

            except Exception as e:
                if self.running:
                    self.error_count += 1
                    self.last_error = str(e)
                    self._refresh_icon()
                    print(f"Event loop error: {e}")
                    time.sleep(2)
            finally:
                if self._ws_sub:
                    try:
                        self._ws_sub.close()
                    except:
                        pass
                    self._ws_sub = None

    def debounce_loop(self):
        """Wait for events, then query after they settle.

        Blocks on threading.Event (zero CPU when idle) instead of polling.
        After the first event, waits QUERY_DEBOUNCE seconds for events to
        settle before querying GlazeWM.
        """
        while self.running:
            try:
                # Block until an event arrives (zero CPU while idle)
                self._event.wait()
                self._event.clear()

                if not self._dirty:
                    continue

                # Immediate refresh requested (e.g. after a command)
                if self._immediate:
                    self._immediate = False
                    self._dirty = False
                    self.query_glaze()
                    self._refresh_icon()
                    continue

                # Wait for events to settle before querying
                while self.running and self._dirty:
                    elapsed = time.time() - self._last_event_time
                    if elapsed >= QUERY_DEBOUNCE:
                        self._dirty = False
                        self.query_glaze()
                        self._refresh_icon()
                        break
                    # Sleep remaining time, but wake if new events arrive
                    self._event.wait(timeout=QUERY_DEBOUNCE - elapsed)
                    self._event.clear()
            except Exception as e:
                print(f"Debounce loop error: {e}")
                time.sleep(1)

    def generate_menu(self):
        """Generate context menu dynamically."""
        menu_items = []
        menu_items.append(item("─── Workspaces ───", lambda: None, enabled=False))

        with self._lock:
            workspaces = list(self.all_workspaces)
            win_count = self.window_count

        if not workspaces:
            menu_items.append(item("  (No workspaces found)", lambda: None, enabled=False))
        else:
            for ws in workspaces:
                name = ws['name']
                is_focused = ws['focused']
                has_windows = ws['resident']
                windows = ws.get('windows', [])

                if has_windows:
                    label = f"● {name}"
                else:
                    label = f"○ {name}"

                def make_focus_handler(workspace_name):
                    return lambda: self.run_cmd(f"focus --workspace {workspace_name}")

                def make_check_handler(focused):
                    return lambda item: focused

                menu_items.append(item(
                    label,
                    make_focus_handler(name),
                    checked=make_check_handler(is_focused)
                ))

                for win in windows:
                    title = win.get('title', '') or win.get('process', 'Unknown')
                    if len(title) > 40:
                        title = title[:37] + "..."
                    menu_items.append(item(
                        f"    └ {title}",
                        make_focus_handler(name),
                        enabled=True
                    ))

        menu_items.append(pystray.Menu.SEPARATOR)
        menu_items.append(item(f"Total Windows: {win_count}", lambda: None, enabled=False))
        menu_items.append(pystray.Menu.SEPARATOR)
        menu_items.append(item("Toggle Floating", lambda: self.run_cmd("toggle-floating")))
        menu_items.append(item("Toggle Tiling (Alt+V)", lambda: self.run_cmd("toggle-tiling-direction")))
        menu_items.append(item("Close Window", lambda: self.run_cmd("close")))
        menu_items.append(pystray.Menu.SEPARATOR)

        def toggle_auto_feature():
            global AUTO_TOGGLE_TILING
            AUTO_TOGGLE_TILING = not AUTO_TOGGLE_TILING
            status = "enabled" if AUTO_TOGGLE_TILING else "disabled"
            print(f"Auto-toggle tiling {status}")

        menu_items.append(item(
            "Auto-Toggle on New Window",
            toggle_auto_feature,
            checked=lambda item: AUTO_TOGGLE_TILING
        ))

        # Toggle floating bar visibility
        if USE_FLOATING_BAR:
            menu_items.append(item(
                "Floating Bar",
                lambda: self._toggle_floating_bar(),
                checked=lambda _: self.bar is not None and not self.bar._manually_hidden
            ))
            menu_items.append(item(
                "Dark Background",
                lambda: self._toggle_bar_background(),
                checked=lambda _: self.bar is not None and not self.bar._transparent
            ))
            menu_items.append(item(
                "Icons Only",
                lambda: self._toggle_icons_only(),
                checked=lambda _: self.bar is not None and self.bar._icons_only
            ))
            menu_items.append(item(
                "Position: Left",
                lambda: self._toggle_bar_position(),
                checked=lambda _: self.bar is not None and not self.bar._position_right
            ))

        menu_items.append(pystray.Menu.SEPARATOR)
        menu_items.append(item("Redraw Windows (Alt+Shift+W)", lambda: self.run_cmd("wm-redraw")))
        menu_items.append(item("Reload GlazeWM", lambda: self.run_cmd("reload-config")))

        if self.last_error and self.error_count > 3:
            menu_items.append(item(f"Warning: {self.last_error[:30]}...", lambda: None, enabled=False))

        menu_items.append(item("Restart", self.restart))
        menu_items.append(item("Exit Tray Tool", self.on_exit))

        return pystray.Menu(*menu_items)

    def _toggle_bar_background(self):
        """Toggle the floating bar between transparent and dark background."""
        if not self.bar:
            return
        self.bar.root.after_idle(self.bar.toggle_background)

    def _toggle_icons_only(self):
        """Toggle between icons+text and icons-only mode."""
        if not self.bar:
            return
        self.bar.root.after_idle(self.bar.toggle_icons_only)

    def _toggle_bar_position(self):
        """Toggle the floating bar between left and right side of taskbar."""
        if not self.bar:
            return
        self.bar.root.after_idle(self.bar.toggle_position)

    def _toggle_floating_bar(self):
        """Toggle the floating bar visibility from the tray menu."""
        if not self.bar:
            return
        if self.bar._manually_hidden:
            self.bar._manually_hidden = False
            self.bar._bar_hidden = False
            self.bar.bar.deiconify()
            self.bar.schedule_update()
        else:
            self.bar._manually_hidden = True
            self.bar._bar_hidden = True
            self.bar.bar.withdraw()

    def _apply_noactivate_delayed(self):
        """Wait for pystray to create its window, then set WS_EX_NOACTIVATE."""
        time.sleep(1)
        make_process_windows_unfocusable()

    def restart(self, icon=None, item=None):
        """Restart the application by spawning a new process and exiting."""
        import subprocess
        script = os.path.abspath(sys.argv[0])
        # Use pythonw for .pyw files to stay silent, otherwise python
        if script.endswith('.pyw'):
            executable = sys.executable.replace('python.exe', 'pythonw.exe')
        else:
            executable = sys.executable
        subprocess.Popen([executable, script], creationflags=0x00000008)  # DETACHED_PROCESS
        self.on_exit(icon, item)

    def on_exit(self, icon=None, item=None):
        """Clean shutdown."""
        print("Shutting down GlazeWM tray...")
        self.running = False
        self._event.set()  # Unblock debounce loop so it exits
        for ws in [self._ws_sub, self._ws_cmd]:
            if ws:
                try:
                    ws.close()
                except:
                    pass
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
        if self.bar:
            try:
                self.bar.root.destroy()
            except Exception:
                pass

    def run(self):
        """Start the tray application."""
        print("Starting GlazeWM tray application...")
        print(f"Auto-toggle tiling: {'enabled' if AUTO_TOGGLE_TILING else 'disabled'}")
        print(f"Floating bar: {'enabled' if USE_FLOATING_BAR else 'disabled'}")
        print(f"Tray icon: {'enabled' if USE_TRAY_ICON else 'disabled'}")
        self.query_glaze()

        # Start event listener (WebSocket subscription, no querying)
        threading.Thread(target=self.event_loop, daemon=True).start()

        # Start debounced refresh loop (queries only after events settle)
        threading.Thread(target=self.debounce_loop, daemon=True).start()

        if USE_TRAY_ICON:
            self.icon = pystray.Icon(
                "GlazeWM",
                self.create_icon_image(),
                "GlazeWM Workspace Manager",
                self.generate_menu()
            )

        if USE_FLOATING_BAR:
            # Floating bar runs on main thread (tkinter mainloop)
            # Tray icon runs in background thread if enabled
            if self.icon:
                threading.Thread(target=self._run_tray_icon, daemon=True).start()
            self.bar = FloatingBar(self)
            self.bar.run()
        elif self.icon:
            # Only tray icon, run on main thread
            threading.Thread(target=self._apply_noactivate_delayed, daemon=True).start()
            self.icon.run()
        else:
            print("Error: Both USE_FLOATING_BAR and USE_TRAY_ICON are disabled!")

    def _run_tray_icon(self):
        """Run pystray icon in a background thread."""
        time.sleep(0.5)
        make_process_windows_unfocusable()
        self.icon.run()


if __name__ == "__main__":
    try:
        GlazeTrayApp().run()
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)
