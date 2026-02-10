GlazeWM Tray Indicator
A lightweight, zero-bar alternative for GlazeWM. This tool lives in your system tray and provides a real-time visual map of your workspaces without occupying any screen real estate.

🚀 Features
Multi-Workspace Tracking: Displays the numbers of all workspaces that have active windows.

Focus Highlighting: Underlines the workspace you are currently on with a blue indicator.

Interactive Menu: Right-click to switch workspaces, close windows, or toggle floating mode.

Dynamic Occupancy: Shows a "●" symbol in the menu for workspaces that contain windows.

Low Overhead: Written in Python; uses minimal CPU and RAM compared to full Electron-based bars.

🛠️ Prerequisites
Before running the tool, you need to install the following Python libraries. Open your terminal and run:

PowerShell
pip install pystray pillow
pystray: Handles the system tray icon and menu logic.

pillow: Used to programmatically draw the workspace numbers onto the icon.

🏃 How to Run
Manual Launch
To run the tool with a visible console (useful for debugging):

PowerShell
python glaze_tray.py
Background Execution (.pyw)
To run the tool without a command prompt window appearing:

Rename your script from glaze_tray.py to glaze_tray.pyw.

Double-click glaze_tray.pyw.

The tool will now run silently in the background. You can find it in your system tray (near the clock).

⚙️ Auto-Start with Windows
To have the tray indicator start automatically when you turn on your PC:

Press Win + R, type shell:startup, and press Enter.

Right-click your glaze_tray.pyw file and select Create Shortcut.

Move that shortcut into the Startup folder you just opened.

⌨️ Troubleshooting
Icon shows "Err": The script cannot communicate with GlazeWM. Ensure glazewm is in your System PATH.

Icon shows "?": The script connected to GlazeWM but couldn't find any active workspaces in the JSON data.

ModuleNotFoundError: Ensure you ran the pip install command using the same Python version you are using to run the script.
