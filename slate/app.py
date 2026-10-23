"""The window and the API its page calls (window.pywebview.api.* in web/app.js)."""
import ctypes
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import webview

from slate import __version__, fonts, install, instance, navigate, repl, state, style, workspace

WEB = Path(__file__).with_name('web')
# WebView2 flags, measured with tools/bench.py on a 250% screen (8 tabs, console open): together about 220 MB less than plain GPU
# rendering at the same scroll and typing latency, with pixel-identical output. Low-end mode alone would also store textures at
# 4 bits per channel (#2B2B2B drawn as #333333), which --disable-rgba-4444-textures undoes; the GPU memory cap saves ~40 MB more.
LEAN = '--enable-low-end-device-mode --disable-rgba-4444-textures --force-gpu-mem-available-mb=64'
SOFTWARE = '--disable-gpu --disable-gpu-compositing'


class Api:
    def __init__(self):
        self._window = None
        self._state = state.load()
        self._console = repl.Console(self._console_emit)
        self._opens, self._ready, self._lock = [], False, threading.Lock()
        self._hwnd, self._dpi = None, None

    # session
    def get_state(self):
        return {'state': self._state, 'python': sys.version.split()[0], 'version': __version__, 'home': str(Path.home())}

    def save_state(self, new_state):
        new_state = dict(new_state)
        for key in ('placement', 'style_script', 'gpu', 'console_python'):  # set by hand or by Python, never by the page, so a page save must not drop them
            if key in self._state:
                new_state[key] = self._state[key]
        self._state = new_state
        state.save(self._state)

    def editor_fonts(self):
        return fonts.jetbrains_mono()

    def page_ready(self):
        """Called once the page has restored its session; returns the files and folders it was asked to open meanwhile."""
        with self._lock:
            self._ready = True
            opens, self._opens = self._opens, []
        return opens

    def _open(self, paths):
        items = [{'path': str(Path(p).resolve()), 'dir': Path(p).is_dir()} for p in paths if Path(p).exists()]
        with self._lock:
            if not self._ready:
                self._opens += items
                return
        if items:
            self._window.run_js(f'window.slate && slate.openPaths({json.dumps(items)})')
        instance.bring_to_front('slate')

    def set_background(self, background):
        """Ask WebView2 to trim its memory while slate is not the active window (MemoryUsageTargetLevel, WebView2's own API)."""
        from System import Func, Type  # pythonnet, loaded by pywebview by now
        from Microsoft.Web.WebView2.Core import CoreWebView2MemoryUsageTargetLevel as Level
        form = self._window.native

        def apply():
            core = form.browser.webview.CoreWebView2
            if core is not None:
                core.MemoryUsageTargetLevel = Level.Low if background else Level.Normal
        form.Invoke(Func[Type](apply))

    def set_dirty(self, dirty):
        self._window.confirm_close = bool(dirty)  # read by pywebview when the window is about to close

    def pick_folder(self):
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return result[0] if result else None

    # files
    def list_dir(self, path):
        return workspace.list_dir(path)

    def read_file(self, path):
        return workspace.read_file(path)

    def write_file(self, path, text, encoding = 'utf-8'):
        mtime = workspace.write_file(path, text, encoding)
        if path.endswith('.py'):
            style.saved(path)
        return mtime

    def stat_files(self, paths):
        return workspace.stat_files(paths)

    def create(self, path, is_dir = False):
        return workspace.create(path, is_dir)

    def list_files(self, roots):
        return workspace.list_files(roots)

    def search(self, roots, query, regex = False, case = False):
        return workspace.search(roots, query, regex, case)

    def reveal(self, path):
        subprocess.Popen(['explorer', '/select,', os.path.normpath(path)])

    # python
    def lint(self, path, text):
        return style.lint(path, text, self._state.get('style_script'))

    def format(self, path, text):
        return style.format_source(path, text, self._state.get('style_script'))

    def goto(self, path, text, line, col, roots):
        return navigate.goto(path, text, line, col, roots)

    # console
    def console_run(self, code, cwd = None, paths = ()):
        return self._console.run(code, cwd, list(paths), self._state.get('console_python'))

    def console_interrupt(self):
        self._console.interrupt()

    def console_restart(self, cwd = None, paths = ()):
        self._console.start(cwd, list(paths), self._state.get('console_python'))

    def _console_emit(self, batch):
        if self._window is not None:
            self._window.run_js(f'window.slate && slate.consoleEvents({json.dumps(batch)})')

    def _on_before_show(self):
        self._hwnd = self._window.native.Handle.ToInt64()  # before_show runs on the GUI thread, where the handle may be read
        if self._state.get('placement'):
            instance.set_placement(self._hwnd, self._state['placement'])

    def _on_shown(self):
        self._dpi = instance.monitor_dpi(self._hwnd)  # only DPI changes after this are the user's moves
        if (self._state.get('placement') or {}).get('maximized'):
            self._window.maximize()

    def _on_moved(self, x = None, y = None):
        if self._dpi:
            self._dpi = instance.follow_dpi(self._hwnd, self._dpi)

    def _on_closing(self):
        self._state['placement'] = instance.get_placement(self._hwnd)
        state.save(self._state)

    def _shutdown(self):
        self._console.stop()
        instance.release()


def main():
    if '--install' in sys.argv or '--uninstall' in sys.argv:
        return install.install() if '--install' in sys.argv else install.uninstall()
    paths = [os.path.abspath(a) for a in sys.argv[1:] if not a.startswith('--')]
    if instance.hand_off(paths):
        return  # a slate window is already open and took the files
    # Per-monitor DPI awareness (v2): WebView2 then renders at each monitor's own resolution. pywebview declares the process only
    # system-aware, which makes Windows bitmap-stretch the window on a monitor whose scaling differs from the main one (blurry text);
    # set first, pywebview's later SetProcessDPIAware has no effect.
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    api = Api()
    api._open(paths)
    instance.serve(api._open)
    # software rendering saves another ~100 MB, but on a 250% screen a keystroke took 52 ms to show instead of 22
    os.environ.setdefault('WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS', SOFTWARE if api._state.get('gpu') is False else LEAN)
    placed = 0 if api._state.get('placement') else None  # with a saved placement, a fixed start position keeps WinForms from centring over it
    # min_size is fixed in physical pixels at the start monitor's scaling, so it stays small enough to allow the same size on a 100% monitor
    window = webview.create_window('slate', url = str(WEB / 'index.html'), js_api = api, width = 1400, height = 900, x = placed, y = placed,
                                   min_size = (300, 200), background_color = '#2B2B2B', text_select = True,
                                   localization = {'global.quitConfirmation': 'Some tabs have unsaved changes. Close slate anyway?'})
    api._window = window
    window.events.before_show += api._on_before_show
    window.events.shown += api._on_shown
    window.events.moved += api._on_moved
    window.events.closing += api._on_closing
    window.events.closed += api._shutdown
    webview.start(http_server = True, debug = '--debug' in sys.argv, private_mode = False,
                  storage_path = str(state.STATE_FILE.parent / 'webview'), icon = str(WEB / 'slate.ico'))
