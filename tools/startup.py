"""Time a start that opens a file, as a double-click in Explorer does, in an isolated copy of the real session.

python tools/startup.py LABEL FILE [RUNS] [--tabs N] [--behind]

Copies %APPDATA%\\slate\\state.json (tabs, folders; autosave off) to %TEMP%\\slate-startup\\LABEL and starts slate RUNS times
(default 5) with FILE, each in a fresh process. Prints one JSON line per run, in ms from that process's start: slate's imports
done, Api created, window shown, the page's navigation start, the moment FILE is the front tab (file_ms), the session restored
(ready_ms), every tab's style check done (lints_ms), and the private working set of slate's processes 3 s later (ram_mb,
in MB). The first run of a new LABEL also creates its WebView2 profile, so
compare the later ones. A slate that is already open is left alone.

--tabs N keeps only the first N saved tabs. --behind keeps the window behind every other one, unfocused and still drawing, as
tools/bench.py --behind does, so it can run while someone works.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(tempfile.gettempdir()) / 'slate-startup'
POLL = """(async () => JSON.stringify({at: performance.timeOrigin + performance.now(), origin: performance.timeOrigin,
  active: typeof editor !== 'undefined' && editor && editor.getModel() ? S.pathOf.get(editor.getModel().uri.toString()) : null,
  ready: typeof S !== 'undefined' && S.ready, linted: typeof S !== 'undefined' && S.ready && !lintQueue.length && !lintDraining}))()"""


def to_back():
    """Send this process's windows behind all others, without activating them, while it starts up (as tools/bench.py)."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(h, _):
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value == os.getpid() and u.IsWindowVisible(h):
            found.append(h)
        return True

    for _ in range(400):
        found.clear()
        u.EnumWindows(each, 0)
        for h in found:
            u.SetWindowPos(h, wintypes.HWND(1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # HWND_BOTTOM; no move, size or activation
        time.sleep(0.05)


def run(label, target, tabs, behind):
    """One start, in this process (started by main): the marks are written to LABEL.jsonl."""
    t0 = time.time()
    marks = {}

    def mark(name):
        marks.setdefault(name, round((time.time() - t0) * 1000))

    sys.path.insert(0, str(ROOT))
    from slate import state
    home = OUT / label
    home.mkdir(parents = True, exist_ok = True)
    session = json.loads((Path(os.environ['APPDATA']) / 'slate' / 'state.json').read_text(encoding = 'utf-8'))
    session['autosave'] = False
    if tabs is not None:
        session['tabs'] = session['tabs'][:tabs]
    session.pop('placement', None)
    state.STATE_FILE = home / 'state.json'
    state.save(session)
    from slate import app, instance
    instance.hand_off = lambda paths: False  # never give the file to a slate that is open
    instance.serve = lambda on_open: None
    mark('imported')
    real_init, real_before = app.Api.__init__, app.Api._on_before_show
    app.Api.__init__ = lambda self: (mark('api'), real_init(self))[1]
    app.Api._on_before_show = lambda self: (mark('before_show'), real_before(self))[1]
    import webview
    if behind:
        flags, disabled = [], ['CalculateNativeWinOcclusion']  # one --disable-features only: Chromium keeps the last
        for f in app.LEAN.split():
            if f.startswith('--disable-features='):
                disabled += f.split('=', 1)[1].split(',')
            else:
                flags.append(f)
        os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = ' '.join(flags + ['--disable-backgrounding-occluded-windows', '--disable-features=' + ','.join(disabled)])
        create_window = webview.create_window
        webview.create_window = lambda *a, **kw: create_window(*a, **{**kw, 'focus': False})
        threading.Thread(target = to_back, daemon = True).start()

    def watch(window):
        window.events.shown.wait(60)
        mark('shown')
        while 'lints_ms' not in marks and time.time() - t0 < 60:
            done, box = threading.Event(), {}
            threading.Thread(target = window.evaluate_js, args = (POLL,), kwargs = {'callback': lambda r: (box.update(r = r), done.set())}, daemon = True).start()
            done.wait(5)
            if box.get('r'):
                r = json.loads(box['r'])
                marks.setdefault('page', round(r['origin'] - t0 * 1000))
                at = round(r['at'] - t0 * 1000)
                if r['active'] and os.path.normcase(r['active']) == os.path.normcase(target):
                    marks.setdefault('file_ms', at)
                if r['ready']:
                    marks.setdefault('ready_ms', at)
                if r['linted']:
                    marks.setdefault('lints_ms', at)
            time.sleep(0.02)
        time.sleep(3)
        import psutil  # private working set (Task Manager's Memory column) of slate and its WebView2 and Python children
        mb = 0
        for p in [psutil.Process()] + psutil.Process().children(recursive = True):
            try:
                mb += p.memory_full_info().uss
            except psutil.Error:
                pass
        marks['ram_mb'] = round(mb / 2 ** 20)
        with open(OUT / f'{label}.jsonl', 'a', encoding = 'utf-8') as f:
            f.write(json.dumps(marks) + '\n')
        print(json.dumps(marks), flush = True)
        window.destroy()

    start = webview.start
    webview.start = lambda *a, **kw: (threading.Thread(target = watch, args = (webview.windows[0],), daemon = True).start(), start(*a, **kw))[1]
    sys.argv = [sys.argv[0], target]
    app.main()


def main():
    args = sys.argv[1:]
    behind = '--behind' in args
    tabs = None
    if '--tabs' in args:
        tabs = int(args[args.index('--tabs') + 1])
        del args[args.index('--tabs'):args.index('--tabs') + 2]
    args = [a for a in args if a != '--behind']
    if args[0] == '--run':
        return run(args[1], args[2], tabs, behind)
    label, target = args[0], os.path.abspath(args[1])
    extra = (['--tabs', str(tabs)] if tabs is not None else []) + (['--behind'] if behind else [])
    for _ in range(int(args[2]) if len(args) > 2 else 5):
        subprocess.run([sys.executable, __file__, '--run', label, target] + extra, stderr = subprocess.DEVNULL)


if __name__ == '__main__':
    main()
