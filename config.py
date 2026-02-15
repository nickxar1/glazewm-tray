# ============================================================
# GlazeWM Tray — User Configuration
# ============================================================
# Edit the settings below to customize the tray tool.
# This is the ONLY file you need to modify.
# ============================================================

# Color scheme (RGB tuples)
COLORS = {
    "bg": (20, 20, 20),          # Background color of the icon
    "text": (255, 255, 255),      # Color of workspace numbers
    "active": (66, 192, 251),     # Highlight color for focused workspace
    "inactive": (100, 100, 100),  # Color for empty workspaces
    "error": (255, 100, 100)      # Color for error indicator
}

# GlazeWM WebSocket URL
GLAZEWM_WS_URL = "ws://127.0.0.1:6123"

# Automatically toggle tiling direction when a new window opens
AUTO_TOGGLE_TILING = True

# Seconds to wait after burst events (window open/close) before querying
QUERY_DEBOUNCE = 0.3

# Display modes (both enabled by default)
USE_FLOATING_BAR = True  # Floating bar on the taskbar
USE_TRAY_ICON = True     # System tray icon

# Floating bar background: None = transparent, or (r, g, b) e.g. (20, 20, 20)
BAR_BG_COLOR = None

# Events to subscribe to from GlazeWM
SUBSCRIBE_EVENTS = [
    "focus_changed", "workspace_activated", "workspace_deactivated",
    "workspace_updated", "window_managed", "window_unmanaged",
    "tiling_direction_changed", "binding_modes_changed",
    "focused_container_moved", "pause_changed"
]

# Events that refresh immediately (no debounce delay)
IMMEDIATE_EVENTS = frozenset({
    'focus_changed', 'workspace_activated',
    'workspace_deactivated', 'workspace_updated',
    'focused_container_moved', 'tiling_direction_changed',
    'binding_modes_changed', 'pause_changed',
})
