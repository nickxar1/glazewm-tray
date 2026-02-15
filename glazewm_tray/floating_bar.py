"""Floating bar widget — a borderless always-on-top tkinter window on the taskbar."""

import threading
import ctypes
from ctypes import wintypes
import tkinter as tk
from PIL import ImageTk

import config
from .icons import get_process_icon, make_fallback_icon
from .win32 import (
    WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, GWL_EXSTYLE,
    is_fullscreen_active, restore_minimized_by_process,
)


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
        self._transparent = config.BAR_BG_COLOR is None
        self._position_right = True  # True = right side (near tray), False = left side
        self._icons_only = False    # True = hide process name text, show only icons
        self._widget_bg = self._rgb(config.COLORS["bg"])
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

        taskbar_hwnd = user32.FindWindowW("Shell_TrayWnd", None)
        if not taskbar_hwnd:
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            self.bar.geometry(f'{width}x{self.BAR_HEIGHT}+{screen_w - width - 8}+{screen_h - self.BAR_HEIGHT}')
            return

        taskbar_rect = wintypes.RECT()
        user32.GetWindowRect(taskbar_hwnd, ctypes.byref(taskbar_rect))

        if self._position_right:
            tray_hwnd = user32.FindWindowExW(taskbar_hwnd, None, "TrayNotifyWnd", None)
            if tray_hwnd:
                tray_rect = wintypes.RECT()
                user32.GetWindowRect(tray_hwnd, ctypes.byref(tray_rect))
                x = tray_rect.left - width - 4
            else:
                x = taskbar_rect.right - width - 200
        else:
            x = taskbar_rect.left + 4

        taskbar_h = taskbar_rect.bottom - taskbar_rect.top
        y = taskbar_rect.top + (taskbar_h - self.BAR_HEIGHT) // 2
        self.bar.geometry(f'{width}x{self.BAR_HEIGHT}+{x}+{y}')

    def _apply_win32_flags(self):
        """Set WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW on the bar window."""
        hwnd = int(self.bar.wm_frame(), 16) if self.bar.wm_frame() else None
        if not hwnd:
            hwnd = self.bar.winfo_id()
        try:
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
            import time
            time.sleep(0.1)
            restore_minimized_by_process(processes)

        threading.Thread(target=_do, daemon=True).start()

    def _build_context_menu(self):
        """Build right-click context menu matching pystray menu."""
        menu = tk.Menu(self.bar, tearoff=0,
                       bg=self._rgb(config.COLORS["bg"]),
                       fg=self._rgb(config.COLORS["text"]),
                       activebackground=self._rgb(config.COLORS["active"]),
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
            return

        try:
            self.bar.attributes('-topmost', True)
            self.bar.lift()
        except tk.TclError:
            pass

        for widget in self.frame.winfo_children():
            widget.destroy()
        self._photo_refs.clear()

        with self.app._lock:
            workspaces = list(self.app.all_workspaces)

        if not workspaces:
            lbl = tk.Label(self.frame, text="?" if self.app.error_count <= 3 else "!",
                           fg=self._rgb(config.COLORS["error"] if self.app.error_count > 3 else config.COLORS["text"]),
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

            if i > 0:
                sep = tk.Frame(self.frame, width=1, bg=self._rgb(config.COLORS["inactive"]))
                sep.pack(side=tk.LEFT, fill=tk.Y, padx=3, pady=4)
                total_width += 7

            num_bg = self._rgb(config.COLORS["active"]) if is_focused else self._widget_bg
            num_fg = config.COLORS["text"] if has_windows or is_focused else config.COLORS["inactive"]
            num_label = tk.Label(self.frame, text=name, font=("Arial", 11, "bold"),
                                 fg=self._rgb(num_fg), bg=num_bg,
                                 padx=4, pady=0, cursor="hand2")
            num_label.pack(side=tk.LEFT, padx=(2, 1))
            num_label.bind('<Button-1>', lambda e, n=name: self._focus_workspace(n))
            total_width += 28

            for win in windows:
                process = win.get('process', '')
                title = win.get('title', '') or process or '?'

                win_frame = tk.Frame(self.frame, bg=self._widget_bg, cursor="hand2")
                win_frame.pack(side=tk.LEFT, padx=(2, 0))

                icon_img = get_process_icon(process, self.ICON_SIZE)
                if not icon_img:
                    icon_img = make_fallback_icon(process[:1].upper() if process else '?', self.ICON_SIZE)
                photo = ImageTk.PhotoImage(icon_img)
                self._photo_refs.append(photo)
                icon_lbl = tk.Label(win_frame, image=photo, bg=self._widget_bg)
                icon_lbl.pack(side=tk.LEFT)

                if not self._icons_only:
                    display = title if title and title != process else process
                    for suffix in (' - Google Chrome', ' - Chrome', ' — Mozilla Firefox',
                                   ' - Microsoft Edge', ' - Notepad', ' - Visual Studio Code'):
                        if display.endswith(suffix):
                            display = display[:-len(suffix)]
                            break
                    short_name = display[:12] if display else '?'
                    name_lbl = tk.Label(win_frame, text=short_name, font=("Arial", 7),
                                        fg=self._rgb(config.COLORS["text"]),
                                        bg=self._widget_bg)
                    name_lbl.pack(side=tk.LEFT, padx=(1, 0))
                    total_width += self.ICON_SIZE + len(short_name) * 5 + 6
                else:
                    total_width += self.ICON_SIZE + 4

                click_targets = [win_frame, icon_lbl]
                if not self._icons_only:
                    click_targets.append(name_lbl)
                for w in click_targets:
                    w.bind('<Button-1>', lambda e, n=name: self._focus_workspace(n))

        total_width += self.PADDING
        total_width = max(total_width, 60)
        self._position_bar(total_width)

    def _check_fullscreen(self):
        """Periodically check if a fullscreen app is active and hide/show bar."""
        try:
            if self._manually_hidden:
                pass
            elif is_fullscreen_active():
                if not self._bar_hidden:
                    self.bar.withdraw()
                    self._bar_hidden = True
            else:
                self.bar.deiconify()
                self.bar.attributes('-topmost', True)
                if self._bar_hidden:
                    self._bar_hidden = False
                    self.update_bar()
        except Exception:
            pass
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
            pass

    def run(self):
        """Start the tkinter mainloop."""
        self.update_bar()
        self.root.mainloop()
