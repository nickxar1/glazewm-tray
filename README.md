# 🪟 GlazeWM Tray Indicator

A lightweight, minimal system tray utility for GlazeWM. This tool replaces the need for a bulky status bar by providing workspace information and window management directly from the Windows System Tray.

## ✨ Features

### Core Functionality
* **Multi-Workspace View**: Displays numbers for all workspaces currently containing open windows
* **Focus Tracking**: Highlights your active workspace with a blue underline
* **Occupancy Indicators**: Workspaces with windows are marked with `●`, empty ones with `○`
* **Real-time Updates**: Fast 0.5-second refresh rate for responsive workspace tracking
* **Window Counter**: Displays total number of open windows across all workspaces

### Window Management
* **Auto-Toggle Tiling** ⭐ NEW: Automatically toggles tiling direction (vertical/horizontal) whenever a new window is opened
* **Quick Actions**: Right-click to access common window commands:
  - Toggle Floating mode
  - Toggle Tiling Direction (Alt+V equivalent)
  - Close active window
  - Reload GlazeWM configuration
* **Click-to-Switch**: Instantly jump to any workspace by selecting it from the tray menu

### Reliability Features
* **Thread-Safe Operations**: Prevents race conditions and UI freezes
* **Error Recovery**: Automatic reconnection if GlazeWM temporarily becomes unresponsive
* **Visual Error Indicators**: Icon changes to "!" when connection issues occur
* **Timeout Protection**: Prevents hanging if GlazeWM queries take too long

## 🛠️ Prerequisites

This tool requires **Python 3.x**. Open your terminal and run the following command to install the necessary libraries:

```bash
pip install pystray pillow
```

## 🏃 How to Run

### 1. The Regular Way (For Testing)
Run the script using the standard Python command. This will keep a command prompt window open, which is helpful for seeing status messages and error logs:

```bash
python glazewm_tray.py
```

You'll see console output like:
```
Starting GlazeWM tray application...
Auto-toggle tiling: enabled
🪟 Window monitor started (auto-toggle enabled)
```

### 2. The "Silent" Way (Background Mode)
To run the tool without a command prompt window cluttering your taskbar:

1. Rename your file from `glazewm_tray.py` to `glazewm_tray.pyw`
2. Double-click the `.pyw` file
3. The script will now run invisibly in the background, appearing only as an icon in your System Tray

### 3. How to Stop the Tool
* **If running as .py**: Close the command prompt window or press `Ctrl+C`
* **If running as .pyw**: Right-click the icon in the System Tray and select **Exit Tray Tool**

## ⚙️ Start Automatically with Windows

To have your tray indicator start every time you log in:

1. Press `Win + R`, type `shell:startup`, and press Enter
2. Right-click your `glazewm_tray.pyw` file and select **Create Shortcut**
3. Move that shortcut into the Startup folder you just opened

**Pro Tip**: Use the `.pyw` version for startup to avoid having a command window appear on boot.

## 🎛️ Configuration

### Auto-Toggle Tiling Feature
The auto-toggle feature automatically runs the tiling direction toggle command (equivalent to Alt+V) whenever a new window is detected. This helps maintain optimal layouts as you open new applications.

**To disable auto-toggle**:
1. Set `AUTO_TOGGLE_TILING = False` at the top of the script, OR
2. Right-click the tray icon → Uncheck "Auto-Toggle on New Window"

You can toggle this setting on/off at any time without restarting the application.

### Update Frequency
By default, the tray icon updates every 0.5 seconds. To change this:

```python
UPDATE_INTERVAL = 0.5  # Change to 0.3 for faster, 1.0 for slower updates
```

### Visual Customization
You can adjust the appearance by editing the `COLORS` dictionary at the top of the script:

```python
COLORS = {
    "bg": (20, 20, 20),          # Background color of the icon
    "text": (255, 255, 255),      # Color of workspace numbers
    "active": (66, 192, 251),     # Highlight color for focused workspace
    "inactive": (100, 100, 100),  # Color for empty workspaces
    "error": (255, 100, 100)      # Color for error indicator
}
```

## 📋 Menu Options

Right-click the tray icon to access:

| Option | Description |
|--------|-------------|
| **Workspace List** | Click any workspace to switch to it instantly |
| **Total Windows** | Shows count of all open windows |
| **Toggle Floating** | Toggle floating mode for active window |
| **Toggle Tiling (Alt+V)** | Manually toggle tiling direction |
| **Close Window** | Close the currently focused window |
| **Auto-Toggle on New Window** | Enable/disable automatic tiling toggle |
| **Reload GlazeWM** | Reload GlazeWM configuration |
| **Exit Tray Tool** | Close the tray application |

## ⚠️ Troubleshooting

### Icon shows "!"
The script cannot communicate with GlazeWM. Possible causes:
* GlazeWM is not running
* GlazeWM is not in your System PATH
* GlazeWM is temporarily unresponsive

**Fix**: Make sure GlazeWM is installed and running. Verify it's in your PATH by running `glazewm --version` in a command prompt.

### Icon shows "?"
The script is running, but GlazeWM is not reporting any active monitors or workspaces.

**Fix**: Try reloading GlazeWM configuration using the tray menu option.

### Import Errors
If Python says "No module named pystray," re-run the pip install command specifically for your Python version:

```bash
python -m pip install pystray pillow
```

### Windows Not Being Tracked
If new windows aren't being detected:
1. Check the console output (run as `.py` instead of `.pyw`)
2. Verify GlazeWM is properly managing the windows
3. Try toggling the Auto-Toggle feature off and on

### High CPU Usage
If the tray tool is using too much CPU:
1. Increase `UPDATE_INTERVAL` from 0.5 to 1.0 seconds
2. Check if GlazeWM itself is having performance issues

## 🔧 Advanced Usage

### Running with Custom GlazeWM Location
If `glazewm` is not in your PATH, you can modify the subprocess calls in the script to use the full path:

```python
# Find this line in the script:
["glazewm", "query", "monitors"]

# Replace with:
[r"C:\Path\To\glazewm.exe", "query", "monitors"]
```

### Logging for Debugging
To see detailed logs, run the script as `.py` (not `.pyw`) and check the console output. You'll see:
* Connection status messages
* Window count changes
* Auto-toggle actions
* Error messages with details

## 🤝 Contributing

Feel free to submit issues or pull requests if you find bugs or have feature suggestions!

## 📄 License

This is free and open-source software. Use it however you'd like!

## 🙏 Credits

Built for the [GlazeWM](https://github.com/glzr-io/glazewm) tiling window manager community.