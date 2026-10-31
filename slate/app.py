"""The window and the API its page calls (window.pywebview.api.* in web/app.js)."""
import ctypes
import json
import os
import sys
import threading
import time
from pathlib import Path

from slate import __version__, fonts, glycans, glytoucan, hints, history, instance, libraries, linkages, navigate, pythons, references, repl, state, style, system, update, usage, workspace
from slate.system import LINUX, MAC, WINDOWS, native

WEB = Path(__file__).with_name('web')
# WebView2 flags, measured with tools/bench.py on a 250% screen (8 tabs, console open): together about 220 MB less than plain GPU
# rendering at the same scroll and typing latency, with pixel-identical output. Low-end mode alone would also store textures at
# 4 bits per channel (#2B2B2B drawn as #333333), which --disable-rgba-4444-textures undoes. The GPU memory cap was 64 MB until
# 2026-10-25: too little for a maximized window at 250%, where moving the mouse into the editor drew frames with its tiles missing
# (the editor blank for a frame); 192 MB had no shortfall. Paid for by two savings: --ui-enable-rgba-4444-textures stores the
# browser's own compositor at 16 bits per pixel, which halves a full-window layer WebView2 draws under the page and nobody sees
# (35.6 -> 17.8 MB at 250% maximized; only its own dialogs, such as confirm(), lose some anti-aliasing shades), and the network
# service in the browser process saves its process (about 12 MB). Net, at rest: about 10 MB less than with 64 (CLAUDE.md)
LEAN = ('--enable-low-end-device-mode --disable-rgba-4444-textures --force-gpu-mem-available-mb=192 --ui-enable-rgba-4444-textures'
        ' --enable-features=NetworkServiceInProcess2')
SOFTWARE = '--disable-gpu --disable-gpu-compositing'
SETTINGS = ('console_python', 'console_pythons', 'style_script', 'gpu', 'usage_log', 'glycan_drawings', 'update_check', 'reference_lookup')  # set_setting's keys
MIME_TYPES = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.ttf': 'font/ttf', '.ico': 'image/x-icon', '.png': 'image/png'}
PORT = 42001  # pywebview's default for its page server: a fixed port keeps the page's origin, and with it WebView2's caches (Monaco's compiled code), across sessions
STALL = 15  # seconds after the window shows by which the page has asked for its state (it takes about 2)
RELOADS = 3  # times _watch_page loads a stalled page again, STALL seconds apart


def project_key(root):
    """app.js keyOf, which keys console_pythons: in lower case, except on Linux, where file names differ by case."""
    key = root.replace('/', '\\').rstrip('\\')
    return key if LINUX else key.lower()


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
        self._port, self._asked = None, threading.Event()

    # session
    def get_state(self):
        if not self._asked.is_set():  # the page's first call: logged at once, since the page's own events wait for a blur or a minute
            usage.record('page', seconds = round(time.time() - self._started, 2))
        self._asked.set()
        return {'state': self._state, 'python': sys.version.split()[0], 'version': __version__, 'home': str(Path.home()), 'sep': os.sep, 'mac': MAC, 'linux': LINUX}

    def save_state(self, new_state):
        new_state = dict(new_state)
        for key in ('placement', 'update', 'notes_seen') + SETTINGS:  # set by hand or by Python, never by the page's session saves, which must not drop them
            if key in self._state:
                new_state[key] = self._state[key]
        self._state = new_state
        state.save(self._state)

    def settings(self, roots = ()):
        """What the settings panel shows: the values of SETTINGS and the Pythons found on this machine (slate/pythons.py)."""
        return {'values': {key: self._state.get(key) for key in SETTINGS}, 'pythons': pythons.find(list(roots)), 'version': __version__,
                'own': str(system.console_python(sys.executable)), 'state_file': str(state.STATE_FILE)}

    def set_setting(self, key, value):
        """One of SETTINGS from the settings panel; None removes it, which means the default."""
        if key not in SETTINGS:
            raise ValueError(f'{key} is not a setting')
        if value is None:
            self._state.pop(key, None)
        else:
            self._state[key] = value
        if key == 'usage_log':
            usage.enabled = value is not False
        state.save(self._state)

    def pick_python(self):
        import webview
        types = ('Python (python.exe)', 'All files (*.*)') if WINDOWS else ()  # python3 has no extension to filter by on macOS and Linux
        result = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types = types)
        return result[0] if result else None

    def update_check(self):
        """The newer release of slate on GitHub, or None. Asks GitHub at most once a day, and never for a checkout installed
        with pip install -e (slate/update.py)."""
        source = None if self._state.get('update_check') is False else update.origin()
        if source is None:
            return None
        cache = self._state.get('update') or {}
        if time.time() - cache.get('checked', 0) > update.EVERY:
            try:
                cache = {'checked': round(time.time()), 'latest': update.latest(source)}
            except OSError:
                return None  # offline: ask again at the next start
            self._state['update'] = cache
            state.save(self._state)
        return cache['latest'] if update.newer(cache.get('latest')) else None

    def update_notes(self, version):
        """Markdown: what changed from this version up to release `version`, or None when GitHub cannot be reached."""
        source = update.origin()
        try:
            return source and update.notes(source, __version__, version)
        except (OSError, ValueError, KeyError):
            return None

    def whats_new(self):
        """{version, notes} on the first start of a release newer than the one last shown, once; else None. A fresh install (no
        update check made yet) only records its version. Offline, it waits for the next start."""
        source = None if self._state.get('update_check') is False else update.origin()
        seen = self._state.get('notes_seen')
        if source is None or seen == __version__:
            return None
        found = None
        if 'update' in self._state and not (seen and update.parts(seen) >= update.parts(__version__)):
            try:
                found = {'version': __version__, 'notes': update.notes(source, seen, __version__)}
            except (OSError, ValueError, KeyError):
                return None
        self._state['notes_seen'] = __version__
        state.save(self._state)
        return found

    def update_install(self, version):
        """Install release `version` (from update_check) with pip; returns the version now installed, which runs from the next start."""
        return update.install(version)

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

    def _open(self, paths, activation = None):
        """activation: the token a second launch on Linux was started with, for the window to come forward (slate/linux.py)."""
        items = [{'path': str(Path(p).resolve()), 'dir': Path(p).is_dir()} for p in paths if Path(p).exists()]
        with self._lock:
            if not self._ready:
                self._opens += items
                return
        if items:
            self._push('openPaths', items)
        native.bring_to_front('slate', activation)

    def _push(self, name, value):
        """Call window.slate[name](value) in the page. On Windows as a web message: a script per push, as run_js runs it,
        stays in the page's heap for good, and console batches carry figures (slate/win.py post)."""
        if not WINDOWS or not hasattr(self._window, 'native'):  # macOS, Linux, and the dev server's stand-in window
            self._window.run_js(f'window.slate && slate.{name}({json.dumps(value)})')
        else:
            native.post(self._window, f'slate-push\n{name}\n{json.dumps(value)}')

    def set_background(self, background):
        """Ask WebView2 to trim its memory while slate is not the active window (MemoryUsageTargetLevel, WebView2's own API).
        WKWebView on macOS and WebKitGTK on Linux have no such setting."""
        if not WINDOWS:
            return
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
        mtime = workspace.write_file(path, text, encoding, before = history.keep)
        history.wrote(path)
        if path.endswith('.py'):
            style.saved(path)
        return mtime

    def history(self, path):
        """Local history: the versions saves replaced, newest first (slate/history.py)."""
        return history.versions(path)

    def history_text(self, path, version):
        return history.read(path, version)

    def stat_files(self, paths):
        return workspace.stat_files(paths)

    def create(self, path, is_dir = False):
        return workspace.create(path, is_dir)

    def rename(self, path, name):
        return workspace.rename(path, name)

    def trash(self, path):
        """Move a file or folder to the Recycle Bin or the Trash, from where it can be restored."""
        native.trash(path)

    def list_files(self, roots):
        return workspace.list_files(roots)

    def search(self, roots, query, regex = False, case = False):
        return workspace.search(roots, query, regex, case)

    def reveal(self, path):
        system.reveal(path)

    # python
    def lint(self, path, text, glycan_check = True):
        """glycan_check: whether the page asks for the glycan check of this text next, which reuses the lint's tree."""
        return style.lint(path, text, self._state.get('style_script'), glycan_check = glycan_check)

    def format(self, path, text):
        return style.format_source(path, text, self._state.get('style_script'))

    def _libraries(self, path, roots):
        """{package: folder} of the lab's libraries (libraries.NAMES) as installed for the Python that runs path's project (the console's
        running one for the console), when their checkouts are not open (slate/libraries.py)."""
        root = next((r for r in roots if project_key(path or '').startswith(project_key(r) + '\\')), None) if path else None
        python = (self._console.python if path is None else None) or self._python_for(root)
        return libraries.installed(str(repl.Console.executable(python)), list(roots))

    def hints(self, path, name, qualifier = None, roots = (), console = None):
        """Signatures and docstrings for a call or a name, from the open projects' own code (slate/hints.py)."""
        return hints.lookup(path, name, qualifier = qualifier, roots = list(roots), console = console, libraries = self._libraries(path, roots))

    def goto(self, path, text, line, col, roots):
        return navigate.goto(path, text, line, col, roots, libraries = self._libraries(path, roots))

    def outline(self, text):
        return navigate.outline(text)

    def glycans(self, names, roots = ()):
        """GlycoDraw's drawings of glycans written in the code, from the console's Python (slate/glycans.py)."""
        return self._drawer.draw(list(names), self._state.get('console_python'), list(roots))

    def glycan_full(self, glycan, roots = ()):
        """{'svg', 'sticker'}: GlycoDraw's full drawing, with linkages, for the hover, or {'error': message}."""
        return self._drawer.request({'op': 'full', 'glycan': glycan}, self._state.get('console_python'), list(roots))

    def glycan_highlight(self, glycan, motif, roots = ()):
        """glycan_full's drawing with a motif of glycowork's list highlighted, as the hover card's and table panel's motif names show it."""
        return self._drawer.request({'op': 'highlight', 'glycan': glycan, 'motif': motif}, self._state.get('console_python'), list(roots))

    def glycan_info(self, glycan, roots = ()):
        """The hover card: canonical form, mass, composition, class and glycowork's motifs (slate/glycan_child.py)."""
        return self._drawer.request({'op': 'info', 'glycan': glycan}, self._state.get('console_python'), list(roots))

    def glycan_check(self, path, text, roots = ()):
        """Diagnostics with quick fixes for the glycans written as values in a file (slate/glycans.py, `literals`)."""
        return glycans.diagnostics(self._drawer, path, text, self._state.get('console_python'), list(roots))

    def glycan_search(self, roots, query):
        """Find-in-files results for the glycans in the open folders that hold a motif, substructure or glyco-regex."""
        return glycans.search(self._drawer, list(roots), query, self._state.get('console_python'), list(roots))

    def glycan_motifs(self, roots = ()):
        return self._drawer.request({'op': 'motifs'}, self._state.get('console_python'), list(roots)).get('motifs', [])

    def glytoucan(self, ids, roots = ()):
        """{id: IUPAC-condensed glycan or None} for GlyTouCan IDs, from the glycowork the drawings come from (slate/glytoucan.py)."""
        return glytoucan.lookup(list(ids), self._state.get('console_python'), list(roots))

    def glycan_typing(self, roots = ()):
        """What follows what in glycowork's glycans, for suggestions while one is typed (slate/linkages.py); None without glycowork."""
        return linkages.counts(self._state.get('console_python'), list(roots))

    def reference(self, kind, ident):
        """The paper a PMID or DOI names (slate/references.py); None when offline or turned off."""
        return None if self._state.get('reference_lookup') is False else references.lookup(kind, ident)

    def open_url(self, url):
        """Opens a PubMed record or a DOI from a reference hover in the default browser."""
        if references.link(url):
            import webbrowser
            webbrowser.open(url)

    # console
    def _python_for(self, root):
        """The console's Python for a project folder: the one set for it, else the default, else None (slate's own)."""
        own = self._state.get('console_pythons') or {}
        return (root and own.get(project_key(root))) or self._state.get('console_python')

    def console_run(self, code, cwd = None, paths = (), source = None):
        """cwd: the project folder the code belongs to, whose Python runs it. source: {file, line, text, whole} for code from a
        file in the editor (repl_child.run_file)."""
        return self._console.run(code, cwd, list(paths), self._python_for(cwd), source)

    def console_interrupt(self):
        self._console.interrupt()

    def console_restart(self, cwd = None, paths = ()):
        self._console.start(cwd, list(paths), self._python_for(cwd))

    def _console_emit(self, batch):
        if self._window is not None:
            self._push('consoleEvents', batch)

    def _on_before_show(self):
        if MAC:  # before_show and closing run on the GUI thread, where the NSWindow may be used
            if self._state.get('placement'):
                native.set_placement(self._window.native, self._state['placement'])
            return
        if LINUX:  # on the GTK thread too; this also starts following the window's size (slate/linux.py)
            native.set_placement(self._window.native, self._state.get('placement'))
            return
        self._hwnd = self._window.native.Handle.ToInt64()  # before_show runs on the GUI thread, where the handle may be read
        if self._state.get('placement'):
            native.set_placement(self._hwnd, self._state['placement'])
        native.reload_on_crash(self._window.native, lambda **fields: usage.record('renderer_failed', **fields))

    def _on_shown(self):
        self._dpi = native.monitor_dpi(self._hwnd) if WINDOWS else None  # only DPI changes after this are the user's moves; macOS and GTK rescale by themselves
        usage.record('shown', dpi = self._dpi, seconds = round(time.time() - self._started, 2))
        if (self._state.get('placement') or {}).get('maximized'):
            self._window.maximize()
        threading.Thread(target = self._watch_page, daemon = True).start()

    def _watch_page(self):
        """Load the page again each STALL seconds after the window showed, up to RELOADS times, while it has not asked for its
        state, rather than leave the window blank, and log whether its server answers (page_stalled). Once was not enough on
        macOS 15 (CI, 2026-10-31): the server answered neither the first load nor the reload 17 s later."""
        from urllib.request import urlopen
        url = f'http://127.0.0.1:{self._port or PORT}/index.html'
        for attempt in range(1, RELOADS + 1):
            if self._asked.wait(STALL) or self._started is None:
                return
            try:
                with urlopen(url, timeout = 3) as response:
                    served = response.status
            except OSError as e:
                served = repr(e)
            usage.record('page_stalled', seconds = STALL * attempt, port = self._port, served = served)
            try:
                self._window.load_url(url)
            except Exception:
                return  # the window closed meanwhile

    def _on_moved(self, x = None, y = None):
        if self._dpi:
            old, self._dpi = self._dpi, native.follow_dpi(self._hwnd, self._dpi)
            if self._dpi != old:
                usage.record('monitor', dpi = self._dpi, previous = old)

    def _on_maximized(self):
        usage.record('maximized', dpi = self._dpi)

    def _on_restored(self):
        usage.record('restored', dpi = self._dpi)

    def _on_closing(self):
        self._state['placement'] = native.get_placement(self._hwnd if WINDOWS else self._window.native)
        state.save(self._state)

    def _shutdown(self):
        if self._started is None:
            return  # quitting from the macOS menu ends the process without closing the window, so this may come twice
        usage.record('end', seconds = round(time.time() - self._started))
        self._started = None
        self._console.stop()
        self._drawer.stop()
        instance.release()


def serve_pages():
    """Adjust how pywebview's server (bottle) sends slate's page files.
    * It asks for no caching, but bottle.static_file replaces the response and drops those headers, so WebView2 kept app.js by
      heuristic (a tenth of the file's age at download) and could run the old page after an update. 'no-cache' makes it ask
      each time; unchanged files come back as 304, so its compiled-code cache still applies.
    * The content type comes from MIME_TYPES: mimetypes would read the Windows registry on the first request, which held up
      index.html by 105 ms.
    * The server is named after its address. http.server looks its address up in DNS (socket.getfqdn) after binding and before
      it listens, and where that lookup hangs nothing answers the page: on GitHub's macOS 15 machine (2026-10-31) slate's own
      request to its page server timed out 15 s after the window showed, and so did WebKit's load and its reload. The name only
      goes into the WSGI environment's SERVER_NAME, which nothing here reads."""
    import bottle
    import http.server
    import socketserver
    static_file = bottle.static_file

    def serve(filename, *a, **kw):
        kw.setdefault('mimetype', MIME_TYPES.get(os.path.splitext(filename)[1].lower(), True))
        return static_file(filename, *a, headers = {'Cache-Control': 'no-cache'}, **kw)
    bottle.static_file = serve

    def server_bind(server):
        socketserver.TCPServer.server_bind(server)
        server.server_name, server.server_port = server.server_address[:2]
    http.server.HTTPServer.server_bind = server_bind


def server_port():
    """The port for pywebview's page server: PORT, or the next one nobody listens on. pywebview binds with SO_REUSEADDR, which
    on Windows lets a second server listen on a port already in use, and the page's requests then go to the first one: a
    process still listening there, such as a slate that is still shutting down, leaves the new window blank (2026-10-31: blank
    on reopening 8 s after closing a two-day session; reproduced with a listener that never answers, 3 of 3 requests hung).
    A plain bind fails while anything listens on the port, but not for the TIME_WAIT connections a closed slate leaves."""
    import socket
    for port in range(PORT, PORT + 20):
        with socket.socket() as s:
            if not WINDOWS:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # elsewhere TIME_WAIT would block a plain bind, and a listener blocks this one too
            try:
                s.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    return None  # pywebview's default


def main():
    if '--install' in sys.argv or '--uninstall' in sys.argv:  # --no-settings: an update refreshing slate.exe or slate.app (slate/update.py)
        installer = system.installer()
        return installer.install('--no-settings' not in sys.argv) if '--install' in sys.argv else installer.uninstall()
    paths = [os.path.abspath(a) for a in sys.argv[1:] if not a.startswith('--')]
    if instance.hand_off(paths):
        return  # a slate window is already open and took the files
    import webview  # only now, so a launch that hands its files to the running slate is done in 255 ms instead of 790
    serve_pages()
    if WINDOWS:
        # Per-monitor DPI awareness (v2): WebView2 then renders at each monitor's own resolution. pywebview declares the process only
        # system-aware, which makes Windows bitmap-stretch the window on a monitor whose scaling differs from the main one (blurry text);
        # set first, pywebview's later SetProcessDPIAware has no effect.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        native.set_app_id()  # before the window exists: the taskbar reads it then
        native.answer_by_message()  # pywebview's answers by script stayed in the page's heap until it crashed (slate/win.py)
    api = Api()
    usage.record('start', version = __version__, python = sys.version.split()[0], gpu = api._state.get('gpu', True), files = len(paths),
                 console_python = bool(api._state.get('console_python')))
    api._open(paths)
    instance.serve(api._open)
    if MAC:
        native.prepare(api._open, api._shutdown)  # Finder's files, Cmd keys for the page, the app's name (slate/mac.py)
    elif LINUX:
        native.prepare()  # the program's name, which the desktop entry matches (slate/linux.py)
    else:
        # software rendering saves another ~100 MB, but on a 250% screen a keystroke took 52 ms to show instead of 22
        os.environ.setdefault('WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS', SOFTWARE if api._state.get('gpu') is False else LEAN)
    placed = 0 if api._state.get('placement') and WINDOWS else None  # with a saved placement, a fixed start position keeps WinForms from centring over it
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
    api._port = server_port()
    if api._port != PORT:
        usage.record('port', port = api._port)
    webview.start(gui = 'gtk' if LINUX else None, http_server = True, http_port = api._port, debug = '--debug' in sys.argv, private_mode = False,
                  storage_path = str(state.STATE_FILE.parent / 'webview'), icon = str(WEB / ('slate.ico' if WINDOWS else 'slate.png')))  # the Dock icon on macOS
