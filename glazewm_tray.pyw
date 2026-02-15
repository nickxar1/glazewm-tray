"""GlazeWM Tray Indicator — entry point.

Usage:
    python run.py          Run with console output (for testing/debugging)
    Rename to run.pyw      Run silently in the background (no console window)
"""

import sys
from glazewm_tray.app import GlazeTrayApp

if __name__ == "__main__":
    try:
        GlazeTrayApp().run()
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)
