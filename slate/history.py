"""Local history, as in PyCharm: before slate saves over a file, the version on disk is kept in data_dir()/history, so what a
save replaced can be compared and restored from the tab's menu ("Local history…").

The version on disk is either slate's own last save, kept at most once per GAP seconds (autosave saves on every blur), or
one written by something else (git, Claude, another editor), which is always kept: slate remembers a hash of what it last
wrote to each file. Per file the newest KEPT versions stay, none older than MAX_AGE, and files over MAX_SIZE are not kept.
Nothing runs until a save, which costs a read of the old file (in the OS cache, since slate read it) and, at most once a
minute, a write.
"""
import hashlib
import os
import threading
import time

from slate import workspace
from slate.system import data_dir

ROOT = data_dir() / 'history'
KEPT = 50
GAP = 60
MAX_SIZE = 2_000_000
MAX_AGE = 30 * 86400
_written = {}  # folder: (mtime_ns, size) of slate's last save there
_newest = {}  # folder: hash of its newest kept version
_lock = threading.Lock()
_swept = False


def folder(path):
    return ROOT / hashlib.sha1(os.path.normcase(os.path.abspath(path)).encode('utf-8')).hexdigest()[:16]


def _versions(where):
    try:
        return sorted((e for e in os.scandir(where) if e.name.isdigit()), key = lambda e: int(e.name), reverse = True)
    except OSError:
        return []


def _sweep(now):
    """Once a session: drop the history of files not saved for MAX_AGE."""
    for entry in os.scandir(ROOT):
        if entry.is_dir() and now - entry.stat().st_mtime > MAX_AGE:
            for version in os.scandir(entry.path):
                os.remove(version.path)
            os.rmdir(entry.path)


def keep(path, data):
    """Called just before slate writes `data` (bytes) to `path`. Never raises: a history that cannot be written must not stop a save."""
    try:
        _keep(path, data)
    except OSError:
        pass


def _keep(path, data):
    global _swept
    try:
        st = os.stat(path)
    except OSError:
        return  # a new file: nothing to keep
    if st.st_size > MAX_SIZE:
        return
    where = folder(path)
    with _lock:
        mine = _written.get(where) == (st.st_mtime_ns, st.st_size)  # by stat: the first read of a file just written costs ~18 ms (the virus scanner), a stat 0.03
        versions = _versions(where)
        now = time.time()
        if mine and versions and now - int(versions[0].name) / 1e9 < GAP:
            return
        with open(path, 'rb') as f:
            old = f.read()
        digest = hashlib.sha1(old).digest()
        if old == data:
            return
        if versions:
            if where not in _newest:  # the first save here this session
                with open(versions[0].path, 'rb') as f:
                    _newest[where] = hashlib.sha1(f.read()).digest()
            if _newest[where] == digest:
                return  # kept already
        where.mkdir(parents = True, exist_ok = True)
        (where / 'path.txt').write_text(os.path.abspath(path), encoding = 'utf-8')
        (where / str(time.time_ns())).write_bytes(old)
        _newest[where] = digest
        for version in versions[KEPT - 1:] + [v for v in versions[:KEPT - 1] if now - int(v.name) / 1e9 > MAX_AGE]:
            os.remove(version.path)
        if not _swept:
            _swept = True
            _sweep(now)


def wrote(path):
    """Called after slate wrote `path`: what its stat says then marks the file as slate's own until something else writes it."""
    try:
        st = os.stat(path)
    except OSError:
        return
    with _lock:
        _written[folder(path)] = (st.st_mtime_ns, st.st_size)


def versions(path):
    """The kept versions of a file, newest first: {id, time (ms since the epoch), size}."""
    return [{'id': v.name, 'time': int(v.name) // 1_000_000, 'size': v.stat().st_size} for v in _versions(folder(path))]


def read(path, version):
    if not version.isdigit():
        raise ValueError(f'no version {version}')
    return {'text': workspace.decode((folder(path) / version).read_bytes())[0]}
