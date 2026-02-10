# 🪟 GlazeWM Tray Indicator

A lightweight, minimal system tray utility for **GlazeWM**. This tool replaces the need for a bulky status bar by providing workspace information and window management directly from the Windows System Tray.

---

## ✨ Features

* **Multi-Workspace View:** Displays numbers for all workspaces currently containing open windows.
* **Focus Tracking:** Highlights your active workspace with a blue underline.
* **Occupancy Indicators:** In the menu, workspaces with windows are marked with a `●` symbol.
* **Quick Actions:** Right-click to toggle floating, close windows, or reload your GlazeWM configuration.
* **Click-to-Switch:** Instantly jump to any workspace by selecting it from the tray menu.

---

## 🛠️ Prerequisites

This tool requires **Python 3.x**. Open your terminal and run the following command to install the necessary libraries:
```powershell
pip install pystray pillow
```
---

## 🏃 How to Run

### 1. The Regular Way (For Testing)

Run the script using the standard Python command. This will keep a command prompt window open, which is helpful for seeing any error messages:
```powershell
python glaze_tray.py
```

### 2. The "Silent" Way (Background Mode)

To run the tool without a command prompt window cluttering your taskbar:

1. Rename your file from `glaze_tray.py` to `glaze_tray.pyw`.
2. Double-click the `.pyw` file.
3. The script will now run invisibly in the background, appearing only as an icon in your System Tray.

### 3. How to Stop the Tool

* **If running as .py:** Close the command prompt window.
* **If running as .pyw:** Right-click the icon in the System Tray and select Exit Tray Tool.

---

## ⚙️ Start Automatically with Windows

To have your tray indicator start every time you log in:

1. Press `Win + R`, type `shell:startup`, and press Enter.
2. Right-click your `glaze_tray.pyw` file and select Create Shortcut.
3. Move that shortcut into the Startup folder you just opened.

---

## 🎨 Customization

You can adjust the appearance by editing the `COLORS` dictionary at the top of the `.pyw` file:

* `bg`: Background color of the icon square.
* `text`: Color of the workspace numbers.
* `active`: Color of the highlight/underline for the focused workspace.

---

## ⚠️ Troubleshooting

* **Icon shows "Err":** The script cannot find the `glazewm` command. Make sure GlazeWM is installed and added to your System PATH.
* **Icon shows "?":** The script is running, but GlazeWM is not reporting any active monitors or workspaces.
* **Import Errors:** If Python says "No module named pystray," re-run the `pip install` command specifically for your version: `python -m pip install pystray pillow`.
