"""Time a start that opens a file, as a double-click in Explorer does, in an isolated copy of the real session.

python tools/startup.py LABEL FILE [RUNS]

Copies %APPDATA%\\slate\\state.json (tabs, folders; autosave off) to %TEMP%\\slate-startup\\LABEL and starts slate RUNS times
(default 5) with FILE, each in a fresh process. Prints one JSON line per run, in ms from that process's start: slate's imports
done, Api created, window shown, the page's navigation start, and the moment FILE is the front tab (file_ms). The first run
of a new LABEL also creates its WebView2 profile, so compare the later ones. A slate that is already open is left alone.
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
  active: typeof editor !== 'undefined' && editor && editor.getModel() ? S.pathOf.get(editor.getModel().uri.toString()) : null}))()"""


def run(label, target):
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

    def watch(window):
        window.events.shown.wait(60)
        mark('shown')
        while 'file_ms' not in marks and time.time() - t0 < 60:
            done, box = threading.Event(), {}
            threading.Thread(target = window.evaluate_js, args = (POLL,), kwargs = {'callback': lambda r: (box.update(r = r), done.set())}, daemon = True).start()
            done.wait(5)
            if box.get('r'):
                r = json.loads(box['r'])
                marks.setdefault('page', round(r['origin'] - t0 * 1000))
                if r['active'] and os.path.normcase(r['active']) == os.path.normcase(target):
                    marks['file_ms'] = round(r['at'] - t0 * 1000)
            time.sleep(0.02)
        with open(OUT / f'{label}.jsonl', 'a', encoding = 'utf-8') as f:
            f.write(json.dumps(marks) + '\n')
        print(json.dumps(marks), flush = True)
        window.destroy()

    start = webview.start
    webview.start = lambda *a, **kw: (threading.Thread(target = watch, args = (webview.windows[0],), daemon = True).start(), start(*a, **kw))[1]
    sys.argv = [sys.argv[0], target]
    app.main()


def main():
    if sys.argv[1] == '--run':
        return run(sys.argv[2], sys.argv[3])
    label, target = sys.argv[1], os.path.abspath(sys.argv[2])
    for _ in range(int(sys.argv[3]) if len(sys.argv) > 3 else 5):
        subprocess.run([sys.executable, __file__, '--run', label, target], stderr = subprocess.DEVNULL)


if __name__ == '__main__':
    main()
