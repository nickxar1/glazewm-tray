import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw, ImageFont
import subprocess
import json
import time
import threading

# --- Configuration ---
COLORS = {
    "bg": (20, 20, 20),      
    "text": (255, 255, 255), 
    "active": (66, 192, 251), # Blue highlight
    "inactive": (100, 100, 100) # Dimmed text
}

class GlazeTrayApp:
    def __init__(self):
        self.running = True
        self.current_ws = "?"
        self.all_workspaces = [] 
        self.icon = None

    def query_glaze(self):
        try:
            # We explicitly set the encoding to utf-8 to handle special characters/emojis
            result = subprocess.run(
                ["glazewm", "query", "monitors"], 
                capture_output=True, 
                text=True, 
                shell=True,
                encoding='utf-8', 
                errors='replace'   
            )
            
            if not result.stdout or result.returncode != 0: 
                return
            
            data = json.loads(result.stdout)
            new_ws_list = []
            
            def scan(obj):
                if isinstance(obj, dict):
                    if obj.get('type') == 'workspace':
                        new_ws_list.append({
                            "name": str(obj.get('name')),
                            "focused": obj.get('hasFocus', False),
                            "resident": len(obj.get('children', [])) > 0
                        })
                    for v in obj.values(): scan(v)
                elif isinstance(obj, list):
                    for i in obj: scan(i)

            scan(data)
            self.all_workspaces = sorted(new_ws_list, key=lambda x: x['name'])
            
            for ws in self.all_workspaces:
                if ws['focused']:
                    self.current_ws = ws['name']
        except Exception as e:
            # This will now print more helpful info if it fails
            print(f"Query Error: {e}")

    def create_icon_image(self):
        """Draws a compact indicator of all active workspaces."""
        width, height = 64, 64
        img = Image.new('RGB', (width, height), COLORS["bg"])
        d = ImageDraw.Draw(img)
        
        try:
            # Using a bold font style if available
            font = ImageFont.truetype("arialbd.ttf", 32) 
        except:
            font = ImageFont.load_default()

        # Only show workspaces that have windows OR are focused
        active_ws = [ws for ws in self.all_workspaces if ws['resident'] or ws['focused']]
        
        if not active_ws:
            d.text((20, 15), "?", fill=COLORS["text"], font=font)
        else:
            # Space numbers out horizontally
            x_offset = 6
            for ws in active_ws[:3]: # Limit to 3 for readability in tray
                color = COLORS["text"] if ws['resident'] else COLORS["inactive"]
                
                # Draw the number
                d.text((x_offset, 12), ws['name'][:1], fill=color, font=font)
                
                # Draw a blue underline if this is the focused one
                if ws['focused']:
                    d.rectangle([x_offset, 50, x_offset + 18, 56], fill=COLORS["active"])
                
                x_offset += 20 

        return img

    def run_cmd(self, cmd):
        subprocess.run(f"glazewm command {cmd}", shell=True)

    def generate_menu(self):
        menu_items = []
        menu_items.append(item("--- Workspaces ---", lambda: None, enabled=False))
        
        for ws in self.all_workspaces:
            name = ws['name']
            label = f"● {name}" if ws['resident'] else f"  {name}"
            menu_items.append(item(
                label, 
                lambda i, n=name: self.run_cmd(f"focus --workspace {n}"),
                checked=lambda i, f=ws['focused']: f
            ))
        
        menu_items.append(pystray.Menu.SEPARATOR)
        menu_items.append(item("Toggle Floating", lambda: self.run_cmd("toggle-floating")))
        menu_items.append(item("Close Window", lambda: self.run_cmd("close")))
        menu_items.append(pystray.Menu.SEPARATOR)
        menu_items.append(item("Reload GlazeWM", lambda: self.run_cmd("reload-config")))
        menu_items.append(item("Exit Tray Tool", self.on_exit))
        
        return pystray.Menu(*menu_items)

    def update_loop(self, icon):
        while self.running:
            self.query_glaze()
            icon.icon = self.create_icon_image()
            icon.menu = self.generate_menu()
            time.sleep(1)

    def on_exit(self, icon, item):
        self.running = False
        icon.stop()

    def run(self):
        # FIXED: Removed the ("?") argument here
        self.icon = pystray.Icon("GlazeWM", self.create_icon_image(), "GlazeWM")
        threading.Thread(target=self.update_loop, args=(self.icon,), daemon=True).start()
        self.icon.run()

if __name__ == "__main__":
    GlazeTrayApp().run()
