"""Win32 icon extraction and fallback icon generation."""

import ctypes
from ctypes import wintypes
from PIL import Image, ImageDraw, ImageFont

from .win32 import (
    PROCESSENTRY32W, SHFILEINFOW, BITMAPINFOHEADER,
    TH32CS_SNAPPROCESS, MAX_PATH, DI_NORMAL,
    SHGFI_ICON, SHGFI_LARGEICON,
    PROCESS_QUERY_LIMITED_INFORMATION,
)


def _get_exe_path_for_process(process_name):
    """Find the full exe path for a process by name using toolhelp snapshots (Unicode)."""
    target = process_name.lower()
    if not target.endswith('.exe'):
        target += '.exe'

    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == -1:
        return None

    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)

        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return None

        while True:
            if entry.szExeFile.lower() == target:
                pid = entry.th32ProcessID
                handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if handle:
                    try:
                        buf = ctypes.create_unicode_buffer(MAX_PATH)
                        size = wintypes.DWORD(MAX_PATH)
                        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                            return buf.value
                    finally:
                        kernel32.CloseHandle(handle)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return None


def _hicon_to_pil(hicon, out_size=16):
    """Convert HICON to PIL Image using DrawIconEx into a DIB section."""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    render_size = 32

    hdc_screen = user32.GetDC(0)
    hdc = gdi32.CreateCompatibleDC(hdc_screen)

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = render_size
    bmi.biHeight = -render_size  # top-down
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0

    bits = ctypes.c_void_p()
    hbm = gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    if not hbm:
        gdi32.DeleteDC(hdc)
        user32.ReleaseDC(0, hdc_screen)
        return None

    old_bm = gdi32.SelectObject(hdc, hbm)

    user32.DrawIconEx(hdc, 0, 0, hicon, render_size, render_size, 0, 0, DI_NORMAL)

    buf_size = render_size * render_size * 4
    buf = (ctypes.c_byte * buf_size)()
    ctypes.memmove(buf, bits, buf_size)

    gdi32.SelectObject(hdc, old_bm)
    gdi32.DeleteObject(hbm)
    gdi32.DeleteDC(hdc)
    user32.ReleaseDC(0, hdc_screen)

    # BGRA -> RGBA
    raw = bytearray(buf)
    for i in range(0, len(raw), 4):
        raw[i], raw[i + 2] = raw[i + 2], raw[i]

    img = Image.frombytes('RGBA', (render_size, render_size), bytes(raw))

    if img.getextrema()[3][1] == 0:  # alpha channel max is 0
        return None

    if render_size != out_size:
        img = img.resize((out_size, out_size), Image.LANCZOS)
    return img


def _get_icon_via_shgetfileinfo(exe_path):
    """Get HICON using SHGetFileInfoW — reliable for most exe files."""
    info = SHFILEINFOW()
    result = ctypes.windll.shell32.SHGetFileInfoW(
        exe_path, 0, ctypes.byref(info), ctypes.sizeof(SHFILEINFOW),
        SHGFI_ICON | SHGFI_LARGEICON
    )
    if result and info.hIcon:
        return info.hIcon
    return None


def get_process_icon(process_name, size=16, _cache={}, _failures={}):
    """Get an app icon as a PIL Image for a given process name.
    Uses SHGetFileInfoW (most reliable) with ExtractIconExW as fallback.
    Retries up to 3 times for processes not yet ready."""
    if process_name in _cache:
        return _cache[process_name]

    if _failures.get(process_name, 0) >= 3:
        return None

    icon_img = None
    try:
        exe_path = _get_exe_path_for_process(process_name)
        if exe_path:
            # Method 1: SHGetFileInfoW (most reliable)
            hicon = _get_icon_via_shgetfileinfo(exe_path)
            if hicon:
                try:
                    icon_img = _hicon_to_pil(hicon, size)
                finally:
                    ctypes.windll.user32.DestroyIcon(hicon)

            # Method 2: ExtractIconExW (fallback - gets large icon)
            if not icon_img:
                hicon_large = wintypes.HICON()
                result = ctypes.windll.shell32.ExtractIconExW(
                    exe_path, 0, ctypes.byref(hicon_large), None, 1
                )
                if result > 0 and hicon_large.value:
                    try:
                        icon_img = _hicon_to_pil(hicon_large.value, size)
                    finally:
                        ctypes.windll.user32.DestroyIcon(hicon_large.value)
    except Exception as e:
        print(f"Icon extraction failed for {process_name}: {e}")

    if icon_img:
        _cache[process_name] = icon_img
    else:
        _failures[process_name] = _failures.get(process_name, 0) + 1
        if _failures[process_name] == 1:
            print(f"Icon not found for: {process_name}")
    return icon_img


_fallback_cache = {}


def make_fallback_icon(letter, size=16):
    """Create a small colored circle with a letter as fallback icon (cached)."""
    key = (letter, size)
    if key in _fallback_cache:
        return _fallback_cache[key]
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([1, 1, size - 2, size - 2], fill=(80, 80, 80, 200))
    try:
        font = ImageFont.truetype("arial.ttf", size - 6)
    except Exception:
        font = ImageFont.load_default()
    bbox = d.textbbox((0, 0), letter, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((size - tw) / 2, (size - th) / 2 - 1), letter, fill=(255, 255, 255), font=font)
    _fallback_cache[key] = img
    return img
