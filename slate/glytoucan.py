"""GlyTouCan IDs (G00031MO) as glycans, so the editor draws them like glycan strings (app.js, glycan drawings).

glycowork's glytoucan_to_glycan knows about 190,000 IDs, from df_glycan (which needs pandas) and glytoucan_ids.json.xz. As a
dict they take about 40 MB, which neither slate's process nor the drawing process should hold for a hover. So a process of
its own (this file run as a script, stdlib only) writes them once per glycowork into a sorted file next to state.json, and
a lookup bisects that file: well under a millisecond per ID, and nothing kept in memory. The build reads both of
glycowork's files the way glytoucan_to_glycan combines them, without importing glycowork, and takes 1-2 s.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

ID = re.compile(r'G\d{5}[A-Z]{2}')
KEPT = 2  # index files kept, for switching between a glycowork checkout and an installed one
_lock = threading.Lock()  # one build at a time; pywebview runs each call on its own thread
_failed = set()  # signatures whose build failed, so a broken glycowork is not rebuilt on every call


def glycowork_folder(python, roots):
    """The folder holding the glycowork package the drawing process imports: an open checkout first, as on its sys.path."""
    for r in roots:
        if (Path(r) / 'glycowork' / '__init__.py').is_file():
            return Path(r)
    from slate import libraries
    from slate.system import console_python
    python = str(console_python(python if python and Path(python).is_file() else sys.executable))  # as glycans.Drawer starts it
    folder = libraries.installed(python, roots).get('glycowork')
    return Path(folder) if folder else None


def sources(folder):
    """[df_glycan's file, the IDs beyond it or None], or None without df_glycan. The IDs beyond it are glytoucan_ids.json.xz
    since glycowork 1.11.0, before that backup_gids.json, which only aliases IDs to df_glycan's."""
    package = folder / 'glycowork'
    sugarbase = sorted(package.glob('glycan_data/v*_sugarbase.json*'))
    extra = [p for p in (package / 'motif' / 'glytoucan_ids.json.xz', package / 'motif' / 'backup_gids.json') if p.is_file()]
    return [sugarbase[-1], extra[0] if extra else None] if sugarbase else None


def read_json(path):
    import lzma  # only the index build, in its own process, reads glycowork's .xz files
    with (lzma.open if str(path).endswith('.xz') else open)(path, 'rt', encoding = 'utf-8') as f:
        return json.load(f)


def build(sugarbase, extra, out):
    """Writes 'ID<tab>glycan' lines sorted by ID, combined as glycowork's glytoucan_to_glycan combines its two sources."""
    data = read_json(sugarbase)
    cell = lambda c: c.get('value') if isinstance(c, dict) else c  # glycowork's DataFrameSerializer cells
    g, t = data['columns'].index('glycan'), data['columns'].index('glytoucan_id')
    glycan_dict = {cell(r[t]): cell(r[g]) for r in data['data'] if isinstance(cell(r[t]), str) and isinstance(cell(r[g]), str)}
    del data
    extra = read_json(extra) if extra and extra != '-' else {}
    glycan_dict |= {k: v for k, v in extra.items() if v not in glycan_dict and v not in extra}
    glycan_dict |= {k: glycan_dict.get(v, v) for k, v in extra.items() if v in glycan_dict or v in extra}
    tmp = Path(str(out) + '.tmp')
    with open(tmp, 'w', encoding = 'utf-8', newline = '\n') as f:
        for k in sorted(glycan_dict):
            v = glycan_dict[k]
            if ID.fullmatch(k) and not ID.fullmatch(v) and '\t' not in v and '\n' not in v:  # an alias left unresolved names no glycan
                f.write(f'{k}\t{v}\n')
    os.replace(tmp, out)


def index(python, roots):
    """The index file for the glycowork the drawing process would use, built first if needed; None without one."""
    from slate import state
    from slate.system import NO_WINDOW, console_python
    folder = glycowork_folder(python, roots)
    found = folder and sources(folder)
    if not found:
        return None
    signature = json.dumps([[str(p), p.stat().st_mtime_ns, p.stat().st_size] for p in found if p])
    home = state.STATE_FILE.parent
    out = home / f'glytoucan-{hashlib.sha1(signature.encode()).hexdigest()[:12]}.tsv'
    if out.is_file():
        return out
    with _lock:
        if out.is_file() or signature in _failed:
            return out if out.is_file() else None
        home.mkdir(parents = True, exist_ok = True)
        own = str(console_python(sys.executable))
        try:  # -I: neither slate's folder (this script's) nor the user's site on sys.path; the script needs only the stdlib
            subprocess.run([own, '-I', __file__, str(found[0]), str(found[1] or '-'), str(out)], capture_output = True, timeout = 120, creationflags = NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if not out.is_file():
            _failed.add(signature)
            return None
        for old in sorted(home.glob('glytoucan-*.tsv'), key = lambda p: p.stat().st_mtime, reverse = True)[KEPT:]:
            try:
                old.unlink()
            except OSError:
                pass
    return out


def find(f, size, key):
    """The glycan for key in the open index file f, by bisecting its byte offsets: every line starts with an 8-byte ID."""
    def line_at(pos):  # the first whole line starting at or after pos
        f.seek(pos)
        if pos:
            f.readline()
        return f.readline()
    k = key.encode('ascii')
    lo, hi = 0, size
    while lo < hi:
        mid = (lo + hi) // 2
        line = line_at(mid)
        if line and line[:8] < k:
            lo = mid + 1
        else:
            hi = mid
    line = line_at(lo)
    return line[9:].rstrip(b'\n').decode('utf-8') if line[:8] == k else None


def lookup(ids, python = None, roots = ()):
    """{id: IUPAC-condensed glycan, or None for an ID glycowork does not know}."""
    ids = [i for i in dict.fromkeys(ids) if ID.fullmatch(i)]
    path = ids and index(python, roots)
    if not path:
        return {i: None for i in ids}
    with open(path, 'rb') as f:
        size = os.fstat(f.fileno()).st_size
        return {i: find(f, size, i) for i in ids}


if __name__ == '__main__':
    build(*sys.argv[1:4])
