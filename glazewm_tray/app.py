"""Main application class — GlazeWM tray + floating bar controller."""

import os
import sys
import json
import time
import threading
import subprocess

import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw, ImageFont
import websocket

import config
from .floating_bar import FloatingBar
from .icons import get_process_icon
from .win32 import make_process_windows_unfocusable


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
                self._ws_cmd.connect(config.GLAZEWM_WS_URL, timeout=2)
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
                        return wins
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
        img = Image.new('RGB', (width, height), config.COLORS["bg"])
        d = ImageDraw.Draw(img)

        font = self._font

        with self._lock:
            active_ws = [ws for ws in self.all_workspaces if ws['resident'] or ws['focused']]

        if not active_ws:
            if self.error_count > 3:
                d.text((20, 15), "!", fill=config.COLORS["error"], font=font)
            else:
                d.text((20, 15), "?", fill=config.COLORS["text"], font=font)
        else:
            x_offset = 6
            for ws in active_ws[:3]:
                color = config.COLORS["text"] if ws['resident'] else config.COLORS["inactive"]
                d.text((x_offset, 12), ws['name'][:1], fill=color, font=font)

                if ws['focused']:
                    d.rectangle([x_offset, 50, x_offset + 18, 56], fill=config.COLORS["active"])

                x_offset += 20

        return img

    def run_cmd(self, cmd):
        """Execute GlazeWM command via WebSocket."""
        try:
            self._ws_query(f"command {cmd}")
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
        """Subscribe to GlazeWM events via WebSocket."""
        while self.running:
            try:
                self._ws_sub = websocket.WebSocket()
                self._ws_sub.connect(config.GLAZEWM_WS_URL, timeout=5)
                self._ws_sub.settimeout(None)

                sub_msg = "sub -e " + " ".join(config.SUBSCRIBE_EVENTS)
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

                    self._last_event_time = time.time()
                    self._dirty = True
                    if event_type in config.IMMEDIATE_EVENTS:
                        self._immediate = True
                    self._event.set()

                    if config.AUTO_TOGGLE_TILING and event_type == 'window_managed':
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
        """Wait for events, then query after they settle."""
        while self.running:
            try:
                self._event.wait()
                self._event.clear()

                if not self._dirty:
                    continue

                if self._immediate:
                    self._immediate = False
                    self._dirty = False
                    self.query_glaze()
                    self._refresh_icon()
                    continue

                while self.running and self._dirty:
                    elapsed = time.time() - self._last_event_time
                    if elapsed >= config.QUERY_DEBOUNCE:
                        self._dirty = False
                        self.query_glaze()
                        self._refresh_icon()
                        break
                    self._event.wait(timeout=config.QUERY_DEBOUNCE - elapsed)
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
            config.AUTO_TOGGLE_TILING = not config.AUTO_TOGGLE_TILING
            status = "enabled" if config.AUTO_TOGGLE_TILING else "disabled"
            print(f"Auto-toggle tiling {status}")

        menu_items.append(item(
            "Auto-Toggle on New Window",
            toggle_auto_feature,
            checked=lambda item: config.AUTO_TOGGLE_TILING
        ))

        if config.USE_FLOATING_BAR:
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
        if not self.bar:
            return
        self.bar.root.after_idle(self.bar.toggle_background)

    def _toggle_icons_only(self):
        if not self.bar:
            return
        self.bar.root.after_idle(self.bar.toggle_icons_only)

    def _toggle_bar_position(self):
        if not self.bar:
            return
        self.bar.root.after_idle(self.bar.toggle_position)

    def _toggle_floating_bar(self):
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
        time.sleep(1)
        make_process_windows_unfocusable()

    def restart(self, icon=None, item=None):
        """Restart the application by spawning a new process and exiting."""
        script = os.path.abspath(sys.argv[0])
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
        self._event.set()
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
        print(f"Auto-toggle tiling: {'enabled' if config.AUTO_TOGGLE_TILING else 'disabled'}")
        print(f"Floating bar: {'enabled' if config.USE_FLOATING_BAR else 'disabled'}")
        print(f"Tray icon: {'enabled' if config.USE_TRAY_ICON else 'disabled'}")
        self.query_glaze()

        threading.Thread(target=self.event_loop, daemon=True).start()
        threading.Thread(target=self.debounce_loop, daemon=True).start()

        if config.USE_TRAY_ICON:
            self.icon = pystray.Icon(
                "GlazeWM",
                self.create_icon_image(),
                "GlazeWM Workspace Manager",
                self.generate_menu()
            )

        if config.USE_FLOATING_BAR:
            if self.icon:
                threading.Thread(target=self._run_tray_icon, daemon=True).start()
            self.bar = FloatingBar(self)
            self.bar.run()
        elif self.icon:
            threading.Thread(target=self._apply_noactivate_delayed, daemon=True).start()
            self.icon.run()
        else:
            print("Error: Both USE_FLOATING_BAR and USE_TRAY_ICON are disabled!")

    def _run_tray_icon(self):
        time.sleep(0.5)
        make_process_windows_unfocusable()
        self.icon.run()
