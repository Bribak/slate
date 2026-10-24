"""What follows what in glycowork's glycans, for suggesting monosaccharides and linkages while a glycan is typed (app.js,
glycan typing).

IUPAC-condensed is written from the non-reducing end: each residue's linkage ties it to the next residue at its own
branch depth, so after 'Gal(b1-4)' comes Gal's parent. Counted over df_glycan's glycans, each glycan once:
* links: {child: {linkage: n}}, the linkage a residue takes, and under '*' all of them;
* parents: {'child|linkage': {parent: n}}, and under '*|linkage' for any child;
* leaves: {residue: n}, the residues that start a chain (a glycan, or a branch after '[').
Like glytoucan.py, this file runs as a script (stdlib only, without importing glycowork) to write the counts once per
glycowork next to state.json; the page asks for them on the first glycan typed and suggests from them itself.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from collections import Counter, defaultdict
from pathlib import Path

TOKEN = re.compile(r'\[|\]|([^()[\]]+)(\([^()]*\))?')
LINK = re.compile(r'[abo?][\d?]?-[\d?][\d?|/,]*')
MIN_COUNT = 2  # a pair seen once is as likely a typo in the data as biology, and halves the file
TOP = 20  # suggestions kept per residue or linkage: a suggest list shows about 12
TOP_LEAVES = 400  # residues that start a chain, filtered as they are typed
_lock = threading.Lock()
_failed = set()


def edges(glycan):
    """([(child, linkage, parent)], [leaf]) for an IUPAC-condensed glycan, or None for anything else."""
    nodes, has_child, pending, out, depth, end = [], set(), defaultdict(list), [], 0, 0
    for m in TOKEN.finditer(glycan):
        if m.start() != end:
            return None
        end = m.end()
        if m.group() == '[':
            depth += 1
        elif m.group() == ']':
            depth -= 1
            if depth < 0:
                return None
        else:
            name, link = m.group(1), m.group(2)
            if link and not LINK.fullmatch(link[1:-1]):
                return None
            i = len(nodes)
            nodes.append(name)
            for k, l in pending.pop(depth, []) + pending.pop(depth + 1, []):  # the chain before it, and the branches just closed
                out.append((nodes[k], l, name))
                has_child.add(i)
            if link:
                pending[depth].append((i, link[1:-1]))
    if end != len(glycan) or depth or any(pending.values()) or not nodes:
        return None
    return out, [nodes[i] for i in range(len(nodes)) if i not in has_child]


def build(sugarbase, out):
    with (__import__('lzma').open if sugarbase.endswith('.xz') else open)(sugarbase, 'rt', encoding = 'utf-8') as f:
        data = json.load(f)
    g = data['columns'].index('glycan')
    glycans = {c.get('value') if isinstance(c, dict) else c for c in (r[g] for r in data['data'])}
    del data
    links, parents, leaves = defaultdict(Counter), defaultdict(Counter), Counter()
    for glycan in glycans:
        found = isinstance(glycan, str) and '{' not in glycan and edges(glycan)
        if not found:
            continue
        for child, link, parent in found[0]:
            links[child][link] += 1
            links['*'][link] += 1
            parents[f'{child}|{link}'][parent] += 1
            parents[f'*|{link}'][parent] += 1
        leaves.update(found[1])
    keep = lambda counts, top = TOP: {k: n for k, n in counts.most_common(top) if n >= MIN_COUNT}
    result = {'links': {k: keep(v) for k, v in links.items()}, 'parents': {k: keep(v) for k, v in parents.items()}, 'leaves': keep(leaves, TOP_LEAVES)}
    result = {key: {k: v for k, v in part.items() if v} if key != 'leaves' else part for key, part in result.items()}
    tmp = Path(str(out) + '.tmp')
    tmp.write_text(json.dumps(result, separators = (',', ':')), encoding = 'utf-8')
    os.replace(tmp, out)


def counts(python, roots):
    """The counts for the glycowork the drawing process would use, built first if needed; None without glycowork."""
    from slate import glytoucan, state
    from slate.system import NO_WINDOW, console_python
    folder = glytoucan.glycowork_folder(python, roots)
    found = folder and glytoucan.sources(folder)
    if not found:
        return None
    signature = json.dumps([str(found[0]), found[0].stat().st_mtime_ns, found[0].stat().st_size])
    home = state.STATE_FILE.parent
    out = home / f'linkages-{hashlib.sha1(signature.encode()).hexdigest()[:12]}.json'
    with _lock:
        if not out.is_file() and signature not in _failed:
            home.mkdir(parents = True, exist_ok = True)
            try:
                subprocess.run([str(console_python(sys.executable)), '-I', __file__, str(found[0]), str(out)], capture_output = True, timeout = 120,
                               creationflags = NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired):
                pass
            if not out.is_file():
                _failed.add(signature)
            for old in sorted(home.glob('linkages-*.json'), key = lambda p: p.stat().st_mtime, reverse = True)[glytoucan.KEPT:]:
                try:
                    old.unlink()
                except OSError:
                    pass
    try:
        return json.loads(out.read_text(encoding = 'utf-8'))
    except (OSError, ValueError):
        return None


if __name__ == '__main__':
    build(*sys.argv[1:3])
