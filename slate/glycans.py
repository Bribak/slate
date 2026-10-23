"""SNFG drawings of the glycans written in the code, with glycowork's GlycoDraw (app.js, "glycan drawings").

glycowork runs in a process of its own (glycan_child.py), started on the first request: importing it takes about 2 s and
70 MB, which the editor's process should neither wait for nor keep. That process ends after IDLE seconds without a
request, and starts again when a file with glycans is shown. Drawing takes 2-7 ms per glycan.
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

CHILD = Path(__file__).with_name('glycan_child.py')
IDLE = 600


class Drawer:
    def __init__(self):
        self._proc, self._key, self._used = None, None, 0
        self._lock = threading.Lock()  # pywebview runs each call on its own thread, and requests share one pipe

    def draw(self, glycans, python = None, paths = ()):
        """{glycan: [small svg, full svg] or None}, or {'error': message} when glycowork cannot be imported."""
        with self._lock:
            key = (python, tuple(paths))
            if self._proc is None or self._proc.poll() is not None or key != self._key:
                error = self._start(python, paths)
                if error:
                    return {'error': error}
                self._key = key
            self._used = time.time()
            try:
                self._proc.stdin.write((json.dumps(glycans) + '\n').encode('utf-8'))
                self._proc.stdin.flush()
                reply = self._proc.stdout.readline()
            except OSError:
                reply = b''
            if not reply:
                self.stop()
                return {'error': 'the drawing process stopped'}
            return json.loads(reply)

    def _start(self, python, paths):
        self.stop()
        python = Path(python if python and Path(python).is_file() else sys.executable)
        if python.name.lower() == 'pythonw.exe':
            python = python.with_name('python.exe')  # real stdio pipes, as for the console
        env = dict(os.environ, PYTHONIOENCODING = 'utf-8', MPLBACKEND = 'Agg')
        self._proc = subprocess.Popen([str(python), '-u', str(CHILD), *paths], stdin = subprocess.PIPE, stdout = subprocess.PIPE,
                                      stderr = subprocess.DEVNULL, env = env, creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        first = json.loads(self._proc.stdout.readline() or '{"error": "the drawing process did not start"}')
        if 'error' in first:
            self.stop()
            return first['error']
        self._used = time.time()
        threading.Thread(target = self._stop_when_idle, args = (self._proc,), daemon = True).start()
        return None

    def _stop_when_idle(self, proc):
        while proc is self._proc and proc.poll() is None:
            time.sleep(30)
            with self._lock:
                if proc is self._proc and time.time() - self._used > IDLE:
                    self.stop()

    def stop(self):
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.kill()
