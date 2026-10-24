"""Where a Python has the lab's libraries (NAMES: glycowork, CandyCrunch, glycorender, glycontact) installed, for hints and
go-to-definition into them when their checkouts are not among the open folders (hints.py, navigate.py): most of the group
installs them with pip.

Asking a Python takes one short process (about 60 ms, importlib.util.find_spec, which finds a package without importing
it), so the answer is kept per interpreter in libraries.json with its mtime, the names asked and the mtimes of its package
folders (site-packages and the user site). pip adds or removes a folder there on every install or uninstall, so a changed
folder mtime asks again: a library installed while slate runs is found within RECHECK seconds.
"""
import json
import os
import subprocess
import threading
import time
from pathlib import Path

from slate import state
from slate.system import NO_WINDOW

NAMES = ('glycowork', 'candycrunch', 'glycorender', 'glycontact')
FIND = ('import importlib.util, json, os, sys\n'
        'out = {}\n'
        'for name in sys.argv[1:]:\n'
        '    spec = importlib.util.find_spec(name)\n'
        '    if spec and spec.submodule_search_locations:\n'
        '        out[name] = os.path.dirname(list(spec.submodule_search_locations)[0])\n'
        'import site, sysconfig\n'
        'dirs = {sysconfig.get_path("purelib"), sysconfig.get_path("platlib")}\n'
        'if site.ENABLE_USER_SITE:\n'
        '    dirs.add(site.getusersitepackages())\n'
        'print(json.dumps({"found": out, "dirs": sorted(dirs)}))\n')
RECHECK = 10  # seconds between looks at a Python's package folders, as hints.py rescans its files
_lock = threading.Lock()
_known = None  # {python: [mtime, {name: folder holding the package}, names asked, {package folder: mtime or None}]}
_looked = {}  # {python: when its package folders were last compared}


def stamps(dirs):
    """{folder: mtime_ns, or None while it does not exist}: a user site appears with the first pip install --user."""
    out = {}
    for d in dirs:
        try:
            out[d] = os.stat(d).st_mtime_ns
        except OSError:
            out[d] = None
    return out


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
        stale = entry is None or entry[0] != mtime or entry[2:3] != [list(NAMES)] or len(entry) < 4  # older entries lack newer names, or the folders
        if not stale and time.time() - _looked.get(python, 0) >= RECHECK:
            _looked[python] = time.time()
            stale = stamps(entry[3]) != entry[3]  # pip installed or removed something since
        if stale:
            try:
                out = subprocess.run([python, '-E', '-c', FIND, *NAMES], capture_output = True, text = True, timeout = 20, cwd = Path(python).parent,  # not -I, which hides pip install --user
                                     creationflags = NO_WINDOW)  # and in Python's own folder: a glycowork folder where slate runs would shadow the real one
                reply = json.loads(out.stdout or '{}')
            except (OSError, ValueError, subprocess.TimeoutExpired):
                reply = {}
            _known[python] = entry = [mtime, reply.get('found', {}), list(NAMES), stamps(reply.get('dirs', []))]
            _looked[python] = time.time()
            try:
                cache_file().parent.mkdir(parents = True, exist_ok = True)
                cache_file().write_text(json.dumps(_known), encoding = 'utf-8')
            except OSError:
                pass
    own = {name for r in roots for name in NAMES if (Path(r) / name / '__init__.py').is_file()}
    open_roots = {os.path.normcase(os.path.abspath(r)) for r in roots}
    return {name: folder for name, folder in entry[1].items() if name not in own and os.path.normcase(os.path.abspath(folder)) not in open_roots
            and (Path(folder) / name / '__init__.py').is_file()}
