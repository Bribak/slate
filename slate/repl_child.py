"""The console's Python process. Reads one JSON line per command ({"code": ..., "id": n}) on stdin and writes JSON lines on stdout.

Output from print() arrives framed as {"o": text} / {"e": text}; anything written straight to the file descriptors
(subprocesses, C extensions) arrives unframed and the parent shows it as is. Images arrive as {"img": {mime, data, width,
height}}: matplotlib figures (through the module://slate_inline backend below) and last values that can draw themselves
the way Jupyter asks them to (_repr_png_, _repr_svg_, _repr_jpeg_), such as glycowork's GlycoDraw drawings.

Commands are read on the main thread, and only between runs: on Windows, a read left pending on the stdin pipe from
a background thread makes DLL loading hang (importing numpy deadlocks). Interrupts therefore come over a localhost
socket instead, whose port is announced at startup.
"""
import _thread
import ast
import base64
import builtins
import io
import json
import linecache
import os
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


def send_image(mime, data, width = None, height = None):
    """data: bytes, or SVG text; width and height in CSS pixels (PNG and JPEG only, from the image when not given)."""
    if isinstance(data, str):
        data = data.encode('utf-8')
    if mime == 'image/png' and width is None and data[12:16] == b'IHDR':
        width, height = struct.unpack('>II', data[16:24])
    encoded = base64.b64encode(data).decode('ascii')
    if len(encoded) > MAX_IMAGE:
        print(f'({mime} of {len(data) // 1_000_000} MB not shown)')
        return
    send({'img': {'mime': mime, 'data': encoded, 'width': width, 'height': height}})


def send_figure(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format = 'png', dpi = fig.dpi * 2, bbox_inches = 'tight')  # twice the pixels, shown at half: sharp on high-DPI screens
    data = buf.getvalue()
    width, height = struct.unpack('>II', data[16:24])
    send_image('image/png', data, width = width // 2, height = height // 2)


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


def show_rich(value):
    """Draw the value if it can show itself as an image (Jupyter's _repr_*_ methods, a matplotlib figure); False otherwise."""
    pyplot = sys.modules.get('matplotlib.pyplot')
    if hasattr(value, 'savefig') and hasattr(value, 'number') and pyplot is not None and value.number in pyplot.get_fignums():
        return True  # a pyplot figure: shown at the end of the run with the others
    if hasattr(value, 'savefig') and hasattr(value, 'dpi'):
        send_figure(value)
        return True
    for method, mime in (('_repr_svg_', 'image/svg+xml'), ('_repr_png_', 'image/png'), ('_repr_jpeg_', 'image/jpeg')):
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
        if data:
            send_image(mime, data, width = meta.get('width'), height = meta.get('height'))
            return True
    return False


def run(code, filename, ns):
    linecache.cache[filename] = (len(code), None, code.splitlines(True), filename)  # so tracebacks can show the console's own lines
    tree = ast.parse(code, filename, 'exec')
    last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
    exec(compile(tree, filename, 'exec'), ns)
    if last is not None:
        value = eval(compile(ast.Expression(last.value), filename, 'eval'), ns)
        if value is not None:
            ns['_'] = value
            if not show_rich(value):
                print(repr(value))


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
            run(msg['code'], f"<console-{msg['id']}>", ns)
        except SyntaxError:
            traceback.print_exception(*sys.exc_info()[:2], None)
        except SystemExit:
            send({'exit': True})
            return
        except BaseException as e:
            traceback.print_exception(type(e), e, user_frames(e.__traceback__))
        finally:
            try:
                show_figures()
            except Exception as e:
                print(f'(figures not shown: {type(e).__name__}: {e})', file = sys.stderr)
            busy.clear()
            send({'done': msg['id']})


if __name__ == '__main__':
    main()
