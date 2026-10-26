"""The Windows side of slate's window: placement in physical pixels, following DPI between monitors, coming to the front,
and answering the page's API calls (app.py; slate/mac.py is the macOS side)."""
import ctypes
import json
import os
import threading
import time
import traceback
from ctypes import wintypes

user32 = ctypes.windll.user32
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [('hwnd', wintypes.HWND), ('wFunc', wintypes.UINT), ('pFrom', wintypes.LPCWSTR), ('pTo', wintypes.LPCWSTR), ('fFlags', ctypes.c_ushort),
                ('fAnyOperationsAborted', wintypes.BOOL), ('hNameMappings', ctypes.c_void_p), ('lpszProgressTitle', wintypes.LPCWSTR)]


APP_ID = 'io.github.bribak.slate'  # the AppUserModelID, which slate/install.py also gives the Start menu shortcut


def set_app_id():
    """Group slate's window on the taskbar as slate rather than as pythonw.exe, which showed Python's icon there; with the
    Start menu shortcut carrying the same ID, the taskbar takes its icon and name, and a pinned slate is the running one."""
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)


def allow_foreground(pid):
    user32.AllowSetForegroundWindow(pid)  # only the process the user just started may bring a window to the front


def post(window, text):
    """Hand text to the page as a WebView2 web message (app.js listens for them). Not a script: WebView2 keeps every script
    run in the page (evaluate_js, run_js) for as long as the page lives, with whatever data it carries as a literal."""
    from System import Action  # pythonnet, loaded by pywebview by now
    view = window.native.webview
    if view.IsDisposed:
        return  # the window closed; Invoke would throw an exception pythonnet cannot wrap, ending the process
    try:
        view.Invoke(Action(lambda: view.CoreWebView2.PostWebMessageAsString(text)))
    except Exception:
        pass  # closed meanwhile


def reload_on_crash(form, record):
    """Reload the page when its renderer process dies, as WebView2's documentation advises; it would otherwise stay blank
    until slate restarts. The page restores the saved session; unsaved text was lost with the renderer. At most once a
    minute, so a page that crashes as it loads cannot loop. record(process, reason, reloaded) goes to the usage log.
    Runs on the GUI thread (before_show), where WebView2 may be touched. An exception in the handler vanishes silently."""
    from Microsoft.Web.WebView2.Core import CoreWebView2ProcessFailedKind
    view, last = form.webview, [0.0]

    def failed(core, args):
        reload = args.ProcessFailedKind == CoreWebView2ProcessFailedKind.RenderProcessExited and time.monotonic() - last[0] > 60
        record(process = str(args.ProcessFailedKind), reason = str(args.Reason), reloaded = reload)
        if reload:
            last[0] = time.monotonic()
            core.Reload()

    if view.CoreWebView2 is not None:
        view.CoreWebView2.ProcessFailed += failed
    else:
        def ready(sender, args):
            if view.CoreWebView2 is not None:
                view.CoreWebView2.ProcessFailed += failed
        view.CoreWebView2InitializationCompleted += ready


def answer_by_message():
    """Send the answers to the page's API calls by post, which app.js hands to pywebview's callbacks. pywebview answers
    each call with evaluate_js, a script holding the answer as a literal, and those scripts never left the page's heap: 30
    garbage collections later, 40 reads of a 442 KB file still held 37 MB. With --enable-low-end-device-mode the page's
    heap is capped at 256 MB, so a session that reread big files (the Changes extension on every focus) crashed the renderer
    and left a blank window (2026-10-26). pywebview's own calls (pywebview*) keep its way. Called before webview.start,
    which imports the WebView2 backend and with it js_bridge_call."""
    from webview import util
    original = util.js_bridge_call

    def answer(window, func, name, params, call_id):
        try:
            text, failed = json.dumps(func(*params)), '0'
        except Exception as e:
            util.logger.error(traceback.format_exc())
            text, failed = json.dumps({'message': str(e), 'name': type(e).__name__, 'stack': traceback.format_exc()}), '1'
        post(window, f'slate-answer\n{name}\n{call_id}\n{failed}\n{text}')

    def bridge_call(window, name, params, call_id):
        func = None if name.startswith('pywebview') else getattr(window._js_api, name, None)
        if not callable(func):
            return original(window, name, params, call_id)
        threading.Thread(target = answer, args = (window, func, name, params, call_id)).start()
    util.js_bridge_call = bridge_call


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


def trash(path):
    """Move a file or folder to the Recycle Bin without Explorer's dialogs. FOF_WANTNUKEWARNING: where there is no Recycle
    Bin (a network drive), Windows asks before deleting for good rather than doing it silently."""
    op = SHFILEOPSTRUCTW(wFunc = 3, pFrom = os.path.abspath(path) + '\0', fFlags = 0x40 | 0x10 | 0x4 | 0x400 | 0x4000)  # FO_DELETE; FOF_ALLOWUNDO, NOCONFIRMATION, SILENT, NOERRORUI, WANTNUKEWARNING
    error = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if error or op.fAnyOperationsAborted:
        raise OSError(f'{os.path.basename(path)} was not moved to the Recycle Bin (error {error:#x})')
