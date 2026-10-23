"""Squiggles and reformatting through the check_style.py of the file's own project, so slate, CI and pre-commit agree.

A file outside any project with a .github/scripts/check_style.py uses the fallback script (glycowork's by default).
"""
import ast
import importlib.util
import io
import threading
import tokenize
import warnings
from pathlib import Path

DEFAULT_SCRIPT = Path.home() / 'Documents' / 'glycowork' / '.github' / 'scripts' / 'check_style.py'
_modules, _signatures = {}, {}
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


def signatures(module, root):
    # GW3 needs every function signature in the project; parsing them all takes a moment, so they are cached until a .py file under root is saved
    if root is None:
        return {}
    key = (id(module), root)
    if key not in _signatures:
        packages = [str(d) for d in root.iterdir() if d.is_dir() and (d / '__init__.py').is_file()]  # not the whole root: a .venv inside it holds thousands of files
        try:
            _signatures[key] = module.build_signatures(packages)
        except (OSError, UnicodeDecodeError):
            _signatures[key] = {}
    return _signatures[key]


def saved(path):
    path = Path(path)
    for key in [k for k in _signatures if k[1] in path.parents]:
        del _signatures[key]


def lint(path, source, fallback = None):
    with _lock:
        return _lint(path, source, fallback)


def compile_problems(path, source):
    """Markers for code Python would refuse to run, with or without a style checker: syntax and indentation errors, but also
    what only the compiler catches ('return' outside a function, a duplicate argument), plus SyntaxWarnings such as '\\d'."""
    with warnings.catch_warnings(record = True) as caught:
        warnings.simplefilter('always')
        try:
            compile(source, path, 'exec', dont_inherit = True)
        except SyntaxError as e:  # IndentationError and TabError included
            return [{'line': e.lineno or 1, 'col': e.offset or 1, 'code': type(e).__name__, 'message': e.msg, 'severity': 'error'}]
        except ValueError as e:
            return [{'line': 1, 'col': 1, 'code': 'ValueError', 'message': str(e), 'severity': 'error'}]
    return [{'line': w.lineno or 1, 'col': 0, 'code': 'SyntaxWarning', 'message': str(w.message), 'severity': 'warning'}
            for w in caught if issubclass(w.category, SyntaxWarning)]


def _lint(path, source, fallback):
    markers = compile_problems(path, source)
    if any(m['severity'] == 'error' for m in markers):
        return {'markers': markers}
    root, script = find_script(path, fallback = fallback)
    if script is None:
        return {'markers': markers, 'note': 'no check_style.py found'}
    module = load(script)
    tree = ast.parse(source)
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    report = lambda ln, code, msg: markers.append({'line': ln, 'col': 0, 'code': code, 'message': msg, 'severity': 'warning'})
    module.check_blank_lines(tree, source.splitlines(), tokens, report)
    module.check_kwarg_spacing(tokens, report)
    module.check_positional_kwargs(tree, signatures(module, root), report)
    note = None
    if hasattr(module, 'pycodestyle_findings'):
        markers += [{'line': ln, 'col': col + 1, 'code': code, 'message': msg, 'severity': 'warning'} for ln, col, code, msg in module.pycodestyle_findings(source)]
    else:
        note = f'{script} predates --fix: pycodestyle squiggles and reformatting need the --fix changes from .claude/changes applied'
    return {'markers': markers, 'note': note, 'script': str(script)}


def format_source(path, source, fallback = None):
    with _lock:
        return _format(path, source, fallback)


def _format(path, source, fallback):
    root, script = find_script(path, fallback = fallback)
    if script is None:
        raise ValueError('no check_style.py found for this file')
    module = load(script)
    if not hasattr(module, 'fix_source'):
        raise ValueError(f'{script} has no fix_source yet: apply the --fix changes from .claude/changes')
    try:
        return module.fix_source(source)
    except (SyntaxError, tokenize.TokenError) as e:
        raise ValueError(f'cannot reformat a file that does not parse: {e}')
