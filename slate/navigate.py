"""Go-to-definition through jedi, which looks names up on demand instead of indexing everything up front."""
import threading
from pathlib import Path

import jedi

_projects = {}
_lock = threading.Lock()  # pywebview runs each call on its own thread, and jedi is not thread-safe


def project_for(path, roots):
    root = next((r for r in roots if Path(r) in Path(path).parents), str(Path(path).parent))
    if root not in _projects:
        _projects[root] = jedi.Project(root, added_sys_path = [r for r in roots if r != root])  # modules from the other open folders resolve even when not pip-installed
    return _projects[root]


def goto(path, source, line, col, roots):
    with _lock:
        script = jedi.Script(code = source, path = path, project = project_for(path, roots))
        names = script.goto(line, col, follow_imports = True) or script.infer(line, col)
    out = []
    for n in names:
        if n.module_path is None or n.line is None:
            continue  # builtins compiled into the interpreter have no source to open
        out.append({'path': str(n.module_path), 'line': n.line, 'col': n.column + 1, 'name': n.name})
    return out
