"""Where a Python has glycowork and CandyCrunch installed, for hints and go-to-definition into them when their checkouts are
not among the open folders (hints.py, navigate.py): most of the group installs them with pip.

Asking a Python takes one short process (about 60 ms, importlib.util.find_spec, which finds a package without importing
it), so the answer is kept per interpreter with its mtime in libraries.json, and a session asks each Python once.
"""
import json
import os
import subprocess
import threading
from pathlib import Path

from slate import state
from slate.system import NO_WINDOW

NAMES = ('glycowork', 'candycrunch')
FIND = ('import importlib.util, json, os, sys\n'
        'out = {}\n'
        'for name in sys.argv[1:]:\n'
        '    spec = importlib.util.find_spec(name)\n'
        '    if spec and spec.submodule_search_locations:\n'
        '        out[name] = os.path.dirname(list(spec.submodule_search_locations)[0])\n'
        'print(json.dumps(out))\n')
_lock = threading.Lock()
_known = None  # {python: [mtime, {name: folder holding the package}]}


def cache_file():
    return state.STATE_FILE.parent / 'libraries.json'


def installed(python, roots = ()):
    """{name: folder holding it} for NAMES installed in python, leaving out any an open folder holds itself (its checkout wins)."""
    global _known
    with _lock:
        if _known is None:
            try:
                _known = json.loads(cache_file().read_text(encoding = 'utf-8'))
            except (OSError, ValueError):
                _known = {}
        try:
            mtime = str(os.stat(python).st_mtime_ns)
        except OSError:
            return {}
        entry = _known.get(python)
        if entry is None or entry[0] != mtime:
            try:
                out = subprocess.run([python, '-E', '-c', FIND, *NAMES], capture_output = True, text = True, timeout = 20, cwd = Path(python).parent,  # not -I, which hides pip install --user
                                     creationflags = NO_WINDOW)  # and in Python's own folder: a glycowork folder where slate runs would shadow the real one
                found = json.loads(out.stdout or '{}')
            except (OSError, ValueError, subprocess.TimeoutExpired):
                found = {}
            _known[python] = entry = [mtime, found]
            try:
                cache_file().parent.mkdir(parents = True, exist_ok = True)
                cache_file().write_text(json.dumps(_known), encoding = 'utf-8')
            except OSError:
                pass
    own = {name for r in roots for name in NAMES if (Path(r) / name / '__init__.py').is_file()}
    open_roots = {os.path.normcase(os.path.abspath(r)) for r in roots}
    return {name: folder for name, folder in entry[1].items() if name not in own and os.path.normcase(os.path.abspath(folder)) not in open_roots
            and (Path(folder) / name / '__init__.py').is_file()}
