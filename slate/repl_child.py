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


def send_image(mime, data, width = None, height = None, figure = False):
    """data: bytes, or SVG text; width and height in CSS pixels (PNG and JPEG only, from the image when not given).
    figure: a matplotlib figure, which the page shows as a die-cut sticker like the glycan drawings."""
    if isinstance(data, str):
        data = data.encode('utf-8')
    if mime == 'image/png' and width is None and data[12:16] == b'IHDR':
        width, height = struct.unpack('>II', data[16:24])
    encoded = base64.b64encode(data).decode('ascii')
    if len(encoded) > MAX_IMAGE:
        print(f'({mime} of {len(data) // 1_000_000} MB not shown)')
        return
    send({'img': {'mime': mime, 'data': encoded, 'width': width, 'height': height, **({'figure': True} if figure else {})}})


def send_figure(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format = 'png', dpi = fig.dpi * 2, bbox_inches = 'tight')  # twice the pixels, shown at half: sharp on high-DPI screens
    data = buf.getvalue()
    width, height = struct.unpack('>II', data[16:24])
    send_image('image/png', data, width = width // 2, height = height // 2, figure = True)


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
    but for a filter's id, which differs between two such calls too. The user's own drawing is left as it was."""
    kind = type(value)
    if kind.__name__ != 'GlycanDrawing' or not kind.__module__.startswith('glycowork.') or not hasattr(value, 'sticker'):
        return None
    try:
        drawing = value
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
    symbol size (its height over the plain SVG's, times GlycoDraw's dim of 50). Text without a linkage, a Python without
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


def show_rich(value):
    """Show the value if it can show itself (Jupyter's _repr_*_ methods, in JupyterLab's order of preference, or a matplotlib
    figure); False otherwise. A GlycoDraw drawing shows as a die-cut sticker (glycan_sticker)."""
    sticker = glycan_sticker(value)
    if sticker is not None:
        send_image('image/svg+xml', sticker[0], width = sticker[1], height = sticker[2])
        return True
    pyplot = sys.modules.get('matplotlib.pyplot')
    if hasattr(value, 'savefig') and hasattr(value, 'number') and pyplot is not None and value.number in pyplot.get_fignums():
        return True  # a pyplot figure: shown at the end of the run with the others
    if hasattr(value, 'savefig') and hasattr(value, 'dpi'):
        send_figure(value)
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
            send_image(mime, data, width = meta.get('width'), height = meta.get('height'))
            return True
    return False


def display(*objs, **options):
    """Jupyter's display(): each object as it would show as a cell's last value, so a cell can show several. The options
    (include, metadata, display_id, ...) are accepted and ignored."""
    for obj in objs:
        if not show_rich(obj):
            print(repr(obj))


class IPythonDisplay:
    """Swaps display() above in for IPython's when IPython imports the module that defines it, since outside a kernel
    IPython's only prints each repr. A finder on sys.meta_path, so `from IPython.display import display` already gets it."""
    NAMES = ('IPython.core.display_functions', 'IPython.core.display')  # IPython 8 and later; before 8

    def find_spec(self, name, path, target = None):
        if name not in self.NAMES:
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path)
        if spec is not None and spec.loader is not None and hasattr(spec.loader, 'exec_module'):
            run_module = spec.loader.exec_module

            def exec_module(module):
                run_module(module)
                if callable(vars(module).get('display')):  # not getattr: IPython 8's IPython.core.display warns that its display is deprecated
                    module.display = display
            spec.loader.exec_module = exec_module
        return spec


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
    sys.meta_path.insert(0, IPythonDisplay())
    inline_backend()
    ns = {'__name__': '__main__', '__builtins__': builtins}
    busy = threading.Event()
    server = socket.create_server(('127.0.0.1', 0))
    threading.Thread(target = serve_interrupts, args = (server, busy), daemon = True).start()
    send({'port': server.getsockname()[1]})
    send({'ready': f'{sys.version.split()[0]} ({sys.executable})'})
    while True:
        try:
            line = commands.readline()
        except KeyboardInterrupt:
            continue  # an interrupt that lands just after a run finished
        if not line:
            return
        msg = json.loads(line)
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
