"""The console's Python process. Reads one JSON line per command ({"code": ..., "id": n}) on stdin and writes JSON lines on stdout.

Output from print() arrives framed as {"o": text} / {"e": text}; anything written straight to the file descriptors
(subprocesses, C extensions) arrives unframed and the parent shows it as is.

Commands are read on the main thread, and only between runs: on Windows, a read left pending on the stdin pipe from
a background thread makes DLL loading hang (importing numpy deadlocks). Interrupts therefore come over a localhost
socket instead, whose port is announced at startup.
"""
import _thread
import ast
import builtins
import json
import linecache
import os
import socket
import sys
import threading
import traceback

REAL_OUT = sys.__stdout__
LOCK = threading.Lock()


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


def run(code, filename, ns):
    linecache.cache[filename] = (len(code), None, code.splitlines(True), filename)  # so tracebacks can show the console's own lines
    tree = ast.parse(code, filename, 'exec')
    last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
    exec(compile(tree, filename, 'exec'), ns)
    if last is not None:
        value = eval(compile(ast.Expression(last.value), filename, 'eval'), ns)
        if value is not None:
            ns['_'] = value
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
    sys.path[:0] = ['']
    sys.path += [p for p in sys.argv[1:] if p not in sys.path]
    commands = sys.stdin
    sys.stdin = open(os.devnull, encoding = 'utf-8')
    sys.stdout, sys.stderr = Framed('o'), Framed('e')
    builtins.input = no_input
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
            busy.clear()
            send({'done': msg['id']})


if __name__ == '__main__':
    main()
