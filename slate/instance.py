"""One slate window: a second launch (double-clicking a .py file) hands its paths to the running slate and exits."""
import ctypes
import json
import os
import secrets
import socket
import threading
from ctypes import wintypes

from slate import state

user32 = ctypes.windll.user32
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]


def info_file():
    return state.STATE_FILE.parent / 'instance.json'  # looked up on each call, since the dev server moves STATE_FILE


def hand_off(paths):
    """True when a running slate took the paths, so this process can exit."""
    try:
        info = json.loads(info_file().read_text(encoding = 'utf-8'))
        user32.AllowSetForegroundWindow(info['pid'])  # only the process the user just started may bring a window to the front
        with socket.create_connection(('127.0.0.1', info['port']), timeout = 2) as conn:
            conn.sendall((json.dumps({'token': info['token'], 'open': paths}) + '\n').encode('utf-8'))
            return conn.recv(2) == b'ok'
    except (OSError, ValueError, KeyError):
        return False  # no slate running, or a stale file left by a crash


def serve(on_open):
    server = socket.create_server(('127.0.0.1', 0))
    token = secrets.token_hex(16)
    info_file().parent.mkdir(parents = True, exist_ok = True)
    info_file().write_text(json.dumps({'port': server.getsockname()[1], 'pid': os.getpid(), 'token': token}), encoding = 'utf-8')
    threading.Thread(target = accept, args = (server, token, on_open), daemon = True).start()


def accept(server, token, on_open):
    while True:
        conn, _ = server.accept()
        with conn:
            try:
                msg = json.loads(conn.makefile('r', encoding = 'utf-8').readline())
            except (OSError, ValueError):
                continue
            if not isinstance(msg, dict) or msg.get('token') != token:
                continue  # the token in the instance file keeps other local programs from driving the editor
            conn.sendall(b'ok')
        on_open(msg.get('open') or [])


def release():
    try:
        if json.loads(info_file().read_text(encoding = 'utf-8')).get('pid') == os.getpid():
            info_file().unlink()
    except (OSError, ValueError):
        pass


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [('length', wintypes.UINT), ('flags', wintypes.UINT), ('showCmd', wintypes.UINT), ('ptMinPosition', wintypes.POINT),
                ('ptMaxPosition', wintypes.POINT), ('rcNormalPosition', wintypes.RECT)]


user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HMONITOR
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
user32.SetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]


def get_placement(hwnd):
    """Where the window is, in physical pixels: pywebview's x/y are scaled by whichever monitor the window is on, so a
    position saved on a 100% monitor comes back off-screen on a 250% one."""
    p = WINDOWPLACEMENT()
    p.length = ctypes.sizeof(p)
    user32.GetWindowPlacement(hwnd, ctypes.byref(p))
    r = p.rcNormalPosition
    return {'rect': [r.left, r.top, r.right, r.bottom], 'maximized': p.showCmd == 3}


def set_placement(hwnd, saved):
    """Put the not-yet-shown window back; Windows pulls it on-screen if its monitor is gone."""
    p = WINDOWPLACEMENT()
    p.length = ctypes.sizeof(p)
    p.showCmd = 0  # SW_HIDE: pywebview shows the window right after
    p.rcNormalPosition = wintypes.RECT(*saved['rect'])
    user32.SetWindowPlacement(hwnd, ctypes.byref(p))


def monitor_dpi(hwnd):
    """DPI of the monitor holding most of the window. GetDpiForWindow lags: during pywebview's 'moved' event it still
    reports the monitor the window just left."""
    x, y = wintypes.UINT(), wintypes.UINT()
    ctypes.windll.shcore.GetDpiForMonitor(user32.MonitorFromWindow(hwnd, 2), 0, ctypes.byref(x), ctypes.byref(y))  # nearest monitor, effective DPI
    return x.value


def follow_dpi(hwnd, old_dpi):
    """Resize the window by the DPI ratio when it moved to a monitor with other scaling, as native apps do; WinForms
    ignores WM_DPICHANGED, so the window would otherwise keep its pixel size (2.5x too big after leaving a 250% screen)."""
    dpi = monitor_dpi(hwnd)
    if old_dpi and dpi != old_dpi and not user32.IsZoomed(hwnd):
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        scale = dpi / old_dpi
        user32.SetWindowPos(hwnd, None, 0, 0, round((r.right - r.left) * scale), round((r.bottom - r.top) * scale), 0x0002 | 0x0004 | 0x0010)  # NOMOVE, NOZORDER, NOACTIVATE
    return dpi


def bring_to_front(title):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid() and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value == title:
                found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    for hwnd in found:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE: back as it was, maximised or not
        user32.SetForegroundWindow(hwnd)
