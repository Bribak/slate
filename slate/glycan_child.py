"""The process that draws glycans for the editor (slate/glycans.py). It imports glycowork once and then answers one JSON line
per request on stdin with one JSON line on stdout:
* a list of IUPAC-condensed glycans: {glycan: [svg, sticker] or null}, drawn compact and without linkage labels, as a
  die-cut sticker where glycowork has them (sticker true), for next to the string.
* {"op": "full", "glycan": ...}: {"svg": ..., "sticker": ...}, the usual GlycoDraw with linkages, as a sticker where
  glycowork has them, for the hover. Only hovered glycans need it, so it is drawn then rather than with every glycan on screen.
* {"op": "highlight", "glycan": ..., "motif": ...}: the same, with a motif from glycowork's list (by name) highlighted, for
  the hover card's and the table panel's motif names.
* {"op": "groups", "groups": {name: [values]}, "names": {name: [samples]}, "palette": ...}: {"svg": ..., "width": ...,
  "dots": ...}, a table row's samples as a box plot per group, drawn with viscose, or {"error": ...}; dots, with viscose 0.2,
  is a dot map as repl_child.dot_map gives one, which names the sample under the mouse.
* {"op": "check", "glycans": [...]}: {glycan: {"canonical": ...} or {"error": ...}}, through canonicalize_iupac.
* {"op": "info", "glycan": ...}: the hover card: mass, composition, class, motifs, canonical form.
* {"op": "match", "query": ..., "glycans": [...]}: {"matches": [...]} for a motif name from glycowork's motif list, a
  glycan (sub)structure or a glyco-regex, as glycowork's annotate_glycan and get_match find them.
* {"op": "motifs"}: {"motifs": [names]}.
Anything glycowork prints goes to stderr, which the parent discards, so stdout carries only the replies.

pandas stays out of this process (38 MB with glycowork 1.11.1, 70+ MB once pandas loads): the motif list is read with csv
rather than annotate.py, which imports pandas, and `mass` lends composition_to_mass a NaN check for the one pd.isna it calls
(glycowork/.claude/changes/2026-10-23-pandas-free-mass.md proposes that fix in glycowork itself).
"""
import ast
import csv
import json
import os
import re
import sys
import time
import types
import warnings
from importlib import resources

REAL_OUT = sys.stdout
MATCH_LIMIT = 15  # seconds a motif search may take before it answers with what it has
NAN_ONLY = types.SimpleNamespace(isna = lambda v: v is None or v != v)  # all of pandas composition_to_mass uses
LINKAGE = re.compile(r'\([abo?][\d?]?-[\d?][\d?|/,]*\)')
PANEL_SIZE = (4.6, 3.0)  # inches of a table row's box plot


def send(msg):
    REAL_OUT.write(json.dumps(msg) + '\n')
    REAL_OUT.flush()


def small(glycan, GlycoDraw):
    """[svg, sticker]: a die-cut sticker, which glycorender bakes into the SVG, or on glycowork before 1.10.1 the plain
    drawing, which the editor puts on a light chip."""
    try:
        return [GlycoDraw(glycan, compact = True, show_linkage = False, suppress = True, sticker = True).as_standalone_svg(), True]
    except TypeError:
        return [GlycoDraw(glycan, compact = True, show_linkage = False, suppress = True).as_svg(), False]


def full(glycan, GlycoDraw, **style):
    """{svg, sticker}: the hover's drawing with linkages, a die-cut sticker like the small one (glycowork 1.10.1+), else
    the plain drawing, which the page puts on white. style: GlycoDraw's options, such as highlight_motif."""
    try:
        return {'svg': GlycoDraw(glycan, suppress = True, sticker = True, **style).as_standalone_svg(), 'sticker': True}
    except TypeError:
        return {'svg': GlycoDraw(glycan, suppress = True, **style).as_svg(), 'sticker': False}


def group_plot(groups, palette, names = None):
    """{svg, width, dots}: a box per sample group with each sample as a point, for a table row's panel. viscose came with GlycoDraw
    (glycowork after its switch), and numpy with viscose, so this costs the process about 2 MB; an older glycowork raises
    ImportError here, and the panel then keeps its bars. PANEL_SIZE fits the panel at viscose's 72 px per inch. With viscose 0.2,
    which keeps each group's points and their places (`_spread`), dots maps every sample's point as repl_child.dot_map does,
    in page points from the top left, for the page's hover."""
    import numpy as np
    import viscose as vc
    fig, ax = vc.subplots(figsize = PANEL_SIZE, palette = palette)
    # viscose 0.2 draws every sample as a point over its box by itself, in the palette's manner; older ones get them here
    if hasattr(ax, '_spread'):
        ax.boxplot(groups, names = names)
    elif hasattr(ax, 'stripplot'):
        ax.boxplot(groups)
    else:
        ax.boxplot(groups, fliers = False)
        for i, values in enumerate(groups.values()):
            ax.scatter(i + np.linspace(-0.13, 0.13, len(values)) if len(values) > 1 else [i] * len(values), values, s = 16, color = 'ink', alpha = 0.55, z = 5)
    reply = {'svg': fig.to_svg(), 'width': fig.width}
    box = ax.artists[0]
    if hasattr(ax, '_spread') and box.get('names') is not None:
        offsets, dots = ax._spread(box)[0], []
        for i, ((group, _), x, values) in enumerate(zip(box['groups'], box['x'].tolist(), box['values'])):
            lefts, ups = ax.page(x + offsets[i], values)
            dots += [[round(left, 1), round(fig.height - up, 1), group, f'{v:.4g}', str(sample)]
                     for left, up, v, sample in zip(lefts.tolist(), ups.tolist(), values.tolist(), box['names'][i].tolist())]
        reply['dots'] = {'page': [float(fig.width), float(fig.height)], 'axes': [{'x': 'Group', 'y': 'Value', 'dots': dots}]}
    return reply


def draw(glycan, GlycoDraw):
    try:
        return small(glycan, GlycoDraw)
    except Exception:  # not a glycan after all, or one GlycoDraw cannot draw: the editor then shows nothing for it
        return None


class Tools:
    """glycowork's functions for checks, the hover card and motif search, imported on the first such request."""

    def __init__(self):
        from glycowork.motif import tokenization
        from glycowork.motif.graph import ensure_graph, glycan_to_nxGraph, subgraph_isomorphism
        from glycowork.motif.processing import canonicalize_iupac, get_class
        from glycowork.glycan_data.loader import lib
        from glycowork.motif.regex import compile_pattern, get_match
        self.lib = lib
        self.tokenization, self.ensure_graph, self.glycan_to_nxGraph, self.subgraph_isomorphism = tokenization, ensure_graph, glycan_to_nxGraph, subgraph_isomorphism
        self.canonicalize_iupac, self.get_class, self.compile_pattern, self.get_match = canonicalize_iupac, get_class, compile_pattern, get_match
        self._motifs = None

    def motifs(self):
        """[(name, compiled motif, termini)] from glycowork's motif list, as annotate_glycan builds it."""
        if self._motifs is None:
            with resources.files('glycowork.glycan_data').joinpath('glycan_motifs.csv').open(encoding = 'utf-8-sig') as f:
                rows = list(csv.DictReader(f))
            self._motifs = []
            for r in rows:
                termini = ast.literal_eval(r['termini_spec']) if r.get('termini_spec') else None
                try:
                    motif = self.compile_pattern(r['motif'][1:]) if r['motif'].startswith('r') else self.glycan_to_nxGraph(r['motif'], termini = 'provided', termini_list = termini)
                except Exception:
                    continue
                self._motifs.append((r['motif_name'], motif, termini))
        return self._motifs

    def count(self, graph, motif, termini):
        if isinstance(motif, list):  # a glyco-regex
            return len(self.get_match(motif, graph))
        return self.subgraph_isomorphism(graph, motif, termini_list = termini or [], count = True)

    def mass(self, glycan):
        """glycowork's monoisotopic mass. Its only use of pandas is pd.isna on a float, and pandas costs 560 ms and 43 MB."""
        t = self.tokenization
        if 'pandas' in sys.modules:
            return t.glycan_to_mass(glycan)
        real, t.pd = t.pd, NAN_ONLY
        try:
            return t.glycan_to_mass(glycan)
        except AttributeError:
            pass  # a glycowork that needs more of pandas here: the real one below
        finally:
            t.pd = real
        return t.glycan_to_mass(glycan)

    def check(self, glycan):
        try:
            return {'canonical': self.canonicalize_iupac(glycan)}
        except Exception as e:
            return {'error': f'{type(e).__name__}: {e}'[:300]}

    def info(self, glycan):
        out = self.check(glycan)
        if 'error' in out:
            return out
        g = out['canonical']
        for key, fn in (('mass', lambda: round(self.mass(g), 4)), ('composition', lambda: self.tokenization.glycan_to_composition(g)),
                        ('class', lambda: self.get_class(g))):
            try:
                out[key] = fn()
            except Exception:
                pass
        try:
            graph = self.ensure_graph(g, termini = 'calc')
            out['motifs'] = [name for name, motif, termini in self.motifs() if self.count(graph, motif, termini)]
        except Exception:
            pass
        return out

    def matcher(self, query):
        """count(glycan graph) for a motif name, a glycan substructure (with linkages, or one monosaccharide glycowork knows) or
        a glyco-regex, whose compiler rejects anything else."""
        named = [(m, t) for name, m, t in self.motifs() if name.lower() == query.lower()]
        if named:
            return lambda graph: self.count(graph, *named[0])
        if LINKAGE.search(query) or query in self.lib:
            motif = self.glycan_to_nxGraph(self.canonicalize_iupac(query) if LINKAGE.search(query) else query)
            return lambda graph: self.subgraph_isomorphism(graph, motif)
        if not re.fullmatch(r'[A-Za-z0-9?\-()[\]{}|*+,./]+', query):
            raise ValueError(f"{query!r} is neither a motif from glycowork's list, nor a glycan, nor a glyco-regex")
        pattern = self.compile_pattern(query)
        return lambda graph: len(self.get_match(pattern, graph))

    def match(self, query, glycans):
        try:
            test = self.matcher(query.strip())
        except Exception as e:
            return {'error': f'{type(e).__name__}: {e}'[:300]}
        found, end = [], time.time() + MATCH_LIMIT
        for g in glycans:
            if time.time() > end:
                return {'matches': found, 'partial': True}
            try:
                if test(self.ensure_graph(g, termini = 'calc')):
                    found.append(g)
            except Exception:
                continue  # not a glycan glycowork reads; the diagnostics say so where it matters
        return {'matches': found}


def answer(msg, GlycoDraw, tools):
    if isinstance(msg, list):
        return {glycan: draw(glycan, GlycoDraw) for glycan in msg}
    op = msg.get('op')
    if op in ('full', 'highlight'):
        try:
            return full(msg['glycan'], GlycoDraw, **({'highlight_motif': msg['motif']} if op == 'highlight' else {}))
        except Exception as e:
            return {'error': f'{type(e).__name__}: {e}'[:300]}
    if op == 'groups':
        try:
            return group_plot(msg['groups'], msg.get('palette'), msg.get('names'))
        except Exception as e:
            return {'error': f'{type(e).__name__}: {e}'[:300]}
    if op == 'check':
        return {g: tools().check(g) for g in msg['glycans']}
    if op == 'info':
        return tools().info(msg['glycan'])
    if op == 'match':
        return tools().match(msg['query'], msg['glycans'])
    if op == 'motifs':
        return {'motifs': [name for name, _, _ in tools().motifs()]}
    return {'error': f'unknown request {op}'}


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or '.') != here]  # slate's own modules are not the user's
    sys.path[:0] = sys.argv[1:]  # the open folders, so a glycowork checkout among them is the one that draws
    sys.stdout = sys.stderr
    warnings.filterwarnings('ignore')
    try:
        import glycowork
        from glycowork.motif.draw import GlycoDraw
    except Exception as e:
        send({'error': f'{type(e).__name__}: {e}'})
        return
    send({'ready': getattr(glycowork, '__version__', '?'), 'file': glycowork.__file__})
    made = []
    tools = lambda: made[0] if made else made.append(Tools()) or made[0]
    for line in sys.stdin:  # on the main thread: a read pending in a background thread makes DLL loading hang on Windows
        try:
            send(answer(json.loads(line), GlycoDraw, tools))
        except Exception as e:  # one bad request must not end the process the editor's drawings come from
            send({'error': f'{type(e).__name__}: {e}'[:300]})


if __name__ == '__main__':
    main()
