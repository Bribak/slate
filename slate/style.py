"""Squiggles and reformatting through the check_style.py of the file's own project, so slate, CI and pre-commit agree.

A file outside any project with a .github/scripts/check_style.py uses the fallback script (glycowork's by default).
"""
import ast
import importlib.util
import inspect
import io
import json
import threading
import tokenize
import warnings
from pathlib import Path

from slate import glycans, heap, hints, state

DEFAULT_SCRIPT = Path.home() / 'Documents' / 'glycowork' / '.github' / 'scripts' / 'check_style.py'
_modules, _signatures = {}, {}
_file_sigs = None  # {script: {'mtime', 'files': {path: [mtime, signatures]}}}, kept in signatures.json between sessions
_lock = threading.Lock()  # lint calls arrive on parallel threads while you type


def find_script(path, fallback = None):
    """(project root or None, check_style.py path or None) for a file."""
    for d in Path(path).parents:
        script = d / '.github' / 'scripts' / 'check_style.py'
        if script.is_file():
            return d, script
    fallback = Path(fallback) if fallback else DEFAULT_SCRIPT
    return None, fallback if fallback.is_file() else None


def load(script):
    mtime = script.stat().st_mtime_ns
    cached = _modules.get(script)
    if cached and cached[0] == mtime:
        return cached[1]
    spec = importlib.util.spec_from_file_location(f'slate_check_style_{len(_modules)}', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _modules[script] = (mtime, module)
    return module


def cache_file():
    return state.STATE_FILE.parent / 'signatures.json'  # looked up on each call, since the dev server and bench move STATE_FILE


def signatures(module, script, root):
    """GW3's {function name: positional parameters} for the whole project, kept until a .py file under root is saved."""
    if root is None:
        return {}
    key = (id(module), root)
    if key not in _signatures:
        packages = [str(d) for d in root.iterdir() if d.is_dir() and (d / '__init__.py').is_file()]  # not the whole root: a .venv inside it holds thousands of files
        _signatures[key] = project_signatures(module, script, packages)
    return _signatures[key]


def project_signatures(module, script, packages):
    """Parsed per file and remembered with each file's mtime, also across sessions, so only changed files are parsed again:
    all of glycowork took 660 ms, a stat of its files 6. Merging per-file results gives what build_signatures gives for the
    whole project, since a name with a *args anywhere stays None and otherwise takes the maximum."""
    global _file_sigs
    if not hasattr(module, 'py_files'):
        try:
            return module.build_signatures(packages)
        except (OSError, UnicodeDecodeError):
            return {}
    if _file_sigs is None:
        try:
            _file_sigs = json.loads(cache_file().read_text(encoding = 'utf-8'))
        except (OSError, ValueError):
            _file_sigs = {}
    entry = _file_sigs.get(str(script))
    if not entry or entry['mtime'] != script.stat().st_mtime_ns:  # a changed checker may count differently
        entry = _file_sigs[str(script)] = {'mtime': script.stat().st_mtime_ns, 'files': {}}
    old, files, merged = entry['files'], {}, {}
    for f in module.py_files(packages):
        try:
            mtime = f.stat().st_mtime_ns
        except OSError:
            continue
        cached = old.get(str(f))
        if not cached or cached[0] != mtime:
            try:
                cached = [mtime, module.build_signatures([f])]
            except (OSError, UnicodeDecodeError):
                cached = [mtime, {}]
        files[str(f)] = cached
        for name, required in cached[1].items():
            merged[name] = required if name not in merged else None if None in (required, merged[name]) else max(required, merged[name])
    if files != old:  # deleted files drop out too
        entry['files'] = files
        try:
            tmp = cache_file().with_suffix('.tmp')
            tmp.write_text(json.dumps(_file_sigs), encoding = 'utf-8')
            tmp.replace(cache_file())
        except OSError:
            pass  # only the next session's first lint gets slower
    return merged


def saved(path):
    path = Path(path)
    for key in [k for k in _signatures if k[1] in path.parents]:
        del _signatures[key]


def lint(path, source, fallback = None, glycan_check = True):
    with _lock, heap.paused():  # 30% of a big file's lint was the garbage collector walking the new tree again and again (heap.py)
        return _lint(path, source, fallback, glycan_check = glycan_check)


def compile_problems(path, source):
    """(markers, tree) for code Python would refuse to run, with or without a style checker: syntax and indentation errors,
    but also what only the compiler catches ('return' outside a function, a duplicate argument), plus SyntaxWarnings such as
    '\\d'. Compiling the parsed tree instead of the source spares a second parse (77 ms instead of 112 for a 2,350-line file),
    with the same messages; the tree is None after an error."""
    with warnings.catch_warnings(record = True) as caught:
        warnings.simplefilter('always')
        try:
            tree = ast.parse(source, path)
            compile(tree, path, 'exec', dont_inherit = True)
        except SyntaxError as e:  # IndentationError and TabError included
            return [{'line': e.lineno or 1, 'col': e.offset or 1, 'code': type(e).__name__, 'message': e.msg, 'severity': 'error'}], None
        except ValueError as e:
            return [{'line': 1, 'col': 1, 'code': 'ValueError', 'message': str(e), 'severity': 'error'}], None
    return [{'line': w.lineno or 1, 'col': 0, 'code': 'SyntaxWarning', 'message': str(w.message), 'severity': 'warning'}
            for w in caught if issubclass(w.category, SyntaxWarning)], tree


def _lint(path, source, fallback, glycan_check = True):
    markers, tree = compile_problems(path, source)
    if tree is None:
        return {'markers': markers}
    hints.remember(path, tree, source)  # the tree is at hand, and a call being typed later will not parse
    if glycan_check:
        glycans.remember(path, source, tree)  # for the glycan check the page asks for right after
    root, script = find_script(path, fallback = fallback)
    if script is None:
        return {'markers': markers, 'note': 'no check_style.py found'}
    module = load(script)
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    report = lambda ln, code, msg: markers.append({'line': ln, 'col': 0, 'code': code, 'message': msg, 'severity': 'warning'})
    module.check_blank_lines(tree, source.splitlines(), tokens, report)
    module.check_kwarg_spacing(tokens, report)
    module.check_positional_kwargs(tree, signatures(module, script, root), report)
    note = None
    if hasattr(module, 'pycodestyle_findings'):
        findings = module.pycodestyle_findings
        found = findings(source, tokens) if 'tokens' in inspect.signature(findings).parameters else findings(source)  # the tokens above, not made again: 21% of pycodestyle's time
        markers += [{'line': ln, 'col': col + 1, 'code': code, 'message': msg, 'severity': 'warning'} for ln, col, code, msg in found]
    else:
        note = f'{script} predates --fix: update it for pycodestyle squiggles and reformatting'
    return {'markers': markers, 'note': note, 'script': str(script)}


def format_source(path, source, fallback = None):
    with _lock, heap.paused():
        return _format(path, source, fallback)


def _format(path, source, fallback):
    root, script = find_script(path, fallback = fallback)
    if script is None:
        raise ValueError('no check_style.py found for this file')
    module = load(script)
    if not hasattr(module, 'fix_source'):
        raise ValueError(f'{script} predates --fix: update it to reformat')
    try:
        return module.fix_source(source)
    except (SyntaxError, tokenize.TokenError) as e:
        raise ValueError(f'cannot reformat a file that does not parse: {e}')
