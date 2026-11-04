"""The console's Python process. Reads one JSON line per command ({"code": ..., "id": n}, plus file, line, text and whole
for code run from a file in the editor: run_file) on stdin and writes JSON lines on stdout.

Output from print() arrives framed as {"o": text} / {"e": text}; anything written straight to the file descriptors
(subprocesses, C extensions) arrives unframed and the parent shows it as is. Images arrive as {"img": {mime, data, width,
height}}: matplotlib figures (through the module://slate_inline backend below) and last values that can draw themselves
the way Jupyter asks them to (_repr_png_, _repr_svg_, _repr_jpeg_), such as glycowork's GlycoDraw drawings. Markdown
(_repr_markdown_, e.g. IPython's Markdown with GlycoDraw's as_markdown() glycans in its text) arrives as {"md": text}.
display() works as in Jupyter: it is a builtin, and IPython's own is swapped for it when IPython is imported.

Commands are read on the main thread, and only between runs: on Windows, a read left pending on the stdin pipe from
a background thread makes DLL loading hang (importing numpy deadlocks). Interrupts therefore come over a localhost
socket instead, whose port is announced at startup.
"""
import _thread
import ast
import base64
import builtins
import copy
import importlib.machinery
import importlib.util
import io
import itertools
import json
import linecache
import os
import re
import socket
import struct
import sys
import threading
import traceback
import types

REAL_OUT = sys.__stdout__
LOCK = threading.Lock()
BACKEND = 'module://slate_inline'
MAX_IMAGE = 8_000_000  # bytes of base64 per image; a bigger one is described instead of shown
MAX_SVG_MARKS, MAX_SVG = 2000, 700_000  # points and bars, and characters, of a viscose figure shown as SVG; beyond them, as PNG
MAX_DOTS = 5000  # points of a viscose figure the page can name under the mouse; beyond it, no hovering
MAX_CELLS = 500_000  # cells of one heatmap the page can look up under the mouse, 2 MB of floats; beyond it, no hovering
SVG_FIGURE = None  # the viscose Figure that last wrote its SVG, for the dot map when annotate_figure's result comes back to svg_figure
MAX_KEPT = 40  # figures kept for saving, as many as the page's MAX_IMAGES
KEPT_FIGURES, FIGURE_IDS = {}, itertools.count(1)  # {id: viscose Figure or SVG text}, oldest first
REDRAWS = {}  # {id: what redraw_figure needs}, for the viscose figures among them
REPLACING = None  # the id of the figure redraw_figure is drawing again, which the new image replaces
ANNOTATION = None  # what annotate_figure last did, for svg_figure: (the function, its other arguments, its keywords)
FIGURE_PALETTE = os.environ.get('SLATE_FIGURE_PALETTE')  # the settings' palette for viscose figures; a set_palette in the code wins afterwards


def send(msg):
    with LOCK:
        REAL_OUT.write(json.dumps(msg) + '\n')
        REAL_OUT.flush()


class Framed:
    encoding = 'utf-8'
    errors = 'replace'

    def __init__(self, key):
        self.key = key

    def write(self, text):
        if text:
            send({self.key: text})
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False

    def fileno(self):
        return (1 if self.key == 'o' else 2)


def no_input(prompt = ''):
    raise RuntimeError('input() is not supported in the slate console')


def send_image(mime, data, width = None, height = None, figure = False, source = None, dots = None, redraw = None):
    """data: bytes, or SVG text; width and height in CSS pixels (PNG and JPEG only, from the image when not given).
    figure: a plot from matplotlib or viscose, which the page shows as a die-cut sticker like the glycan drawings. Its PNG
    has twice the pixels and is shown at half, sharp on high-DPI screens. source: what save_figure can write this image
    from, a viscose Figure or the SVG, which the page's menu then offers to save. dots: dot_map's, for hovering. redraw: (the
    viscose Figure, what annotate_figure was given or None) for redraw_figure, which the page then offers in other palettes."""
    if isinstance(data, str):
        data = data.encode('utf-8')
    if mime == 'image/png' and width is None and data[12:16] == b'IHDR':
        width, height = (n // 2 if figure else n for n in struct.unpack('>II', data[16:24]))
    encoded = base64.b64encode(data).decode('ascii')
    if len(encoded) > MAX_IMAGE:
        print(f'({mime} of {len(data) // 1_000_000} MB not shown)')
        return
    extra = {'figure': True} if figure else {}
    if dots:
        extra['dots'] = dots
    if source is not None:
        extra['saves'] = f'{os.getpid()}-{next(FIGURE_IDS)}'  # with the process, as the page keeps its images across a console restart
        KEPT_FIGURES[extra['saves']] = source
        if redraw is not None and hasattr(redraw[0], 'in_palette'):
            REDRAWS[extra['saves']] = redraw
            extra['palette'] = redraw[0].pal.name
        if REPLACING:
            extra['replaces'] = REPLACING
        while len(KEPT_FIGURES) > MAX_KEPT:
            oldest = next(iter(KEPT_FIGURES))
            del KEPT_FIGURES[oldest]
            REDRAWS.pop(oldest, None)
    send({'img': {'mime': mime, 'data': encoded, 'width': width, 'height': height, **extra}})


def send_figure(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format = 'png', dpi = fig.dpi * 2, bbox_inches = 'tight')
    send_image('image/png', buf.getvalue(), figure = True)


def dot_map(fig):
    """Where each point of a laid-out viscose Figure landed, so that the page can name the dot under the mouse: {'page':
    [width, height], 'axes': [{'x', 'y': axis names, 'dots': [[left, top, x, y, name]]}]}, positions in page points from
    the top left, values as text. name, only where there is one, is the point's in scatter(names = ...), else text annotated
    at exactly that point, which on a glycowork volcano plot before it passes names is a significant dot's glycan. Points
    outside the limits are left out. A heatmap's cells come as 'cells', see cell_map. None without either, over MAX_DOTS,
    or if another viscose's internals differ."""
    try:
        np = sys.modules['numpy']  # viscose needs it
        if sum(np.size(a['x']) for ax in fig.axes for a in ax.artists if a['kind'] == 'points') > MAX_DOTS:
            return None
        cells = cell_map(fig, np)
        axes = []
        for ax in fig.axes:
            names = {(a['x'], a['y']): a['s'] for a in ax.artists if a['kind'] == 'text'}
            x0, y0, w, h = ax.rect
            dots = []
            for a in ax.artists:
                if a['kind'] == 'points':
                    lefts, ups = ax.page(a['x'], a['y'])
                    own = a.get('names')
                    own = own.tolist() if own is not None else [None] * len(lefts)
                    for x, y, left, up, mine in zip(a['x'].tolist(), a['y'].tolist(), lefts.tolist(), ups.tolist(), own):
                        if x0 <= left <= x0 + w and y0 <= up <= y0 + h:  # NaN compares false
                            name = names.get((x, y)) if mine is None else str(mine)
                            dots.append([round(left, 1), round(fig.height - up, 1), f'{x:.4g}', f'{y:.4g}'] + ([name] if name else []))
            if dots:
                axes.append({'x': ax.xlabel, 'y': ax.ylabel, 'dots': dots})
        if not axes and not cells:
            return None
        return {'page': [fig.width, fig.height], 'axes': axes, **({'cells': cells} if cells else {})}
    except Exception:
        return None


def cell_map(fig, np):
    """Each heatmap of a laid-out viscose Figure, as a grid the page can look a cell up in without an entry per cell: 'box',
    the axes as [left, top, right, bottom], and 'origin' and 'step', the first cell's corner and how far on each next column
    and row goes, in page points from the top left (a step is negative under inverted limits); 'rows' and 'columns', names as
    drawn, from heatmap's names or, in a viscose before those, the tick labels ('' where it has none); the axis names; and
    'values', the rows of cells as little-endian 32-bit floats in base64, NaN for an empty cell, up to MAX_CELLS. 'nth', only
    where a name repeats: for each row, how many rows of that name come before it in the data, so that a click finds its own."""
    maps = []
    for ax in fig.axes:
        for a in ax.artists:
            if a['kind'] != 'heat' or a['m'].size > MAX_CELLS:
                continue
            ny, nx = a['m'].shape
            xs, ys = ax.page(np.array([0.0, 1.0]), np.array([float(ny), ny - 1.0]))
            rows, columns = a.get('rows'), a.get('columns')
            if rows is None and getattr(ax, '_ytl', None) and len(ax._ytl) == ny:
                rows = ax._ytl[::-1]
            if columns is None and getattr(ax, '_xtl', None) and len(ax._xtl) == nx:
                columns = ax._xtl
            rows = [str(v).strip() for v in rows] if rows is not None else [''] * ny
            bar = next((b._cbar['label'] for b in fig.axes if b._cbar and b._cbar['source'] is ax), '')
            left, up, w, h = ax.rect
            found = {'box': [round(float(v), 2) for v in (left, fig.height - up - h, left + w, fig.height - up)],
                     'origin': [round(float(xs[0]), 3), round(float(fig.height - ys[0]), 3)],
                     'step': [float(xs[1] - xs[0]), float(ys[0] - ys[1])],
                     'rows': rows, 'columns': [str(v) for v in columns] if columns is not None else [''] * nx,
                     'x': ax.xlabel, 'y': bar,
                     'values': base64.b64encode(a['m'].astype('<f4').tobytes()).decode('ascii')}
            if any(rows) and len(set(rows)) < ny:
                order = getattr(fig, 'row_order', None)
                order = order if order is not None and len(order) == ny else list(range(ny))
                seen, found['nth'] = {}, [0] * ny
                for r in sorted(range(ny), key = lambda r: order[r]):
                    found['nth'][r] = seen.get(rows[r], 0)
                    seen[rows[r]] = found['nth'][r] + 1
            maps.append(found)
    return maps


def send_viscose(fig):
    """A viscose Figure as SVG, which stays sharp on any screen and takes a tenth of the PNG's time in Couture and Poster and
    under half in Lumen and Aura. It costs the page about 1 MB per figure, which a large one would multiply, as each path
    stays parsed for as long as the image is kept. So a figure with more than MAX_SVG_MARKS points or bars, whose every mark
    is a path but which viscose stamps into one image in a PNG, and an SVG over MAX_SVG, such as a heatmap's many glycans as
    outlines, go as a PNG. So does a figure viscose cannot write as SVG. A heatmap's SVG is measured without its embedded
    images: in Lumen and Aura its soft shadow alone is 1-2 MB of base64, with no paths to keep parsed, and writing the PNG
    too would add twice the time."""
    global SVG_FIGURE
    svg = ''
    drawn = 0
    try:
        np = sys.modules['numpy']  # viscose needs it
        if sum(np.size(a['x']) for ax in fig.axes for a in ax.artists if 'x' in a) <= MAX_SVG_MARKS:
            svg = fig.to_svg()
            SVG_FIGURE = None  # shown right here, not on its way through annotate_figure
            drawn = len(re.sub(r'base64,[^"]*', '', svg)) if any(a['kind'] == 'heat' for ax in fig.axes for a in ax.artists) else len(svg)
    except Exception:
        pass  # another viscose's internals
    if svg and drawn <= MAX_SVG:
        send_image('image/svg+xml', svg, figure = True, source = fig, dots = dot_map(fig), redraw = (fig, None))
    else:
        png = fig.to_png(144.0)
        send_image('image/png', png, figure = True, source = fig, dots = dot_map(fig), redraw = (fig, None))


def save_figure(figure, kind, path):
    """Writes a figure the console showed, by the id send_image gave it, as pdf, svg or png, on the command's own turn between
    runs. A viscose Figure as it stands now, through its own writers; an SVG, such as glycowork's annotated volcano, through
    what annotate_figure saves with, from the glycorender glycowork brought along. PNG at 300 dpi, as both do by default."""
    source = KEPT_FIGURES.get(figure)
    if source is None:
        send({'sys': f'That figure is no longer kept: the console keeps the newest {MAX_KEPT}'})
        return
    path = path if path.lower().endswith(f'.{kind}') else f'{path}.{kind}'
    if not isinstance(source, str):
        source.save(path)
    elif kind == 'svg':
        with open(path, 'w', encoding = 'utf-8', newline = '\n') as f:
            f.write(source)
    else:
        from glycorender.render import simple_svg_to_pdf, simple_svg_to_png
        (simple_svg_to_pdf if kind == 'pdf' else simple_svg_to_png)(source, path)
    send({'saved': path})


def show_figures(*args, **kwargs):
    """plt.show() for the slate_inline backend, and the end of every run: each open pyplot figure goes to the console and is
    closed, as in Jupyter, so a figure is never shown twice and closed ones free their memory."""
    pyplot = sys.modules.get('matplotlib.pyplot')
    if pyplot is None or pyplot.get_backend() != BACKEND:
        return
    for num in pyplot.get_fignums():
        send_figure(pyplot.figure(num))
    pyplot.close('all')


def inline_backend():
    """The matplotlib backend MPLBACKEND names, built here so this file stays stdlib-only: matplotlib's own Agg canvas is
    looked up only when matplotlib loads the backend, which happens only if the code imports pyplot."""
    module = types.ModuleType('slate_inline')

    def lookup(name):
        if name != 'FigureCanvas':
            raise AttributeError(name)  # matplotlib fills in what a backend leaves out
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        module.FigureCanvas = FigureCanvasAgg  # stored, since pyplot next copies the module's attributes (vars) into its backend class
        del module.__getattr__
        return FigureCanvasAgg
    module.__getattr__ = lookup
    module.show = show_figures
    sys.modules['slate_inline'] = module


def glycan_sticker(value):
    """(svg, width, height) for a GlycoDraw drawing (glycowork's GlycanDrawing, 1.10.1+) as a die-cut sticker, else None.
    Its _repr_png_ draws on white, which sat as a white box on the console's dark background, and glycan drawings on a dark
    page are stickers in slate. A plain drawing's canvas is widened by the difference between GlycoDraw's margins for a
    sticker and for a plain or shadowed drawing (0.45, 0.2 and 0.35 of dim), since the cut and its shadow need that room:
    switched to sticker without it, they were clipped flat at the edges. The result equals GlycoDraw(..., sticker = True)
    but for a filter's id, which differs between two such calls too. The user's own drawing is left as it was. glycowork's
    own as_sticker() does this where it exists (proposed for 1.11.1: glycowork/.claude/changes/2026-10-31-stickers-on-dark.md)."""
    kind = type(value)
    if kind.__name__ != 'GlycanDrawing' or not kind.__module__.startswith('glycowork.') or not hasattr(value, 'sticker'):
        return None
    try:
        drawing = value.as_sticker() if hasattr(value, 'as_sticker') else value
        if not drawing.sticker:
            canvas = copy.copy(drawing.drawing_obj)  # a drawsvg Drawing; its elements are shared, not changed
            extra = drawing.dim * (0.45 - (0.35 if drawing.shadow else 0.2))
            x, y, w, h = canvas.view_box
            canvas.view_box = (x - extra, y - extra, w + 2 * extra, h + 2 * extra)
            canvas.width, canvas.height = canvas.width + 2 * extra, canvas.height + 2 * extra
            drawing = copy.copy(drawing)
            drawing.drawing_obj, drawing.sticker, drawing.shadow = canvas, True, False
        return drawing.as_standalone_svg(), round(drawing.drawing_obj.width), round(drawing.drawing_obj.height)
    except Exception:
        return None  # another glycowork's internals: shown as glycowork shows it


GLYCAN_TEXT = re.compile(r'[A-Za-z[{](?:[A-Za-z0-9?\-.|/[\]{}]|\([^()\s]*\))*')  # glycans.IN_TEXT: no spaces, commas only inside a linkage
LINKAGE = re.compile(r'\([abo?][\d?]?-[\d?][\d?|/,]*\)')  # glycans.LINKAGE: what makes a word a glycan
AS_IT_IS = re.compile(r'```.*?```|`[^`\n]*`|<img\s[^>]*>', re.S)  # code, and drawings already in the text
PLAIN_DRAWING = re.compile(r'<img src="data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)" alt="SNFG diagram of (\S+) drawn in (horizontal|vertical) '
                           r'(standard|compact) style (with|without) linkage labels\." height="(\d+)"[^>]*>')  # GlycoDraw's as_markdown(), its default alt text
MAX_DRAWN = 50  # distinct glycans drawn per Markdown text


def glycodraw():
    """GlycoDraw from this Python's glycowork, imported if it is installed but not yet imported (once, about 1.3 s), else None."""
    module = sys.modules.get('glycowork.motif.draw')
    if module is None:
        try:
            if importlib.util.find_spec('glycowork') is None:
                return None
            import glycowork.motif.draw as module
        except Exception:
            return None
    return getattr(module, 'GlycoDraw', None)


def markdown_stickers(md):
    """Markdown with its glycans as GlycoDraw's die-cut stickers, as drawings sit on slate's dark background: a glycan written
    as text (the editor's rule: at least one linkage, no spaces, not in code) becomes its sticker, as with as_markdown(),
    which keeps the string as the drawing's alt text, and a plain
    as_markdown() drawing is redrawn as one from its alt text, which names the glycan and how it was drawn, at the same
    symbol size (its height over the plain SVG's, times GlycoDraw's dim of 50); with the GLYCOWORK_DARK_DISPLAY that repl.py
    sets, a glycowork with as_sticker() makes as_markdown() stickers itself. Text without a linkage, a Python without
    glycowork (1.11.1+, for as_markdown) or anything that fails leaves the Markdown as it is."""
    if not LINKAGE.search(md):
        return md
    GlycoDraw = glycodraw()
    if GlycoDraw is None:
        return md
    drawn = {}

    def sticker(glycan, size = 14, **style):
        key = (glycan, size, tuple(sorted(style.items())))
        if key not in drawn and len(drawn) < MAX_DRAWN:
            try:
                drawn[key] = GlycoDraw(glycan, sticker = True, suppress = True, **style).as_markdown(size = size)
            except Exception:
                drawn[key] = None  # not a glycan GlycoDraw reads, or a glycowork without stickers or as_markdown
        return drawn.get(key)

    def text(m):
        glycan = m.group().rstrip('.,;:')  # the end of a sentence
        if not LINKAGE.search(glycan) or glycan.count('(') != glycan.count(')') or glycan.count('[') != glycan.count(']'):
            return m.group()
        drawing = sticker(glycan)
        return m.group() if drawing is None else drawing + m.group()[len(glycan):]

    def as_it_is(m):
        plain = PLAIN_DRAWING.fullmatch(m.group())
        if plain is None:
            return m.group()
        svg = base64.b64decode(plain.group(1)).decode('utf-8', 'replace')
        height = re.search(r'<svg[^>]*?\sheight="([\d.]+)', svg)
        if 'feDropShadow' in svg or height is None:
            return m.group()  # already a sticker
        size = int(plain.group(6)) * 50 / float(height.group(1))
        drawing = sticker(plain.group(2), size = size, vertical = plain.group(3) == 'vertical', compact = plain.group(4) == 'compact',
                          show_linkage = plain.group(5) == 'with')
        return drawing or m.group()

    try:
        out, last = [], 0
        for m in AS_IT_IS.finditer(md):
            out += [GLYCAN_TEXT.sub(text, md[last:m.start()]), as_it_is(m)]
            last = m.end()
        return ''.join(out) + GLYCAN_TEXT.sub(text, md[last:])
    except Exception:
        return md


MAX_ROWS, MAX_COLUMNS = 20, 12  # of a DataFrame shown as a table


def dataframe_markdown(value):
    """A pandas DataFrame (glycowork's results among them) as a Markdown table, which the console shows with the glycans in
    its cells drawn (markdown_stickers), rather than pandas' 80-column text, which hid all but the first and last column of
    a get_differential_expression result behind '...'. The first MAX_ROWS rows and MAX_COLUMNS columns, floats to 4
    significant digits; the index only when it holds labels (strings). None for anything else, or if anything fails."""
    pd = sys.modules.get('pandas')
    if pd is None or not isinstance(value, pd.DataFrame):
        return None
    try:
        frame = value.iloc[:MAX_ROWS, :MAX_COLUMNS]
        labels = frame.index.dtype == object

        def cell(v):
            if v is None or (isinstance(v, float) and v != v):
                return ''
            return (f'{v:.4g}' if isinstance(v, float) else str(v)).replace('|', '\\|').replace('\n', ' ')
        head = ([cell(value.index.name or '')] if labels else []) + [cell(c) for c in frame.columns]
        rows = [([cell(i)] if labels else []) + [cell(v) for v in row] for i, row in zip(frame.index, frame.itertuples(index = False))]
        lines = ['| ' + ' | '.join(head) + ' |', '|' + '---|' * len(head)] + ['| ' + ' | '.join(r) + ' |' for r in rows]
        hidden = [f'{n} more {what}' for n, what in ((len(value) - len(frame), 'rows'), (value.shape[1] - frame.shape[1], 'columns')) if n]
        return '\n'.join(lines) + (f'\n\n*{value.shape[0]} rows × {value.shape[1]} columns; {" and ".join(hidden)} not shown*' if hidden else '')
    except Exception:
        return None


def show_rich(value):
    """Show the value if it can show itself (Jupyter's _repr_*_ methods, in JupyterLab's order of preference, or a matplotlib
    figure); False otherwise. A GlycoDraw drawing shows as a die-cut sticker (glycan_sticker). What viscose shows, its Figure
    or what viscose.show_png displays, is a figure, known by its type's module, as matplotlib's are by savefig."""
    sticker = glycan_sticker(value)
    if sticker is not None:
        send_image('image/svg+xml', sticker[0], width = sticker[1], height = sticker[2])
        return True
    if type(value).__module__ == 'viscose.figure' and hasattr(value, 'to_svg'):
        send_viscose(value)
        return True
    pyplot = sys.modules.get('matplotlib.pyplot')
    if hasattr(value, 'savefig') and hasattr(value, 'number') and pyplot is not None and value.number in pyplot.get_fignums():
        return True  # a pyplot figure: shown at the end of the run with the others
    if hasattr(value, 'savefig') and hasattr(value, 'dpi'):
        send_figure(value)
        return True
    table = dataframe_markdown(value)
    if table is not None:
        send({'md': markdown_stickers(table)})
        return True
    for method, mime in (('_repr_markdown_', 'text/markdown'), ('_repr_svg_', 'image/svg+xml'), ('_repr_png_', 'image/png'), ('_repr_jpeg_', 'image/jpeg')):
        fn = getattr(type(value), method, None)  # looked up on the type, as IPython does, so objects answering any attribute (mocks, some proxies) are not taken for images
        if not callable(fn):
            continue
        try:
            data = fn(value)
        except Exception as e:
            print(f'({type(value).__name__}.{method} failed: {type(e).__name__}: {e})', file = sys.stderr)
            continue
        meta = {}
        if isinstance(data, tuple):  # (data, {'width': ..., 'height': ...}), as GlycoDraw returns
            data, meta = data[0], data[1] if len(data) > 1 and isinstance(data[1], dict) else {}
        if data and mime == 'text/markdown':
            if len(data) > MAX_IMAGE:
                print(f'(Markdown of {len(data) // 1_000_000} MB not shown)')
            else:
                send({'md': markdown_stickers(data)})
            return True
        if data:
            send_image(mime, data, width = meta.get('width'), height = meta.get('height'), figure = str(type(value).__module__).startswith('viscose.'))
            return True
    return False


def display(*objs, **options):
    """Jupyter's display(): each object as it would show as a cell's last value, so a cell can show several. The options
    (include, metadata, display_id, ...) are accepted and ignored."""
    for obj in objs:
        if not show_rich(obj):
            print(repr(obj))


def svg_figure(svg_data, chem = False, shadow = False, sticker = False):
    """glycowork's display_svg_with_matplotlib in the console (DisplaySwaps): the SVG as it is, which the page shows crisp,
    rather than rendered through glycorender into a matplotlib figure. That drew neither matplotlib's text nor the glycans
    annotate_figure puts on the dots of get_volcano and plot_network, so the volcano came out blank (glycowork 1.11.0 and
    1.11.1). A matplotlib or viscose figure gets the figure sticker: matplotlib names itself in its header, viscose in a
    metadata element at the top of its SVG, which annotate_figure leaves in place."""
    global SVG_FIGURE, ANNOTATION
    svg = svg_data if isinstance(svg_data, str) else svg_data.as_svg()
    head = svg[:3000]
    figure = 'matplotlib' in head or '<metadata>viscose' in head
    fig, SVG_FIGURE = SVG_FIGURE, None
    annotation, ANNOTATION = ANNOTATION, None
    dots = redraw = None
    size = re.search(r'<svg[^>]*?\swidth="([\d.]+)[^>]*?\sheight="([\d.]+)', head)
    if fig is not None and '<metadata>viscose' in head and size and abs(float(size.group(2)) - fig.height) < 0.5:  # the figure this SVG came from, as far as one can tell
        dots = dot_map(fig)
        if dots:
            dots['page'] = [float(size.group(1)), float(size.group(2))]
        redraw = (fig, annotation)
    send_image('image/svg+xml', svg, figure = figure, source = svg if figure else None, dots = dots, redraw = redraw)


def swap_function(attr, ours):
    def swap(module):
        if callable(vars(module).get(attr)):  # not getattr: IPython 8's IPython.core.display warns that its display is deprecated
            setattr(module, attr, ours)
    return swap


def swap_glycowork_draw(module):
    """svg_figure for display_svg_with_matplotlib, and annotate_figure noted, as drawing a figure in another palette means
    annotating it again, with the arguments only get_volcano knew."""
    swap_function('display_svg_with_matplotlib', svg_figure)(module)
    annotate = vars(module).get('annotate_figure')
    if callable(annotate):
        def noted(svg_input, *args, **kwargs):
            global ANNOTATION
            result = annotate(svg_input, *args, **kwargs)
            ANNOTATION = (noted, args, kwargs)
            return result
        module.annotate_figure = noted


def redraw_figure(figure, palette):
    """A viscose figure the console showed, by the id send_image gave it, drawn again in another palette (Figure.in_palette,
    viscose 0.1.2) and annotated again where it was. Its image replaces the old one in the page, which keeps the figure's
    place, and no code runs again."""
    global REPLACING
    kept = REDRAWS.get(figure)
    if kept is None:
        send({'sys': f'That figure is no longer kept: the console keeps the newest {MAX_KEPT}'})
        return
    fig, annotation = kept
    moved = fig.in_palette(palette)
    REPLACING = figure
    try:
        if annotation is None:
            send_viscose(moved)
        else:
            annotate, args, kwargs = annotation
            svg_figure(annotate(moved.to_svg(), *args, **{**kwargs, 'filepath': ''}))
    finally:
        REPLACING = None
    KEPT_FIGURES.pop(figure, None)
    REDRAWS.pop(figure, None)


def swap_figure_show(module):
    """fig.show(), which glycowork's plots end with, makes a PNG first and shows that (viscose's show_png). to_svg notes its
    figure, as annotate_figure only hands on text and svg_figure needs the figure for its dot map."""
    to_svg = module.Figure.to_svg

    def noted(self):
        global SVG_FIGURE
        SVG_FIGURE = self
        return to_svg(self)
    module.Figure.show = lambda self, dpi = 144.0, viewer = True: send_viscose(self)
    module.Figure.to_svg = noted


def choose_palette(module):
    if FIGURE_PALETTE:
        module.set_palette(FIGURE_PALETTE)


class DisplaySwaps:
    """Changes a module as it is imported: display() above for IPython's, since outside a kernel IPython's only prints each
    repr, svg_figure for glycowork's display_svg_with_matplotlib, send_viscose for fig.show(), and the settings' palette for
    viscose's default. A finder on sys.meta_path, so `from IPython.display import display` already gets it, and glycowork's
    own calls (it imports the function where it calls it, or calls it as a global of draw.py) find the swapped one."""
    SWAPS = {'IPython.core.display_functions': swap_function('display', display), 'IPython.core.display': swap_function('display', display),  # IPython 8 and later; before 8
             'glycowork.motif.draw': swap_glycowork_draw, 'viscose.figure': swap_figure_show, 'viscose.palettes': choose_palette}

    def find_spec(self, name, path, target = None):
        if name not in self.SWAPS:
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path)
        if spec is not None and spec.loader is not None and hasattr(spec.loader, 'exec_module'):
            run_module, swap = spec.loader.exec_module, self.SWAPS[name]

            def exec_module(module):
                run_module(module)
                swap(module)
            spec.loader.exec_module = exec_module
        return spec


def command(msg):
    """What the page asks between runs, which is not code to run: {'palette'} changes the settings' palette in a running
    console, as a restart would lose its variables, {'save'} writes a figure, and {'redraw', 'palette'} draws one again.
    True if msg was one."""
    global FIGURE_PALETTE
    try:
        if 'redraw' in msg:
            redraw_figure(msg['redraw'], msg['palette'])
        elif 'palette' in msg:
            FIGURE_PALETTE = msg['palette']
            if 'viscose.palettes' in sys.modules:
                sys.modules['viscose.palettes'].set_palette(FIGURE_PALETTE)
        elif 'save' in msg:
            save_figure(msg['save'], msg['kind'], msg['path'])
        else:
            return False
    except Exception as e:
        print(f'({type(e).__name__}: {e})', file = sys.stderr)
    return True


def run(code, filename, ns, first_line = 1):
    tree = ast.parse('\n' * (first_line - 1) + code, filename, 'exec')  # padded, so line numbers (SyntaxErrors' too) are the file's
    last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
    exec(compile(tree, filename, 'exec'), ns)
    if last is not None:
        value = eval(compile(ast.Expression(last.value), filename, 'eval'), ns)
        if value is not None:
            ns['_'] = value
            if not show_rich(value):
                print(repr(value))


def run_file(msg, ns):
    """Code from a file in the editor: a cell or selection starting at msg['line'], or with msg['whole'] the file as a script.
    As in Spyder and VS Code's interactive window, the working directory becomes the file's folder, which also goes on
    sys.path for its sibling modules. A script starts with a namespace of its own, as `python file.py` would, and its
    variables are copied into the console's afterwards, as IPython's %run does."""
    path = msg['file']
    folder = os.path.dirname(path)
    os.chdir(folder)
    if folder not in sys.path:
        sys.path.insert(1, folder)  # after '', the working directory
    text = msg.get('text', msg['code'])
    linecache.cache[path] = (len(text), None, text.splitlines(True), path)  # tracebacks quote the editor's text, saved or not
    if not msg.get('whole'):
        ns['__file__'] = path
        return run(msg['code'], path, ns, first_line = msg.get('line', 1))
    script = {'__name__': '__main__', '__file__': path, '__builtins__': builtins}
    argv, sys.argv = sys.argv, [path]
    try:
        exec(compile(msg['code'], path, 'exec'), script)
    finally:
        sys.argv = argv
        ns.update((k, v) for k, v in script.items() if not k.startswith('__'))  # also after an exception, to look at what went wrong


def serve_interrupts(server, busy):
    while True:
        conn, _ = server.accept()
        with conn:
            while conn.recv(1):
                if busy.is_set():
                    _thread.interrupt_main()


def user_frames(tb):
    while tb is not None and tb.tb_frame.f_code.co_filename == __file__:
        tb = tb.tb_next  # drop main() and run(), the console's own frames
    return tb


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or '.') != here]  # slate's own modules (style, state, usage...) must not shadow the user's
    sys.path[:0] = ['']
    sys.path += [p for p in sys.argv[1:] if p not in sys.path]
    commands = sys.stdin
    sys.stdin = open(os.devnull, encoding = 'utf-8')
    sys.stdout, sys.stderr = Framed('o'), Framed('e')
    builtins.input = no_input
    builtins.display = display  # as in Jupyter, without an import
    sys.meta_path.insert(0, DisplaySwaps())
    inline_backend()
    ns = {'__name__': '__main__', '__builtins__': builtins}
    busy = threading.Event()
    server = socket.create_server(('127.0.0.1', 0))
    threading.Thread(target = serve_interrupts, args = (server, busy), daemon = True).start()
    send({'port': server.getsockname()[1], 'pid': os.getpid()})
    send({'ready': f'{sys.version.split()[0]} ({sys.executable})'})
    while True:
        try:
            line = commands.readline()
        except KeyboardInterrupt:
            continue  # an interrupt that lands just after a run finished
        if not line:
            return
        msg = json.loads(line)
        if command(msg):
            continue
        busy.set()
        try:
            if msg.get('file'):
                run_file(msg, ns)
            else:
                name = f"<console-{msg['id']}>"
                linecache.cache[name] = (len(msg['code']), None, msg['code'].splitlines(True), name)  # so tracebacks can show the console's own lines
                run(msg['code'], name, ns)
        except SyntaxError:
            traceback.print_exception(*sys.exc_info()[:2], None)
        except SystemExit:
            send({'exit': True})
            return
        except BaseException as e:
            traceback.print_exception(type(e), e, user_frames(e.__traceback__))
        finally:
            if msg.get('file'):
                linecache.cache.pop(msg['file'], None)  # a module of the same file imported later quotes its saved text
            try:
                show_figures()
            except Exception as e:
                print(f'(figures not shown: {type(e).__name__}: {e})', file = sys.stderr)
            busy.clear()
            send({'done': msg['id']})


if __name__ == '__main__':
    main()
