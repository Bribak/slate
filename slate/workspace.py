"""Files on disk: the tree, reading and writing, the fuzzy finder's file list and find-in-files."""
import os
import re
from pathlib import Path

TREE_HIDDEN = {'.DS_Store', '.git', '__pycache__', '.ipynb_checkpoints', '.mypy_cache', '.pytest_cache', '.ruff_cache', '.idea'}
WALK_SKIP = TREE_HIDDEN | {'node_modules', '.venv', 'venv', 'env', 'build', 'dist', '.tox', '.eggs', 'site-packages', '_proc', '_docs'}
BINARY_EXT = {'.pkl', '.pickle', '.pt', '.pth', '.ckpt', '.npy', '.npz', '.h5', '.hdf5', '.parquet', '.feather', '.zip', '.gz', '.bz2',
              '.xz', '.7z', '.tar', '.whl', '.exe', '.dll', '.pyd', '.so', '.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf', '.svgz',
              '.mzml', '.mzxml', '.raw', '.wiff', '.d', '.db', '.sqlite', '.xlsx', '.docx', '.pptx', '.ttf', '.woff', '.woff2'}
MAX_OPEN = 5_000_000
MAX_SEARCH = 2_000_000
MAX_FILES = 60_000
MAX_MATCHES = 2_000


def list_dir(path):
    entries = []
    with os.scandir(path) as it:
        for e in it:
            if e.name in TREE_HIDDEN or e.name.endswith('.egg-info'):
                continue
            try:
                is_dir = e.is_dir()
            except OSError:
                continue
            entries.append({'name': e.name, 'path': e.path, 'dir': is_dir})
    return sorted(entries, key = lambda e: (not e['dir'], e['name'].lower()))


def read_file(path):
    p = Path(path).resolve()
    size = p.stat().st_size
    if size > MAX_OPEN:
        raise ValueError(f'{p.name} is {size / 1e6:.0f} MB; slate opens files up to {MAX_OPEN // 1_000_000} MB')
    data = p.read_bytes()
    if b'\0' in data[:8192]:
        raise ValueError(f'{p.name} looks like a binary file')
    text, encoding = decode(data)
    return {'path': str(p), 'text': text, 'encoding': encoding, 'mtime': str(p.stat().st_mtime_ns)}  # as a string: nanoseconds overflow a JS number's precision


def decode(data):
    """A file's text and the encoding it was read in, which write_file saves it in again."""
    for encoding in ('utf-8-sig' if data.startswith(b'\xef\xbb\xbf') else 'utf-8', 'cp1252'):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode('latin-1'), 'latin-1'  # never fails: every byte is a character


def write_file(path, text, encoding = 'utf-8', before = None):
    """`before(path, data)` runs just before the bytes replace the file (slate/history.py keeps the old version)."""
    p = Path(path)
    try:
        data = text.encode(encoding)
    except UnicodeEncodeError as e:
        raise ValueError(f"this {encoding} file cannot hold '{text[e.start]}' (line {text.count(chr(10), 0, e.start) + 1})") from None
    if before:
        before(path, data)
    p.write_bytes(data)  # bytes, so the editor's line endings reach the disk untranslated
    return str(p.stat().st_mtime_ns)


def stat_files(paths):
    out = {}
    for path in paths:
        try:
            out[path] = str(os.stat(path).st_mtime_ns)
        except OSError:
            out[path] = None
    return out


def create(path, is_dir = False):
    p = Path(path)
    if p.exists():
        raise ValueError(f'{p.name} already exists')
    if is_dir:
        p.mkdir(parents = True)
    else:
        p.parent.mkdir(parents = True, exist_ok = True)
        p.touch()
    return str(p)


def rename(path, name):
    """Give a file or folder a new name in the same folder; returns the new path."""
    p = Path(path)
    if not name or name in ('.', '..') or any(c in name for c in '\\/'):
        raise ValueError(f"'{name}' is not a name for a file or folder")
    target = p.with_name(name)
    if target.exists() and os.path.normcase(str(target)) != os.path.normcase(str(p)):  # only the case changes: Windows sees one file
        raise ValueError(f'{name} already exists')
    p.rename(target)
    return str(target)


def walk_files(roots):
    """(root index, path relative to that root) for every file the finder and search should see."""
    count = 0
    for i, root in enumerate(roots):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in WALK_SKIP and not d.startswith('.') and not d.endswith('.egg-info'))
            rel = os.path.relpath(dirpath, root)
            for name in filenames:
                yield i, name if rel == '.' else os.path.join(rel, name)
                count += 1
                if count >= MAX_FILES:
                    return


def list_files(roots):
    return [[i, rel] for i, rel in walk_files(roots)]


def search(roots, query, regex = False, case = False):
    if not query:
        return {'matches': [], 'files': 0, 'truncated': False}
    try:
        pattern = re.compile(query if regex else re.escape(query), 0 if case else re.IGNORECASE)
    except re.error as e:
        raise ValueError(f'bad regex: {e}')
    # Most files hold no match, so each is first asked whether it holds one at all. A search of glycowork, CandyCrunch and
    # slate (429 files, 20 MB) spent 105 ms in the case-insensitive regex alone. For a plain query in an ASCII file, a byte
    # search (on lowercased bytes when case does not matter) answers that exactly, and spares the file's decoding; files with
    # other characters keep the regex, whose Unicode case rules match more (the Kelvin sign as a k). A regex is asked of the
    # whole text, where a match in a line is one too, unless anchors or lookarounds see the line breaks: those check every line.
    needle = None if regex or not query.isascii() else query.encode('ascii') if case else query.lower().encode('ascii')
    whole = not regex or not re.search(r'[\^$]|\\[AZ]|\(\?<?[=!]', query)
    matches, files = [], 0
    for i, rel in walk_files(roots):
        path = os.path.join(roots[i], rel)
        if os.path.splitext(rel)[1].lower() in BINARY_EXT:
            continue
        try:
            if os.path.getsize(path) > MAX_SEARCH:
                continue
            with open(path, 'rb') as f:
                data = f.read()
        except OSError:
            continue
        if b'\0' in data[:8192]:
            continue
        if needle is not None and data.isascii():
            if needle not in (data if case else data.lower()):
                continue
            text = data.decode('ascii')
        else:
            text = data.decode('utf-8', errors = 'replace')
            if whole and not pattern.search(text):
                continue
        hit = False
        for ln, line in enumerate(text.splitlines(), 1):
            for m in pattern.finditer(line):
                hit = True
                matches.append({'path': path, 'root': i, 'rel': rel, 'line': ln, 'start': m.start(), 'end': m.end(), 'text': line[:400]})
                if len(matches) >= MAX_MATCHES:
                    return {'matches': matches, 'files': files + 1, 'truncated': True}
                break  # one hit per line keeps the list readable; the editor highlights the rest
        files += hit
    return {'matches': matches, 'files': files, 'truncated': False}
