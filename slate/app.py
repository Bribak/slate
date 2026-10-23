"""The window and the API its page calls (window.pywebview.api.* in web/app.js)."""
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from slate import __version__, fonts, glycans, hints, install, instance, navigate, repl, state, style, usage, workspace

WEB = Path(__file__).with_name('web')
# WebView2 flags, measured with tools/bench.py on a 250% screen (8 tabs, console open): together about 220 MB less than plain GPU
# rendering at the same scroll and typing latency, with pixel-identical output. Low-end mode alone would also store textures at
# 4 bits per channel (#2B2B2B drawn as #333333), which --disable-rgba-4444-textures undoes; the GPU memory cap saves ~40 MB more.
LEAN = '--enable-low-end-device-mode --disable-rgba-4444-textures --force-gpu-mem-available-mb=64'
SOFTWARE = '--disable-gpu --disable-gpu-compositing'
MIME_TYPES = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.ttf': 'font/ttf', '.ico': 'image/x-icon', '.png': 'image/png'}


class Api:
    def __init__(self):
        self._window = None
        self._state = state.load()
        usage.enabled = self._state.get('usage_log', True)
        self._console = repl.Console(self._console_emit)
        self._drawer = glycans.Drawer()
        self._opens, self._ready, self._lock = [], False, threading.Lock()
        self._hwnd, self._dpi = None, None
        self._started = time.time()

    # session
    def get_state(self):
        return {'state': self._state, 'python': sys.version.split()[0], 'version': __version__, 'home': str(Path.home())}

    def save_state(self, new_state):
        new_state = dict(new_state)
        for key in ('placement', 'style_script', 'gpu', 'console_python', 'usage_log', 'glycan_drawings'):  # set by hand or by Python, never by the page, so a page save must not drop them
            if key in self._state:
                new_state[key] = self._state[key]
        self._state = new_state
        state.save(self._state)

    def editor_fonts(self):
        return fonts.jetbrains_mono()

    def extensions(self):
        """[{name, code}] for each personal page script in %APPDATA%/slate/extensions (app.js loadExtensions). The folder is
        not part of slate, so what is in it reaches no one else."""
        folder = state.STATE_FILE.parent / 'extensions'
        return [{'name': f.name, 'code': f.read_text(encoding = 'utf-8')} for f in sorted(folder.glob('*.js'))] if folder.is_dir() else []

    def page_ready(self):
        """Called once the page can open files, before it restores the session; returns the files and folders it was asked to open meanwhile."""
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

    def log_usage(self, events):
        """The page's share of the usage log: call timings, keys and errors, sent about once a minute."""
        for event in events:
            usage.record(**event)

    def set_dirty(self, dirty):
        self._window.confirm_close = bool(dirty)  # read by pywebview when the window is about to close

    def pick_folder(self):
        import webview
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

    def hints(self, path, name, qualifier = None, roots = (), console = None):
        """Signatures and docstrings for a call or a name, from the open projects' own code (slate/hints.py)."""
        return hints.lookup(path, name, qualifier = qualifier, roots = list(roots), console = console)

    def goto(self, path, text, line, col, roots):
        return navigate.goto(path, text, line, col, roots)

    def glycans(self, names, roots = ()):
        """GlycoDraw's drawings of glycans written in the code, from the console's Python (slate/glycans.py)."""
        return self._drawer.draw(list(names), self._state.get('console_python'), list(roots))

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
        usage.record('shown', dpi = self._dpi, seconds = round(time.time() - self._started, 2))
        if (self._state.get('placement') or {}).get('maximized'):
            self._window.maximize()

    def _on_moved(self, x = None, y = None):
        if self._dpi:
            old, self._dpi = self._dpi, instance.follow_dpi(self._hwnd, self._dpi)
            if self._dpi != old:
                usage.record('monitor', dpi = self._dpi, previous = old)

    def _on_maximized(self):
        usage.record('maximized', dpi = self._dpi)

    def _on_restored(self):
        usage.record('restored', dpi = self._dpi)

    def _on_closing(self):
        self._state['placement'] = instance.get_placement(self._hwnd)
        state.save(self._state)

    def _shutdown(self):
        usage.record('end', seconds = round(time.time() - self._started))
        self._console.stop()
        self._drawer.stop()
        instance.release()


def serve_pages():
    """Adjust how pywebview's server (bottle) sends slate's page files.
    * It asks for no caching, but bottle.static_file replaces the response and drops those headers, so WebView2 kept app.js by
      heuristic (a tenth of the file's age at download) and could run the old page after an update. 'no-cache' makes it ask
      each time; unchanged files come back as 304, so its compiled-code cache still applies.
    * The content type comes from MIME_TYPES: mimetypes would read the Windows registry on the first request, which held up
      index.html by 105 ms."""
    import bottle
    static_file = bottle.static_file

    def serve(filename, *a, **kw):
        kw.setdefault('mimetype', MIME_TYPES.get(os.path.splitext(filename)[1].lower(), True))
        return static_file(filename, *a, headers = {'Cache-Control': 'no-cache'}, **kw)
    bottle.static_file = serve


def main():
    if '--install' in sys.argv or '--uninstall' in sys.argv:
        return install.install() if '--install' in sys.argv else install.uninstall()
    paths = [os.path.abspath(a) for a in sys.argv[1:] if not a.startswith('--')]
    if instance.hand_off(paths):
        return  # a slate window is already open and took the files
    import webview  # only now, so a launch that hands its files to the running slate is done in 255 ms instead of 790
    serve_pages()
    # Per-monitor DPI awareness (v2): WebView2 then renders at each monitor's own resolution. pywebview declares the process only
    # system-aware, which makes Windows bitmap-stretch the window on a monitor whose scaling differs from the main one (blurry text);
    # set first, pywebview's later SetProcessDPIAware has no effect.
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    api = Api()
    usage.record('start', version = __version__, python = sys.version.split()[0], gpu = api._state.get('gpu', True), files = len(paths),
                 console_python = bool(api._state.get('console_python')))
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
    window.events.maximized += api._on_maximized
    window.events.restored += api._on_restored
    window.events.closing += api._on_closing
    window.events.closed += api._shutdown
    webview.start(http_server = True, debug = '--debug' in sys.argv, private_mode = False,
                  storage_path = str(state.STATE_FILE.parent / 'webview'), icon = str(WEB / 'slate.ico'))
