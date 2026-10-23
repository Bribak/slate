"""The persistent Python console: one child interpreter whose variables survive between runs, until restarted."""
import codecs
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from slate import usage

CHILD = Path(__file__).with_name('repl_child.py')
FLUSH_EVERY = 0.05
MAX_CHUNK = 200_000


class Console:
    def __init__(self, emit):
        self._emit = emit  # emit(list of [kind, payload]); called from a background thread, at most every FLUSH_EVERY seconds
        self._proc, self._port = None, None
        self._pending, self._lock = [], threading.Lock()
        self._next_id = 0
        threading.Thread(target = self._flusher, daemon = True).start()

    def _push(self, kind, payload):
        with self._lock:
            if self._pending and self._pending[-1][0] == kind and kind in ('o', 'e'):
                self._pending[-1][1] += payload
            else:
                self._pending.append([kind, payload])

    def _flusher(self):
        while True:
            time.sleep(FLUSH_EVERY)
            with self._lock:
                batch, self._pending = self._pending, []
            for item in batch:
                if item[0] in ('o', 'e') and len(item[1]) > MAX_CHUNK:  # a runaway print loop must not freeze the window
                    item[1] = f'... ({len(item[1]) - MAX_CHUNK} characters skipped)\n' + item[1][-MAX_CHUNK:]
            if batch:
                self._emit(batch)

    def start(self, cwd = None, paths = (), python = None):
        self.stop()
        env = dict(os.environ, PYTHONIOENCODING = 'utf-8', PYTHONUNBUFFERED = '1')
        if python and not Path(python).is_file():
            self._push('e', f'console_python {python} not found; using the Python slate runs on\n')
            python = None
        python = Path(python or sys.executable)  # repl_child.py is stdlib-only, so any Python 3.8+ (a venv, a conda env) can run it
        if python.name.lower() == 'pythonw.exe':
            python = python.with_name('python.exe')  # the child needs real stdio pipes
        self._proc = subprocess.Popen([str(python), '-u', str(CHILD), *paths], stdin = subprocess.PIPE, stdout = subprocess.PIPE,
                                      stderr = subprocess.PIPE, cwd = cwd if cwd and os.path.isdir(cwd) else None, env = env,
                                      creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        proc = self._proc
        threading.Thread(target = self._read_out, args = (proc,), daemon = True).start()
        threading.Thread(target = self._read_err, args = (proc,), daemon = True).start()

    def _read_out(self, proc):
        for raw in proc.stdout:
            line = raw.decode('utf-8', errors = 'replace')
            try:
                msg = json.loads(line)
            except ValueError:
                msg = None
            if not isinstance(msg, dict):
                self._push('o', line)  # written straight to the file descriptor, e.g. by a subprocess
            elif 'o' in msg or 'e' in msg:
                key = 'o' if 'o' in msg else 'e'
                self._push(key, msg[key])
            elif 'port' in msg:
                if proc is self._proc:
                    self._port = msg['port']
            else:
                for key, value in msg.items():
                    self._push(key, value)
        code = proc.wait()
        if proc is self._proc:  # not stopped by slate: the code exited, or the interpreter crashed
            self._proc = None
            self._push('stopped', code)
            if code:  # 0 is exit() or the end of input
                usage.record('console_exit', code = code)

    def _read_err(self, proc):
        decoder = codecs.getincrementaldecoder('utf-8')(errors = 'replace')
        while chunk := proc.stderr.read1(65536):
            self._push('e', decoder.decode(chunk))

    def run(self, code, cwd = None, paths = (), python = None):
        if self._proc is None:
            self.start(cwd, paths, python)
        self._next_id += 1
        self._send({'code': code, 'id': self._next_id})
        return self._next_id

    def interrupt(self):
        if self._proc is None or self._port is None:
            return
        try:
            with socket.create_connection(('127.0.0.1', self._port), timeout = 2) as conn:
                conn.sendall(b'i')
        except OSError:
            pass

    def _send(self, msg):
        try:
            self._proc.stdin.write((json.dumps(msg) + '\n').encode('ascii'))
            self._proc.stdin.flush()
        except (OSError, AttributeError):
            self._push('stopped', None)

    def stop(self):
        proc, self._proc, self._port = self._proc, None, None
        if proc is not None and proc.poll() is None:
            proc.kill()
