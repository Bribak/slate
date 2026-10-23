"""Signatures and docstrings of the open projects' own functions, for the hints while typing a call and on hover.

Read with ast, without jedi: from each folder's top-level packages (as style.py reads GW3's signatures), from the lab's
libraries (libraries.NAMES) where the console's Python installed them when their checkouts are not open (libraries.py),
and from the open file's newest text that parsed. Kept per file with its mtime in hints.json, so a new session reads
only what changed. Library functions (pandas, numpy, networkx) get no hints; a name is only resolved through the file's own
definitions and imports, since a hint for the wrong function is worse than none.
"""
import ast
import json
import os
import threading
import time
from pathlib import Path

from slate import state, workspace

RESCAN = 10  # seconds between checks of a folder's file times
MAX_DOC = 1500
_lock = threading.Lock()
_files = None  # {path: [mtime, module, definitions]} for package files on disk, kept in hints.json
_open = {}  # {path key: (definitions, imports)} from the newest text of open files that parsed
_scanned = {}  # {root: time of the last check}
_console = (None, None)  # (key of the console's history, (definitions, imports)) parsed from it
_version = 0  # bumped whenever _files changes, which invalidates _ordered
_ordered = (None, [])  # ((sources, _version), the entries under them, in their order)


def cache_file():
    return state.STATE_FILE.parent / 'hints.json'  # looked up on each call, since the dev server and bench move STATE_FILE


def comment(line):
    """The # comment at the end of a source line, ignoring a # inside a string."""
    i = comment_start(line)
    return line[i + 1:].strip() if i >= 0 else ''


def comment_start(line):
    """Where the # comment of a source line starts, ignoring a # inside a string; -1 without one."""
    quote, i = None, 0
    while i < len(line):
        c = line[i]
        if quote:
            if c == '\\':
                i += 1
            elif line.startswith(quote, i):
                i += len(quote) - 1
                quote = None
        elif c in '"\'':
            quote = c * 3 if line.startswith(c * 3, i) else c
            i += len(quote) - 1
        elif c == '#':
            return i
        i += 1
    return -1


def describe(fn, lines, kind, owner = None, cls = None):
    """{name, kind, owner, line, label, params: [[start, end, doc, name]], doc, returns} for a def (or a class, through its __init__)."""
    name = cls.name if cls is not None else fn.name
    args = fn.args if fn is not None else ast.arguments(posonlyargs = [], args = [], kwonlyargs = [], kw_defaults = [], defaults = [])
    positional = args.posonlyargs + args.args
    defaults = [None] * (len(positional) - len(args.defaults)) + args.defaults
    items = [(a, d, '') for a, d in zip(positional, defaults)]
    if args.posonlyargs:
        items.insert(len(args.posonlyargs), (None, None, '/'))
    if args.vararg:
        items.append((args.vararg, None, '*'))
    elif args.kwonlyargs:
        items.append((None, None, '*'))
    items += [(a, d, '') for a, d in zip(args.kwonlyargs, args.kw_defaults)]
    if args.kwarg:
        items.append((args.kwarg, None, '**'))
    if kind != 'function' and items and items[0][0] is not None and items[0][0].arg in ('self', 'cls'):
        items = items[1:]  # called as obj.method() or Class(), so the first parameter is never passed
    ends = {}  # line -> parameters ending on it: a comment there documents the parameter when it is the only one
    for a, d, _ in items:
        if a is not None:
            end = max(n.end_lineno for n in (a, a.annotation, d) if n is not None)
            ends.setdefault(end, []).append(a.arg)
    label, params = name + '(', []
    for i, (a, d, star) in enumerate(items):
        text = star if a is None else star + a.arg + (f': {ast.unparse(a.annotation)}' if a.annotation else '') + (f' = {ast.unparse(d)}' if d is not None else '')
        if i:
            label += ', '
        if a is not None:
            end = max(n.end_lineno for n in (a, a.annotation, d) if n is not None)
            doc = comment(lines[end - 1]) if end != fn.lineno and len(ends[end]) == 1 else ''
            params.append([len(label), len(label) + len(text), doc, a.arg])
        label += text
    label += ')'
    returns = ''
    if fn is not None and fn.returns is not None:
        label += f' -> {ast.unparse(fn.returns)}'
        if fn.returns.end_lineno != fn.lineno and fn.returns.end_lineno not in ends:
            returns = comment(lines[fn.returns.end_lineno - 1])
    doc = (ast.get_docstring(cls) if cls is not None else None) or (ast.get_docstring(fn) if fn is not None else None) or ''
    return {'name': name, 'kind': kind, 'owner': owner, 'line': (cls or fn).lineno, 'label': label, 'params': params,
            'doc': doc[:MAX_DOC], 'returns': returns}


def statements(body):
    """Every statement inside compound statements, without the expressions ast.walk would also visit (30,000 nodes in a big file)."""
    for node in body:
        for field in ('body', 'orelse', 'finalbody', 'handlers'):
            inner = getattr(node, field, None) or []
            yield from inner
            yield from statements(inner)
        for case in getattr(node, 'cases', None) or []:
            yield from case.body
            yield from statements(case.body)


def definitions(tree, lines):
    """({name: [description]}, {local name: [module, attribute or None]}) for a module: its functions, classes and their
    methods, also under `if`/`try` (try: import torch ... except ImportError), and what it imports under which name,
    including the imports inside functions that glycowork uses against circular imports (module-level ones win)."""
    defs, imports = {}, {}

    def visit(body, cls):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs.setdefault(node.name, []).append(describe(node, lines, 'method' if cls else 'function', owner = cls))
            elif isinstance(node, ast.ClassDef) and cls is None:
                init = next((n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == '__init__'), None)
                defs.setdefault(node.name, []).append(describe(init, lines, 'class', cls = node))
                visit(node.body, node.name)
            elif isinstance(node, ast.If):
                visit(node.body + node.orelse, cls)
            elif isinstance(node, ast.Try):
                visit(node.body + [n for h in node.handlers for n in h.body] + node.orelse + node.finalbody, cls)
    visit(tree.body, None)
    for node in [*tree.body, *statements(tree.body)]:  # module-level imports first, so they win over a function's
        if isinstance(node, ast.Import):
            for a in node.names:
                imports.setdefault(a.asname or a.name.split('.')[0], [a.name if a.asname else a.name.split('.')[0], None])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                imports.setdefault(a.asname or a.name, ['.' * node.level + (node.module or ''), a.name])
    return defs, imports


def module_of(path, root):
    rel = Path(path).relative_to(root).with_suffix('')
    parts = rel.parts[:-1] if rel.name == '__init__' else rel.parts
    return '.'.join(parts)


def _load():
    global _files
    if _files is None:
        try:
            _files = json.loads(cache_file().read_text(encoding = 'utf-8'))
        except (OSError, ValueError):
            _files = {}


def _save():
    try:
        tmp = cache_file().with_suffix('.tmp')
        tmp.write_text(json.dumps(_files), encoding = 'utf-8')
        tmp.replace(cache_file())
    except OSError:
        pass  # only the next session's first hint gets slower


def _scan(roots, libraries = None):
    """Read every package file of the folders, and of the installed libraries ({name: folder}), that changed since it was
    last read (at most every RESCAN seconds)."""
    global _version
    _load()
    changed = False
    for root, only in [(r, None) for r in roots] + [(folder, name) for name, folder in (libraries or {}).items()]:
        key = (root, only)
        if time.time() - _scanned.get(key, 0) < RESCAN:
            continue
        _scanned[key] = time.time()
        try:
            folders = [Path(root) / only] if only else [d for d in Path(root).iterdir() if d.is_dir()]  # a library by its name: listing site-packages (949 entries) took 230-290 ms per library and rescan
            packages = [d for d in folders if (d / '__init__.py').is_file()]  # as style.signatures: not a .venv's thousands of files
        except OSError:
            continue
        for f in (f for p in packages for f in p.rglob('*.py') if not workspace.WALK_SKIP & set(f.relative_to(root).parts)):  # below root: an installed library sits in site-packages
            try:
                mtime = str(f.stat().st_mtime_ns)
            except OSError:
                continue
            key = str(f)
            if key in _files and _files[key][0] == mtime:
                continue
            try:
                source = f.read_text(encoding = 'utf-8')
                found = definitions(ast.parse(source), source.splitlines())[0]
            except (OSError, ValueError, SyntaxError):
                found = {}
            _files[key] = [mtime, module_of(f, root), found]
            changed = True
    if changed:
        _version += 1
        _save()


def remember(path, tree, source):
    """The open file's definitions and imports from text that just parsed (called by style's lint); a call being typed does not parse."""
    found = definitions(tree, source.splitlines())
    with _lock:
        _open[_key(path)] = found


def _key(path):
    return os.path.normcase(os.path.abspath(path))


def _within(sources):
    """The scanned files under the open folders and installed libraries in use, those of the open folders first: a
    library's copy stays in hints.json after its checkout is opened, and must not win over it."""
    global _ordered
    key = (tuple(sources), _version)
    if _ordered[0] != key:  # kept until the folders or the files change: rebuilding it took a few ms per keystroke in a call
        prefixes = [os.path.join(os.path.normcase(os.path.abspath(s)), '') for s in sources]
        rank = lambda path: next((i for i, p in enumerate(prefixes) if os.path.normcase(path).startswith(p)), None)
        ranked = [(r, n, entry) for n, (path, entry) in enumerate(_files.items()) if (r := rank(path)) is not None]
        _ordered = (key, [entry for _, _, entry in sorted(ranked, key = lambda x: x[:2])])
    return _ordered[1]


def _module(name, sources):
    """Definitions of a dotted module among the scanned package files ({} when it is not one of the open projects')."""
    return next((found for _, module, found in _within(sources) if module == name), {})


def _absolute(module, path):
    """The dotted name of a relative import ('.processing' in glycowork/motif/draw.py is glycowork.motif.processing)."""
    if not module.startswith('.'):
        return module
    level = len(module) - len(module.lstrip('.'))
    here = (_files.get(str(Path(path)), [None, None])[1] or '') if path else ''
    package = here.split('.') if Path(path).name == '__init__.py' else here.split('.')[:-1]
    base = package[:len(package) - level + 1] if level > 1 else package
    return '.'.join(base + ([module.lstrip('.')] if module.lstrip('.') else []))


def _console_context(entries):
    """Definitions and imports from the console's history, each entry parsed on its own; cached until the history changes."""
    global _console
    key = (len(entries), entries[-1] if entries else None)
    if _console[0] != key:
        defs, imports = {}, {}
        for code in entries:
            try:
                d, i = definitions(ast.parse(code), code.splitlines())
            except (SyntaxError, ValueError):
                continue
            for name, found in d.items():
                defs[name] = found
            imports.update(i)
        _console = (key, (defs, imports))
    return _console[1]


def lookup(path, name, qualifier = None, roots = (), console = None, libraries = None):
    """Descriptions of what `qualifier.name` is at a call in path (or in the console, given its history), best first.
    libraries: {name: folder} of the lab's libraries where the console's Python installed them (libraries.py)."""
    with _lock:
        _scan(list(roots), libraries = libraries)
        sources = list(roots) + list((libraries or {}).values())
        if console is not None:
            defs, imports = _console_context(console)
        elif _key(path) in _open:
            defs, imports = _open[_key(path)]
        else:
            entry = _files.get(str(Path(path)))
            defs, imports = (entry[2], {}) if entry else ({}, {})
        if qualifier in ('self', 'cls'):
            return [d for d in defs.get(name, []) if d['kind'] == 'method']
        if qualifier:
            head, _, rest = qualifier.partition('.')
            if head not in imports:
                return []  # a method of some object, which cannot be resolved by name alone
            module, attr = imports[head]
            module = _absolute(module, path) + (f'.{attr}' if attr else '') + (f'.{rest}' if rest else '')
            return [d for d in _module(module, sources).get(name, []) if d['kind'] != 'method']
        local = [d for d in defs.get(name, []) if d['kind'] != 'method']
        if local or name not in imports:
            return local[:5]
        module, attr = imports[name]
        module = _absolute(module, path)
        found = [d for d in _module(module, sources).get(attr or name, []) if d['kind'] != 'method']
        if not found and attr:  # re-exported by a package's __init__: the same name anywhere in that top-level package
            top = module.split('.')[0]
            found = [d for _, m, f in _within(sources) if m.split('.')[0] == top for d in f.get(attr, []) if d['kind'] != 'method']
        return found[:5]
