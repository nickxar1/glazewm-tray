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
QUERY_DEBOUNCE = 1.0  # Seconds to wait after last event before querying

# Events to subscribe to
SUBSCRIBE_EVENTS = [
    "focus_changed", "workspace_activated", "workspace_deactivated",
    "workspace_updated", "window_managed", "window_unmanaged",
    "tiling_direction_changed", "binding_modes_changed",
    "focused_container_moved", "pause_changed"
]

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


class GlazeTrayApp:
    def __init__(self):
        self.running = True
        self.current_ws = "?"
        self.all_workspaces = []
        self.icon = None
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

            stack = [data]
            while stack:
                obj = stack.pop()
                if isinstance(obj, dict):
                    obj_type = obj.get('type')
                    if obj_type == 'workspace':
                        children = obj.get('children', [])
                        new_ws_list.append({
                            "name": str(obj.get('name')),
                            "focused": obj.get('hasFocus', False),
                            "resident": len(children) > 0
                        })
                    elif obj_type == 'window':
                        total_windows += 1
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
        """Update tray icon and menu only if state has changed."""
        if not self.icon:
            return
        try:
            with self._lock:
                state_key = (
                    tuple(
                        (ws['name'], ws['focused'], ws['resident'])
                        for ws in self.all_workspaces
                    ),
                    self.window_count,
                    self.error_count > 3,
                    self.last_error,
                )
            if state_key == self._last_state:
                return
            self._last_state = state_key
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

                    # Mark dirty and wake the debounce loop
                    self._last_event_time = time.time()
                    self._dirty = True
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

        menu_items.append(pystray.Menu.SEPARATOR)
        menu_items.append(item("Redraw Windows (Alt+Shift+W)", lambda: self.run_cmd("wm-redraw")))
        menu_items.append(item("Reload GlazeWM", lambda: self.run_cmd("reload-config")))

        if self.last_error and self.error_count > 3:
            menu_items.append(item(f"Warning: {self.last_error[:30]}...", lambda: None, enabled=False))

        menu_items.append(item("Exit Tray Tool", self.on_exit))

        return pystray.Menu(*menu_items)

    def _apply_noactivate_delayed(self):
        """Wait for pystray to create its window, then set WS_EX_NOACTIVATE."""
        time.sleep(1)
        make_process_windows_unfocusable()

    def on_exit(self, icon, item):
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
        icon.stop()

    def run(self):
        """Start the tray application."""
        print("Starting GlazeWM tray application...")
        print(f"Auto-toggle tiling: {'enabled' if AUTO_TOGGLE_TILING else 'disabled'}")
        self.query_glaze()

        self.icon = pystray.Icon(
            "GlazeWM",
            self.create_icon_image(),
            "GlazeWM Workspace Manager",
            self.generate_menu()
        )

        # Start event listener (WebSocket subscription, no querying)
        threading.Thread(target=self.event_loop, daemon=True).start()

        # Start debounced refresh loop (queries only after events settle)
        threading.Thread(target=self.debounce_loop, daemon=True).start()

        # Apply WS_EX_NOACTIVATE after pystray creates its window
        threading.Thread(target=self._apply_noactivate_delayed, daemon=True).start()

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
