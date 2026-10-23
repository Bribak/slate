"""Glycans in the code (app.js, "glycan drawings"): SNFG drawings, the hover card, diagnostics with quick fixes, and search by
motif, all answered by glycowork in a process of its own (glycan_child.py).

glycowork runs in that process, started on the first request: importing it takes 1-2 s and 38-70 MB, which the editor's
process should neither wait for nor keep. It ends after IDLE seconds without a request, and starts again when a file with
glycans is shown. Drawing takes 2-7 ms per glycan, a check about 1 ms, a hover card 2-10 ms.

Diagnostics only look at glycans written as values: `literals` leaves out test files, where other notations and broken
glycans are written on purpose to test glycowork, docstrings, and strings inside string methods (.replace, .startswith),
`in` tests, re and glycowork's conversion and glyco-regex functions, and replacement pairs. Among 123 glycan-like strings
in glycowork's and CandyCrunch's own code that left nothing: everything canonicalize_iupac would have changed there was
prose, a fragment for .replace, or a rewrite that changes meaning (a2-? to a2-3/6).
"""
import ast
import bisect
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from slate import workspace
from slate.system import NO_WINDOW, console_python

CHILD = Path(__file__).with_name('glycan_child.py')
IDLE = 600
GLYCAN = re.compile(r'[A-Za-z[{][A-Za-z0-9?\-()[\]{},.|/]*')
LINKAGE = re.compile(r'\([abo?][\d?]?-[\d?][\d?|/,]*\)')
SHORT_IUPAC = re.compile(r'^(?:[A-Z][A-Za-z0-9]*[ab?][\d?]-[\d?])+[A-Z][A-Za-z0-9]*$')  # Neu5Aca2-3Galb1-4GlcNAc, IUPAC without parentheses
ANY_GLYCAN = re.compile(r'\([abo?][\d?]?-[\d?]|WURCS=|[a-z][ab?][\d?]-[\d?][A-Z]')  # whether a file may hold glycans at all
IN_TEXT = re.compile(r'[A-Za-z[{](?:[A-Za-z0-9?\-.|/[\]{}]|\([^()\s]*\))*')  # a glycan in a line of code or a table: commas only inside a linkage
STRING_METHODS = {'replace', 'startswith', 'endswith', 'split', 'rsplit', 'strip', 'lstrip', 'rstrip', 'find', 'rfind', 'index', 'rindex',
                  'count', 'partition', 'rpartition', 'removeprefix', 'removesuffix', 'join', 'format', 'sub', 'subn', 'match', 'search',
                  'fullmatch', 'findall', 'finditer', 'compile', 'escape', 'multireplace'}
CONVERTERS = re.compile(r'^(canonicali[sz]e\w*|check_nomenclature|\w+_to_iupac|iupac_to_\w+|get_match\w*|compile_pattern|compile_component|\w*regex\w*)$')
TESTS = re.compile(r'(^|[\\/])(tests?)[\\/]|(^|[\\/])(test_[^\\/]*|[^\\/]*_test|conftest)\.py$')
TOKENS = re.compile(r'[^()[\]{}]+\([^()]*\)|[^()[\]{}]+$')  # each residue with its linkage, and the reducing end
MAX_CHECKED = 20_000
SEARCH_EXT = {'.py', '.csv', '.tsv', '.txt'}
MAX_GLYCANS = 50_000  # distinct glycans a motif search hands to glycowork
_tree = (None, None, None)  # (path, text, tree) of the last lint, which parsed the text the glycan check gets next


class Drawer:
    def __init__(self):
        self._proc, self._key, self._used = None, None, 0
        self._lock = threading.Lock()  # pywebview runs each call on its own thread, and requests share one pipe
        self._checked = {}  # {glycan: check result}, so each glycan is canonicalized once per session

    def draw(self, glycans, python = None, paths = ()):
        """{glycan: [small svg, full svg] or None}, or {'error': message} when glycowork cannot be imported."""
        return self.request(list(glycans), python, paths)

    def request(self, msg, python = None, paths = ()):
        with self._lock:
            key = (python, tuple(paths))
            if self._proc is None or self._proc.poll() is not None or key != self._key:
                error = self._start(python, paths)
                if error:
                    return {'error': error}
                self._key = key
            self._used = time.time()
            try:
                self._proc.stdin.write((json.dumps(msg) + '\n').encode('utf-8'))
                self._proc.stdin.flush()
                reply = self._proc.stdout.readline()
            except OSError:
                reply = b''
            if not reply:
                self.stop()
                return {'error': 'the drawing process stopped'}
            return json.loads(reply)

    def running(self):
        return self._proc is not None and self._proc.poll() is None

    def check(self, glycans, python = None, paths = ()):
        """{glycan: {'canonical'} or {'error'}} for glycans written as values, each asked of glycowork once per session."""
        new = [g for g in dict.fromkeys(glycans) if g not in self._checked]
        if new:
            reply = self.request({'op': 'check', 'glycans': new}, python, paths)
            if 'error' in reply and not isinstance(reply.get('error'), dict):
                return reply
            if len(self._checked) > MAX_CHECKED:
                self._checked.clear()
            self._checked.update(reply)
        return {g: self._checked[g] for g in glycans if g in self._checked}

    def _start(self, python, paths):
        self.stop()
        python = console_python(python if python and Path(python).is_file() else sys.executable)  # real stdio pipes, as for the console
        env = dict(os.environ, PYTHONIOENCODING = 'utf-8', MPLBACKEND = 'Agg')
        self._proc = subprocess.Popen([str(python), '-u', str(CHILD), *paths], stdin = subprocess.PIPE, stdout = subprocess.PIPE,
                                      stderr = subprocess.DEVNULL, env = env, creationflags = NO_WINDOW)
        first = json.loads(self._proc.stdout.readline() or '{"error": "the drawing process did not start"}')
        if 'error' in first:
            self.stop()
            return first['error']
        self._used = time.time()
        threading.Thread(target = self._stop_when_idle, args = (self._proc,), daemon = True).start()
        return None

    def _stop_when_idle(self, proc):
        while proc is self._proc and proc.poll() is None:
            time.sleep(30)
            with self._lock:
                if proc is self._proc and time.time() - self._used > IDLE:
                    self.stop()

    def stop(self):
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.kill()


def remember(path, text, tree):
    """The tree style's lint just parsed: the glycan check comes right after for the same text, and parsing glycowork's
    processing.py takes 63 ms."""
    global _tree
    _tree = (path, text, tree)


def hot_nodes(tree, hot):
    """{node: parent} for the nodes on lines in hot, walking only into statements that span one of them: on glycowork's
    processing.py, walking and mapping all 21,000 nodes took 66 ms."""
    parents, todo = {}, [tree]
    while todo:
        node = todo.pop()
        for child in ast.iter_child_nodes(node):
            lo, hi = getattr(child, 'lineno', None), getattr(child, 'end_lineno', None)
            i = bisect.bisect_left(hot, lo) if lo is not None else 0
            if lo is not None and not (i < len(hot) and hot[i] <= hi):
                continue  # no glycan line inside: hot is sorted, so one lookup tells
            parents[child] = node
            todo.append(child)
    return parents


def is_test(path):
    return bool(TESTS.search(str(path)))


def call_name(call):
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, 'id', '')


def written_as_value(node, parents):
    """False for a string that is text to search or rewrite rather than a glycan: inside a string method, re or a conversion
    or glyco-regex function, an `in` test, a pair of strings (old, new) or a docstring."""
    child, p = node, parents.get(node)
    if isinstance(p, ast.Expr):
        return False  # a docstring, or a string on its own
    while p is not None and not isinstance(p, ast.stmt):
        if isinstance(p, ast.Call) and (call_name(p) in STRING_METHODS or CONVERTERS.match(call_name(p)) or getattr(p.func, 'value', None) is not None and getattr(p.func.value, 'id', '') == 're'):
            return False
        if isinstance(p, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in p.ops):
            return False
        if isinstance(p, ast.Tuple) and len(p.elts) == 2 and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in p.elts):
            return False  # ("Neu5Ac(a2-?)", "Neu5Ac(a2-3/6)") pairs that a loop hands to .replace
        if isinstance(p, ast.Dict) and child in p.keys:
            return False  # the keys of a mapping are what gets looked up or replaced
        child, p = p, parents.get(p)
    return True


def literals(path, text):
    """[(line, start, end, glycan)] for each glycan written as a string value in a Python file, with 1-based character
    columns of the string's content, as Monaco counts them; [] for test files."""
    if is_test(path) or not ANY_GLYCAN.search(text):
        return []
    lines, out = text.split('\n'), []
    hot = [n for n, line in enumerate(lines, 1) if ANY_GLYCAN.search(line)]
    tree = _tree[2] if _tree[0] == path and _tree[1] == text else None
    try:
        tree = tree or ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    parents = hot_nodes(tree, hot)
    for node in parents:
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str) and node.lineno == node.end_lineno):
            continue
        g = node.value
        if not g or any(c.isspace() for c in g) or not (g.startswith('WURCS=') or (GLYCAN.fullmatch(g) and (LINKAGE.search(g) or SHORT_IUPAC.match(g)))):
            continue
        if not written_as_value(node, parents):
            continue
        raw = lines[node.lineno - 1].encode('utf-8')
        literal = raw[node.col_offset:node.end_col_offset].decode('utf-8', 'replace')
        inner = literal.lstrip('rRuUbBfF')
        quote = inner[:3] if inner[:3] in ('"""', "'''") else inner[:1]
        if inner[len(quote):len(inner) - len(quote)] != g:
            continue  # escapes or implicit concatenation: the text on screen is not the value
        start = len(raw[:node.col_offset].decode('utf-8', 'replace')) + len(literal) - len(inner) + len(quote) + 1
        out.append((node.lineno, start, start + len(g), g))
    return out


def unbalanced(g):
    """The first bracket without its partner, or None: glycowork reads 'Gal(b1-4)GlcNAc)' without complaint."""
    stack = []
    for c in g:
        if c in '([{':
            stack.append(c)
        elif c in ')]}':
            if not stack or '([{'[')]}'.index(c)] != stack.pop():
                return c
    return stack[-1] if stack else None


def same_residues(a, b):
    """True when b only orders a's branches differently: the same residues with the same linkages."""
    return sorted(TOKENS.findall(a)) == sorted(TOKENS.findall(b))


def diagnostics(drawer, path, text, python = None, paths = ()):
    """[{line, start, end, severity, message, fix}] for a file's glycans: one glycowork cannot read (warning), one written in
    another notation (hint, fix: IUPAC-condensed), one whose branches glycowork orders differently (hint, fix: that order).
    None while no drawing process runs: a check alone must not start it (+40 MB for glycowork's processing.py, whose
    glycans were all off screen), so the page asks again once a glycan on screen has been drawn."""
    found = literals(path, text)
    if not found:
        return []
    if not drawer.running():
        return None
    checked = drawer.check([g for *_, g in found], python, paths)
    if 'error' in checked and isinstance(checked['error'], str):
        return []  # no glycowork: the drawings already said so
    out = []
    for line, start, end, g in found:
        result = checked.get(g) or {}
        where = {'line': line, 'start': start, 'end': end}
        bracket = unbalanced(g) if g[-1].isalnum() else None
        if bracket:
            out.append({**where, 'severity': 'warning', 'message': f"Unbalanced brackets in this glycan: a '{bracket}' has no partner"})
        elif 'error' in result:
            if g[-1].isalnum() and not g.startswith('WURCS='):  # one ending in a linkage is a fragment
                out.append({**where, 'severity': 'warning', 'message': f"glycowork cannot read this glycan: {result['error']}"})
        elif result.get('canonical') and result['canonical'] != g:
            c = result['canonical']
            if not LINKAGE.search(g):
                out.append({**where, 'severity': 'hint', 'message': f'In IUPAC-condensed: {c}', 'fix': c, 'title': 'Convert to IUPAC-condensed'})
            elif same_residues(g, c):
                out.append({**where, 'severity': 'hint', 'message': f'glycowork writes this glycan as {c}', 'fix': c, 'title': "Use glycowork's order"})
    return out


def search(drawer, roots, query, python = None, paths = ()):
    """Find-in-files results for the glycans in the open folders that contain a motif (glycan_child.Tools.matcher):
    glycan strings in .py files and glycan cells in .csv/.tsv/.txt tables."""
    where = {}  # {glycan: [match]}
    for i, rel in workspace.walk_files(roots):
        if os.path.splitext(rel)[1].lower() not in SEARCH_EXT:
            continue
        path = os.path.join(roots[i], rel)
        try:
            if os.path.getsize(path) > workspace.MAX_SEARCH:
                continue
            text = Path(path).read_text(encoding = 'utf-8', errors = 'replace')
        except OSError:
            continue
        if not LINKAGE.search(text):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if '(' not in line:
                continue
            for m in IN_TEXT.finditer(line):
                g = m.group().rstrip('.')
                if LINKAGE.search(g) and len(where) < MAX_GLYCANS:
                    where.setdefault(g, []).append({'path': path, 'root': i, 'rel': rel, 'line': n, 'start': m.start(), 'end': m.start() + len(g), 'text': line[:400]})
    if not where:
        return {'matches': [], 'files': 0, 'truncated': False, 'glycans': 0}
    reply = drawer.request({'op': 'match', 'query': query, 'glycans': list(where)}, python, paths)
    if 'error' in reply:
        raise ValueError(reply['error'])
    matches = [hit for g in reply['matches'] for hit in where[g]]
    matches.sort(key = lambda h: (h['root'], h['rel'], h['line']))
    lines = list({(h['path'], h['line']): h for h in matches}.values())[:workspace.MAX_MATCHES]  # one hit per line, as in find in files
    return {'matches': lines, 'files': len({h['path'] for h in lines}), 'truncated': bool(reply.get('partial')) or len(lines) >= workspace.MAX_MATCHES,
            'glycans': len(reply['matches'])}
