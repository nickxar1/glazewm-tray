import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw, ImageFont
import subprocess
import json
import time
import threading
import sys

# --- Configuration ---
COLORS = {
    "bg": (20, 20, 20),      
    "text": (255, 255, 255), 
    "active": (66, 192, 251),
    "inactive": (100, 100, 100),
    "error": (255, 100, 100)
}

UPDATE_INTERVAL = 0.5
AUTO_TOGGLE_TILING = True  # Set to False to disable auto-toggle feature

class GlazeTrayApp:
    def __init__(self):
        self.running = True
        self.current_ws = "?"
        self.all_workspaces = [] 
        self.icon = None
        self.last_error = None
        self.error_count = 0
        self._lock = threading.Lock()
        
        # Track window count for auto-toggle detection
        self.window_count = 0
        self.last_window_count = 0

    def query_glaze(self):
        """Query GlazeWM state with improved error handling."""
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            
            result = subprocess.run(
                ["glazewm", "query", "monitors"], 
                capture_output=True, 
                text=True, 
                encoding='utf-8',
                errors='replace',
                startupinfo=si,
                check=False,
                timeout=2
            )
            
            if result.returncode != 0:
                self.error_count += 1
                self.last_error = f"GlazeWM returned code {result.returncode}"
                if self.error_count > 5:
                    print(f"Warning: GlazeWM query failing ({self.error_count} times)")
                return
            
            if not result.stdout:
                return
            
            data = json.loads(result.stdout)
            new_ws_list = []
            total_windows = 0
            
            def scan(obj):
                nonlocal total_windows
                if isinstance(obj, dict):
                    if obj.get('type') == 'workspace':
                        children = obj.get('children', [])
                        new_ws_list.append({
                            "name": str(obj.get('name')),
                            "focused": obj.get('hasFocus', False),
                            "resident": len(children) > 0
                        })
                    # Count windows (type == 'window')
                    if obj.get('type') == 'window':
                        total_windows += 1
                    for v in obj.values(): 
                        scan(v)
                elif isinstance(obj, list):
                    for i in obj: 
                        scan(i)

            scan(data)
            
            # Thread-safe update
            with self._lock:
                self.all_workspaces = sorted(new_ws_list, key=lambda x: x['name'])
                
                for ws in self.all_workspaces:
                    if ws['focused']:
                        self.current_ws = ws['name']
                        break
                
                # Update window count
                self.last_window_count = self.window_count
                self.window_count = total_windows
                
                # Reset error count on success
                if self.error_count > 0:
                    print("GlazeWM connection restored")
                self.error_count = 0
                self.last_error = None
                
        except subprocess.TimeoutExpired:
            self.error_count += 1
            self.last_error = "GlazeWM query timeout"
        except json.JSONDecodeError as e:
            self.error_count += 1
            self.last_error = f"JSON parse error: {e}"
        except Exception as e:
            self.error_count += 1
            self.last_error = str(e)
            if self.error_count % 10 == 1:
                print(f"Query Error: {e}")

    def create_icon_image(self):
        """Draws a compact indicator of all active workspaces."""
        width, height = 64, 64
        img = Image.new('RGB', (width, height), COLORS["bg"])
        d = ImageDraw.Draw(img)
        
        try:
            font = ImageFont.truetype("arialbd.ttf", 32) 
        except:
            try:
                font = ImageFont.truetype("arial.ttf", 32)
            except:
                font = ImageFont.load_default()

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
        """Execute GlazeWM command safely without shell=True."""
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            
            full_cmd = ["glazewm", "command"] + cmd.split()
            
            subprocess.Popen(
                full_cmd,
                startupinfo=si,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # Trigger immediate update after command
            threading.Thread(target=self._delayed_update, daemon=True).start()
            
        except Exception as e:
            print(f"Command error: {e}")

    def toggle_tiling_direction(self):
        """Toggle tiling direction (equivalent to Alt+V)."""
        print("🔄 Auto-toggling tiling direction...")
        self.run_cmd("toggle-tiling-direction")

    def _delayed_update(self):
        """Force update after a short delay to catch window state changes."""
        time.sleep(0.1)
        self.query_glaze()
        if self.icon:
            self.icon.icon = self.create_icon_image()
            self.icon.menu = self.generate_menu()

    def window_monitor_loop(self):
        """Monitor for new windows and auto-toggle tiling."""
        print("🪟 Window monitor started (auto-toggle enabled)")

        while self.running:
            try:
                with self._lock:
                    current_count = self.window_count
                    last_count = self.last_window_count

                # Detect new window opened
                if AUTO_TOGGLE_TILING and current_count > last_count:
                    print(f"📊 Window count: {last_count} → {current_count}")
                    # Mark change as consumed so we don't double-fire
                    with self._lock:
                        self.last_window_count = current_count
                    self.toggle_tiling_direction()

                time.sleep(0.3)  # Check more frequently than main loop

            except Exception as e:
                print(f"Window monitor error: {e}")
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
        
        # Auto-toggle toggle switch
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
        menu_items.append(item("Reload GlazeWM", lambda: self.run_cmd("reload-config")))
        
        if self.last_error and self.error_count > 3:
            menu_items.append(item(f"⚠ Error: {self.last_error[:30]}...", lambda: None, enabled=False))
        
        menu_items.append(item("Exit Tray Tool", self.on_exit))
        
        return pystray.Menu(*menu_items)

    def update_loop(self, icon):
        """Main update loop with better timing."""
        while self.running:
            self.query_glaze()
            
            try:
                icon.icon = self.create_icon_image()
                icon.menu = self.generate_menu()
            except Exception as e:
                print(f"Update error: {e}")
            
            time.sleep(UPDATE_INTERVAL)

    def on_exit(self, icon, item):
        """Clean shutdown."""
        print("Shutting down GlazeWM tray...")
        self.running = False
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
        
        # Start main update loop
        threading.Thread(
            target=self.update_loop, 
            args=(self.icon,), 
            daemon=True
        ).start()
        
        # Start window monitor loop
        threading.Thread(
            target=self.window_monitor_loop,
            daemon=True
        ).start()
        
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