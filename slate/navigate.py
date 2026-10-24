"""Go-to-definition: the open folders' own names from the syntax tree, everything else through jedi.

jedi infers types even to find a local variable (it checks whether the `if`s around each assignment can run), which
took up to 8 s in glycowork's draw.py, and Ctrl+hover asks for every word the mouse crosses. So whatever scope and
imports alone answer (local names, parameters, functions, classes, `self.`/`cls.` members, keyword arguments, imported
modules and names of the open folders) is found with ast in a few ms. jedi gets library code and attributes of
objects, and gives up when a newer request arrives or after JEDI_LIMIT seconds.
"""
import ast
import keyword
import os
import re
import sys
import threading
import time
from collections import OrderedDict, namedtuple
from pathlib import Path

from slate import heap, hints

JEDI_LIMIT = 3  # seconds: of 16 jedi lookups in prediction.py and draw.py that took longer (unlimited), 2 found something
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
ASSIGNING = (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.For, ast.AsyncFor, ast.With, ast.AsyncWith)
BRANCHES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try) + ((ast.TryStar,) if hasattr(ast, 'TryStar') else ())
MAX_DEPTH = 8  # imports and base classes followed in a row
JEDI_IDLE = 600  # seconds without a jedi lookup before its compiled-module subprocess ends, as the glycan process
FORGET = 120  # seconds without a request before the parsed trees are dropped (about 20 MB after a few lookups)
MAX_FILES = 6  # parsed modules kept: a tree takes about 25 times its source (draw.py: 161 KB, 8.6 MB), all 43 package files 115 MB
Binding = namedtuple('Binding', 'file kind node line offset name')  # where a name gets its value; offset in UTF-8 bytes, as ast counts
_projects = {}
_lock = threading.Lock()  # pywebview runs each call on its own thread, and jedi is not thread-safe
_newest = [0, None]  # [ticket, key] of the newest request: older ones still waiting or inside jedi give up
_done = (None, None)  # (key, result) of the last request, for the click that follows a Ctrl+hover on the same word
_files = OrderedDict()  # {path: (mtime, File or None)} of modules read from disk, most recently used last
_current = None  # File of the last text asked about
_running = [None, 0]  # [ticket, deadline] of the jedi lookup in progress
_watching = False
_idle = None  # timer that drops the parsed trees when go-to-definition rests
_jedi_idle = None  # timer that ends jedi's subprocess
_environment = None  # jedi's Python for its compiled-module subprocess
_libraries = {}  # {package: folder holding it} of the lab's libraries installed for the console's Python (libraries.py), set by goto


class Superseded(BaseException):
    """Raised inside jedi to stop it; a BaseException, so jedi's own `except Exception` cannot swallow it."""


class File:
    def __init__(self, path, source):
        with heap.paused():  # a big module's tree, which the collector would otherwise walk again and again as it grows (heap.py)
            self.tree = ast.parse(source)
        self.path, self.source = path, source
        self.lines = source.split('\n')
        self.scopes = {}  # {id(scope node): {name: [Binding]}}

    def at(self, line, offset, name):
        """{path, line, col, name} for a name at a byte offset, with the column in characters as Monaco counts."""
        text = self.lines[line - 1] if line <= len(self.lines) else ''
        return {'path': self.path, 'line': line, 'col': len(text.encode('utf-8')[:offset].decode('utf-8', 'replace')) + 1, 'name': name}

    def names(self, scope):
        """{name: [Binding]} for what scope binds itself, not the functions, classes and comprehensions nested in it."""
        if id(scope) not in self.scopes:
            self.scopes[id(scope)] = index = {}
            if isinstance(scope, FUNCTIONS):
                a = scope.args
                for arg in a.posonlyargs + a.args + [a.vararg] + a.kwonlyargs + [a.kwarg]:
                    if arg is not None:
                        index.setdefault(arg.arg, []).append(Binding(self, 'arg', arg, arg.lineno, arg.col_offset, arg.arg))
            if isinstance(scope, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                body = [g.target for g in scope.generators]
            else:
                body = [scope.body] if isinstance(scope, ast.Lambda) else scope.body
            for node in walk(body):
                for b in self.bindings(node):
                    index.setdefault(b.name, []).append(b)
        return self.scopes[id(scope)]

    def bindings(self, node):
        """The Bindings a single node makes; `x += 1` counts as an assignment of x, as in jedi."""
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            m = re.search(rb'\b(?:def|class)\s+(' + re.escape(node.name.encode('utf-8')) + rb')\b', self.lines[node.lineno - 1].encode('utf-8'))
            yield Binding(self, 'def', node, node.lineno, m.start(1) if m else node.col_offset, node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            yield Binding(self, 'name', node, node.lineno, node.col_offset, node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                if a.name != '*':
                    name = a.asname or (a.name.split('.')[0] if isinstance(node, ast.Import) else a.name)
                    offset = a.end_col_offset - len(a.asname.encode('utf-8')) if a.asname else a.col_offset
                    yield Binding(self, 'import' if isinstance(node, ast.Import) else 'from', (node, a), a.end_lineno, offset, name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            m = re.search(rb'\bas\s+(' + re.escape(node.name.encode('utf-8')) + rb')\b', self.lines[node.lineno - 1].encode('utf-8'))
            yield Binding(self, 'name', node, node.lineno, m.start(1) if m else node.col_offset, node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                yield Binding(self, 'global' if isinstance(node, ast.Global) else 'nonlocal', node, node.lineno, node.col_offset, name)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            yield Binding(self, 'name', node, node.lineno, node.col_offset, node.name)


def walk(nodes):
    """Nodes of a scope's own code: nested functions, classes and comprehensions are yielded but not entered."""
    todo = list(reversed(nodes))
    while todo:
        node = todo.pop()
        yield node
        if not isinstance(node, SCOPES):
            todo.extend(reversed(list(ast.iter_child_nodes(node))))


def start(node):
    """Where a node's source begins, counting a def's or class's decorators."""
    first = min([node] + getattr(node, 'decorator_list', []), key = lambda n: (n.lineno, n.col_offset))
    return first.lineno, first.col_offset


def chain_at(tree, pos):
    """The nodes from the module down to the innermost one whose source holds pos (line, byte offset)."""
    chain, children = [tree], list(ast.iter_child_nodes(tree))
    while children:
        node = children.pop(0)
        if not hasattr(node, 'lineno'):  # arguments, comprehension, withitem, match_case: no position of their own
            children[:0] = list(ast.iter_child_nodes(node))
        elif start(node) <= pos < (node.end_lineno, node.end_col_offset):
            chain.append(node)
            children = list(ast.iter_child_nodes(node))
    return chain


def read(path):
    """The parsed module at path (None when it does not parse), kept until the file changes or MAX_FILES newer ones are read."""
    try:
        mtime = Path(path).stat().st_mtime_ns
    except OSError:
        return None
    if path not in _files or _files[path][0] != mtime:
        try:
            _files[path] = (mtime, File(path, Path(path).read_bytes().decode('utf-8')))
        except (OSError, ValueError, SyntaxError):
            _files[path] = (mtime, None)
        while len(_files) > MAX_FILES:
            _files.popitem(last = False)
    _files.move_to_end(path)
    return _files[path][1]


def module_file(module, level, here, roots):
    """The file of a dotted module in the open folders (or relative to here, for `from .x import`), else None."""
    parts = [p for p in (module or '').split('.') if p]
    if level:
        bases = [Path(here).parents[level - 1]] if level <= len(Path(here).parents) else []
    else:
        folder = Path(here).parent
        own = [r for r in roots if Path(r) in Path(here).parents]
        bases = ([] if (folder / '__init__.py').is_file() else [folder]) + [Path(r) for r in own + [r for r in roots if r not in own]]  # a script imports its neighbours
        if parts and parts[0] in _libraries:
            bases.append(Path(_libraries[parts[0]]))  # a pip-installed library of the lab's, read with ast like the open folders
    for base in bases:
        p = base.joinpath(*parts)
        for f in ((p.with_name(p.name + '.py'), p / '__init__.py') if parts else (p / '__init__.py',)):
            if f.is_file():
                return str(f)
    return None


def module_binding(path):
    return Binding(None, 'module', path, 1, 0, '')


def submodule(path, name):
    """A package's submodule as a Binding, or None."""
    if Path(path).name != '__init__.py':
        return None
    p = Path(path).parent / name
    f = next((f for f in (p.with_name(name + '.py'), p / '__init__.py') if f.is_file()), None)
    return module_binding(str(f)) if f else None


def opened(path):
    """The parsed module at path: the open text when it is the file being edited, else the file on disk."""
    return _current if _current is not None and Path(path) == Path(_current.path) else read(path)


def imported(b, roots):
    """What an import Binding refers to, as Bindings in the module it names; None when that is not in the open folders."""
    stmt, alias = b.node
    if isinstance(stmt, ast.Import):
        path = module_file(alias.name if alias.asname else alias.name.split('.')[0], 0, b.file.path, roots)
        return [module_binding(path)] if path else None
    path = module_file(stmt.module, stmt.level, b.file.path, roots)
    if path is None:
        return None
    f = opened(path)
    found = f.names(f.tree).get(alias.name) if f is not None else None
    if found:
        return [x for x in found if x.kind not in ('global', 'nonlocal')]
    sub = submodule(path, alias.name)
    return [sub] if sub else None


def resolve(file, chain, name, pos):
    """The Bindings a name used at pos refers to, from the innermost scope that binds it; None for builtins and star imports."""
    scopes = [n for n in chain if isinstance(n, SCOPES + (ast.Module,))]
    same = True  # still in the code that runs where the name is used, not a def or class around it
    for scope in reversed(scopes):
        if isinstance(scope, ast.ClassDef) and scope is not scopes[-1]:
            continue  # a class body's names are not visible inside its methods
        found = file.names(scope).get(name)
        if found and any(b.kind == 'global' for b in found):
            return [b for b in file.names(file.tree).get(name, []) if b.kind != 'global'] or None
        if found and not any(b.kind == 'nonlocal' for b in found):
            return reaching(file, chain, found, pos) if same else found
        same = same and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    return None


def branches(chain):
    """The branches of if/for/while/try a node sits in, as (statement, field) pairs; a for's target counts as its body."""
    out = []
    for parent, child in zip(chain, chain[1:]):
        if isinstance(parent, BRANCHES):
            field = next((name for name, value in ast.iter_fields(parent) if value is child or isinstance(value, list) and any(v is child for v in value)), None)
            out.append((id(parent), id(child) if field == 'handlers' else 'body' if field == 'target' else field))
    return out


def reaching(file, chain, found, pos):
    """The assignments a use can see, as jedi finds them but without inferring conditions: from the last one before
    the use back to the first that runs whenever the use does, i.e. not in an if/for/while/try branch the use is not in."""
    out, here, statement = [], branches(chain), next((n for n in reversed(chain) if isinstance(n, ast.stmt)), None)
    for b in sorted((b for b in found if (b.line, b.offset) <= pos), key = lambda b: (b.line, b.offset), reverse = True):
        theirs = chain_at(file.tree, (b.line, b.offset))
        if statement in theirs and isinstance(statement, ASSIGNING):
            continue  # the statement that assigns the name uses its earlier value (x = x + 1, x -= 1, for x in x)
        out.append(b)
        mine = branches(theirs)
        if here[:len(mine)] == mine:
            break
    return out or found


def locations(bindings, roots, depth = 0):
    """[{path, line, col, name}] for Bindings, following imports to what they import; None when one leaves the open folders."""
    out = []
    for b in bindings:
        if b.kind == 'module':
            out.append({'path': b.node, 'line': 1, 'col': 1, 'name': ''})
        elif b.kind in ('import', 'from'):
            inner = imported(b, roots) if depth < MAX_DEPTH else None
            found = locations(inner, roots, depth = depth + 1) if inner is not None else None
            if found is None:
                return None
            out += found
        else:
            out.append(b.file.at(b.line, b.offset, b.name))
    return out


def values(bindings, roots, depth):
    """[(File, module, class or function node)] that Bindings hold; None when one of them takes type inference."""
    out = []
    for b in bindings:
        if depth >= MAX_DEPTH:
            return None
        if b.kind == 'def':
            out.append((b.file, b.node))
        elif b.kind == 'module':
            f = opened(b.node)
            if f is None:
                return None
            out.append((f, f.tree))
        elif b.kind in ('import', 'from'):
            inner = imported(b, roots)
            found = values(inner, roots, depth + 1) if inner is not None else None
            if found is None:
                return None
            out += found
        else:
            return None
    return out


def expression(file, chain, node, pos, roots, depth = 0):
    """[(File, node)] for a Name or dotted Attribute that names a module, class or function of the open folders, else None."""
    if isinstance(node, ast.Name):
        found = resolve(file, chain, node.id, pos)
        if found and len(found) == 1 and found[0].kind == 'arg':
            cls = owner(chain, found[0].node)
            if cls is not None:
                return [(file, cls)]  # self or cls
        return values(found, roots, depth) if found else None
    if isinstance(node, ast.Attribute):
        outer = expression(file, chain, node.value, pos, roots, depth = depth)
        found = members(outer, node.attr, roots, depth + 1) if outer is not None else None
        return values(found, roots, depth + 1) if found else None
    return None


def owner(chain, arg):
    """The class whose method takes arg as its first parameter (self or cls), else None."""
    for parent, fn in zip(chain, chain[1:]):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and isinstance(parent, ast.ClassDef):
            first = (fn.args.posonlyargs + fn.args.args)[:1]
            static = any(isinstance(d, ast.Name) and d.id == 'staticmethod' for d in fn.decorator_list)
            if first and first[0] is arg and not static:
                return parent
    return None


def members(holders, attr, roots, depth):
    """Bindings of attr in modules and classes, or None when one of them cannot answer without inference."""
    out = []
    for f, node in holders:
        if isinstance(node, ast.Module):
            found = [b for b in f.names(node).get(attr, []) if b.kind not in ('global', 'nonlocal')]
            sub = submodule(f.path, attr) if not found else None
            found = found or ([sub] if sub else None)
        elif isinstance(node, ast.ClassDef):
            found = class_member(f, node, attr, roots, depth)
        else:
            found = None  # an attribute of what a function returns
        if not found:
            return None
        out += found
    return out


def class_member(f, cls, attr, roots, depth):
    """attr in a class: its body, then `self.attr =` in its methods, then its base classes ([] when absent, None when a base is unknown)."""
    found = f.names(cls).get(attr)
    if found:
        return found
    found = []
    for fn in cls.body:
        first = (fn.args.posonlyargs + fn.args.args)[:1] if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) else []
        for node in ast.walk(fn) if first else ():
            if (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store) and node.attr == attr and isinstance(node.value, ast.Name)
                    and node.value.id == first[0].arg):
                found.append(Binding(f, 'name', node, node.end_lineno, node.end_col_offset - len(attr.encode('utf-8')), attr))
    if found:
        return found
    for base in cls.bases:
        holders = expression(f, [f.tree], base, start(cls), roots, depth = depth + 1) if depth < MAX_DEPTH else None
        if holders is None or not all(isinstance(n, ast.ClassDef) for _, n in holders):
            return None  # a library base class (nn.Module) or one built at runtime: up to jedi
        for hf, hcls in holders:
            inner = class_member(hf, hcls, attr, roots, depth + 1)
            if inner is None:
                return None
            if inner:
                return inner
    return found


def parameter(file, chain, call, name, pos, roots):
    """Locations of the parameter a keyword argument names, in the function or class (its __init__) called."""
    holders = expression(file, chain, call.func, pos, roots)
    if holders is None:
        return None
    out = []
    for f, node in holders:
        fns = [node] if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else []
        if isinstance(node, ast.ClassDef):
            fns = [b.node for b in class_member(f, node, '__init__', roots, 0) or [] if b.kind == 'def']
        for fn in fns:
            a = fn.args
            out += [f.at(arg.lineno, arg.col_offset, arg.arg) for arg in a.posonlyargs + a.args + a.kwonlyargs if arg.arg == name]
    return out or None


def find(file, pos, roots):
    """Definitions of the name at pos (line, byte offset): a list, or None when it takes jedi."""
    chain = chain_at(file.tree, pos)
    node, parent = chain[-1], chain[-2] if len(chain) > 1 else None
    if isinstance(node, ast.Name):
        if isinstance(node.ctx, ast.Store) and not (isinstance(parent, ast.AugAssign) and parent.target is node):
            return [file.at(node.lineno, node.col_offset, node.id)]
        found = resolve(file, chain, node.id, pos)
        return locations(found, roots) if found else None
    if isinstance(node, ast.Attribute):
        holders = expression(file, chain, node.value, pos, roots)
        found = members(holders, node.attr, roots, 1) if holders is not None else None
        return locations(found, roots) if found else None
    if isinstance(node, ast.arg):
        return [file.at(node.lineno, node.col_offset, node.arg)]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.ExceptHandler)):
        b = next(file.bindings(node), None)
        return [file.at(b.line, b.offset, b.name)] if b and (b.line, b.offset) == pos else None  # on its name, else in its header
    if isinstance(node, ast.alias):
        name = node.asname or node.name
        if node.asname and pos[1] >= node.end_col_offset - len(node.asname.encode('utf-8')):
            return [file.at(node.end_lineno, node.end_col_offset - len(node.asname.encode('utf-8')), name)]  # the `as` name itself
        if isinstance(parent, ast.ImportFrom):
            return locations([Binding(file, 'from', (parent, node), node.lineno, node.col_offset, name)], roots)
        dotted = node.name.encode('utf-8')
        cut = dotted.find(b'.', pos[1] - node.col_offset)
        path = module_file((dotted if cut < 0 else dotted[:cut]).decode('utf-8'), 0, file.path, roots)  # the module up to the clicked part
        return [{'path': path, 'line': 1, 'col': 1, 'name': ''}] if path else None
    if isinstance(node, ast.ImportFrom):
        line = file.lines[node.lineno - 1].encode('utf-8')
        m = re.compile(rb'from\s+(\.*)\s*([\w.]*)').match(line, node.col_offset)
        if not m or not m.start(2) <= pos[1] <= m.end(2):
            return []
        cut = line.find(b'.', pos[1], m.end(2))
        module = line[m.start(2):cut if cut >= 0 else m.end(2)].decode('utf-8')
        path = module_file(module, len(m.group(1)), file.path, roots)
        return [{'path': path, 'line': 1, 'col': 1, 'name': ''}] if path else None
    if isinstance(node, ast.keyword) and isinstance(parent, ast.Call) and node.arg:
        return parameter(file, chain[:-1], parent, node.arg, pos, roots)
    if isinstance(node, ast.Global):
        name = file.lines[pos[0] - 1].encode('utf-8')[pos[1]:]
        name = re.match(rb'\w+', name).group().decode('utf-8')
        return locations([b for b in file.names(file.tree).get(name, []) if b.kind != 'global'], roots) or None
    if isinstance(node, (ast.Module, ast.Constant, ast.JoinedStr)):
        return []  # a comment, blank space or a string
    return None


def forget():
    global _current, _done
    with _lock:
        _files.clear()
        _current, _done = None, (None, None)


def environment():
    """jedi's Python, whose subprocess imports compiled libraries to inspect them. Its numpy runs OpenBLAS on one thread:
    by default OpenBLAS reserves about 32 MB per thread on import (717 MB on 22), of which about 10 MB is ever used."""
    global _environment
    if _environment is None:
        import jedi
        _environment = jedi.create_environment(jedi.get_default_environment().executable, safe = False,
                                               env_vars = dict(os.environ, OPENBLAS_NUM_THREADS = '1'))  # replaces the whole environment
    return _environment


def stop_jedi():
    """End jedi's compiled-module subprocess and the libraries it imported; jedi starts a new one on its next lookup,
    since `_kill` marks the old one crashed. Both are jedi internals, so a jedi that renames them keeps its subprocess."""
    with _lock:
        sub = getattr(_environment, '_subprocess', None)
        if sub is not None and not getattr(sub, 'is_crashed', True) and hasattr(sub, '_kill'):
            sub._kill()


def project_for(path, roots):
    import jedi  # imported on the first jedi lookup: at startup it cost 200 ms before the window appeared
    root = next((r for r in roots if Path(r) in Path(path).parents), str(Path(path).parent))
    if root not in _projects:
        _projects[root] = jedi.Project(root, added_sys_path = [r for r in roots if r != root])  # modules from the other open folders resolve even when not pip-installed
    return _projects[root]


def watch():
    """Let a newer request or JEDI_LIMIT stop jedi, on entering a function of its inference (Python 3.12+; before, jedi runs to the end).

    sys.settrace made jedi 3-5 times slower (tracing turns off 3.12's specialised bytecode). A PY_START monitor costs
    nothing measurable: each function outside jedi's syntax_tree.py is seen once and then switched off. It only stops
    there, never halfway through an exchange with jedi's compiled-module subprocess.
    """
    global _watching
    mon = getattr(sys, 'monitoring', None)
    tool = next((i for i in range(6) if mon.get_tool(i) is None), None) if mon and not _watching else None
    _watching = True
    if tool is None:
        return
    from jedi.inference import syntax_tree
    where = syntax_tree.__file__  # importlib sets co_filename to __file__, also when loading a .pyc

    def started(code, offset):
        if code.co_filename != where:
            return mon.DISABLE
        ticket, deadline = _running
        if ticket is not None and (_newest[0] != ticket or time.monotonic() > deadline):
            raise Superseded

    mon.use_tool_id(tool, 'slate go-to-definition')
    mon.register_callback(tool, mon.events.PY_START, started)
    mon.set_events(tool, mon.events.PY_START)


def keep_stub_maps():
    """jedi lists each imported package's folder for .pyi stubs again in every lookup: 187 listings and 6,917 isdir
    calls, 0.9 s, for one attribute in draw.py. List each folder once per session instead."""
    from jedi.inference.gradual import typeshed
    merge = getattr(typeshed, '_merge_create_stub_map', None)
    if merge is None or getattr(merge, 'kept', None) is not None:
        return
    kept = {}

    def merged(path_infos):
        path_infos = tuple(path_infos)
        if path_infos not in kept:
            kept[path_infos] = merge(path_infos)
        return kept[path_infos]

    merged.kept = kept
    typeshed._merge_create_stub_map = merged


def with_jedi(path, source, line, col, roots, ticket):
    global _jedi_idle
    import jedi
    watch()
    keep_stub_maps()
    _running[:] = [ticket, time.monotonic() + JEDI_LIMIT]
    try:
        script = jedi.Script(code = source, path = path, project = project_for(path, roots), environment = environment())
        names = script.goto(line, col, follow_imports = True) or script.infer(line, col)
    finally:
        _running[:] = [None, 0]
        if _jedi_idle is not None:
            _jedi_idle.cancel()
        _jedi_idle = threading.Timer(JEDI_IDLE, stop_jedi)
        _jedi_idle.daemon = True
        _jedi_idle.start()
    out = []
    for n in names:
        if n.module_path is None or n.line is None:
            continue  # builtins compiled into the interpreter have no source to open
        out.append({'path': str(n.module_path), 'line': n.line, 'col': n.column + 1, 'name': n.name})
    return out


def goto(path, source, line, col, roots, libraries = None):
    """Definitions of the name at a 1-based line and 0-based column: [{path, line, col, name}]. libraries: {package: folder}
    of the lab's libraries (libraries.NAMES) where the console's Python installed them, read like the open folders."""
    global _current, _done, _idle
    lines = source.split('\n')
    text = lines[line - 1] if 0 < line <= len(lines) else ''
    word = next((m for m in re.finditer(r'\w+', text) if m.start() <= col <= m.end()), None)
    if word is None or word.group()[0].isdigit() or keyword.iskeyword(word.group()) or 0 <= hints.comment_start(text) < word.start():
        return []
    key = (path, line, word.start(), source)
    if _newest[1] != key:
        _newest[:] = [_newest[0] + 1, key]
    ticket = _newest[0]
    with _lock:
        if _newest[0] != ticket:
            return []  # the mouse moved on while this one waited
        _libraries.clear()
        _libraries.update(libraries or {})
        if _done[0] == key:
            return _done[1]
        if _current is None or _current.path != path or _current.source != source:
            try:
                _current = File(path, source)
            except (SyntaxError, ValueError):
                _current = None  # being typed: jedi's parser copes
        with heap.paused():
            found = find(_current, (line, len(text[:word.start()].encode('utf-8'))), roots) if _current is not None else None
        if found is None:
            try:
                found = with_jedi(path, source, line, word.start(), roots, ticket)
            except Superseded:
                return []
        found = list({(d['path'], d['line'], d['col']): d for d in found}.values())
        _done = (key, found)
        if _idle is not None:
            _idle.cancel()
        _idle = threading.Timer(FORGET, forget)
        _idle.daemon = True
        _idle.start()
    return found


def outline(source):
    """The classes and functions of a module for the file structure popup (Ctrl+F12): [{name, kind, line, end, children}],
    or None when the text does not parse (the page then keeps the last one). Defs under if, try or with count as the
    level they sit in: glycowork defines fallbacks inside try/except ImportError."""
    try:
        with heap.paused():
            tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None

    def symbols(body, in_class):
        out = []
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                is_class = isinstance(node, ast.ClassDef)
                out.append({'name': node.name, 'kind': 'class' if is_class else 'method' if in_class else 'function', 'line': node.lineno, 'end': node.end_lineno,
                            'children': symbols(node.body, is_class)})
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.AsyncWith, ast.For, ast.While)):
                inner = node.body + getattr(node, 'orelse', []) + getattr(node, 'finalbody', []) + [n for h in getattr(node, 'handlers', []) for n in h.body]
                out += symbols(inner, in_class)
        return out
    return symbols(tree.body, False)
