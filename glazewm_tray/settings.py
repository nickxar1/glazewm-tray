"""Persistent settings — reads/writes settings.ini next to run.py."""

import os
import configparser

_SETTINGS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'settings.ini'
)

_DEFAULTS = {
    'auto_toggle_tiling': True,
    'icons_only': False,
    'position_right': True,
    'transparent': True,
    'bar_hidden': False,
    'label_left': True,
}


def load():
    """Load settings.ini and return a dict of bools. Falls back to defaults."""
    cfg = configparser.ConfigParser()
    cfg.read(_SETTINGS_FILE)
    result = {}
    for key, default in _DEFAULTS.items():
        raw = cfg.get('glazewm', key, fallback=str(default).lower())
        result[key] = raw.strip().lower() == 'true'
    return result


def save(data):
    """Write the given settings dict to settings.ini."""
    cfg = configparser.ConfigParser()
    cfg['glazewm'] = {k: str(v).lower() for k, v in data.items()}
    try:
        with open(_SETTINGS_FILE, 'w') as f:
            cfg.write(f)
    except Exception as e:
        print(f"Settings save error: {e}")
