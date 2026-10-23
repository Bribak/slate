"""One slate window: a second launch (double-clicking a .py file) hands its paths to the running slate and exits."""
import json
import os
import secrets
import socket
import threading

from slate import state
from slate.system import native


def info_file():
    return state.STATE_FILE.parent / 'instance.json'  # looked up on each call, since the dev server moves STATE_FILE


def hand_off(paths):
    """True when a running slate took the paths, so this process can exit."""
    try:
        info = json.loads(info_file().read_text(encoding = 'utf-8'))
        native.allow_foreground(info['pid'])
        with socket.create_connection(('127.0.0.1', info['port']), timeout = 2) as conn:
            conn.sendall((json.dumps({'token': info['token'], 'open': paths}) + '\n').encode('utf-8'))
            return conn.recv(2) == b'ok'
    except (OSError, ValueError, KeyError):
        return False  # no slate running, or a stale file left by a crash


def serve(on_open):
    server = socket.create_server(('127.0.0.1', 0))
    token = secrets.token_hex(16)
    info_file().parent.mkdir(parents = True, exist_ok = True)
    info_file().write_text(json.dumps({'port': server.getsockname()[1], 'pid': os.getpid(), 'token': token}), encoding = 'utf-8')
    threading.Thread(target = accept, args = (server, token, on_open), daemon = True).start()


def accept(server, token, on_open):
    while True:
        conn, _ = server.accept()
        with conn:
            try:
                msg = json.loads(conn.makefile('r', encoding = 'utf-8').readline())
            except (OSError, ValueError):
                continue
            if not isinstance(msg, dict) or msg.get('token') != token:
                continue  # the token in the instance file keeps other local programs from driving the editor
            conn.sendall(b'ok')
        on_open(msg.get('open') or [])


def release():
    try:
        if json.loads(info_file().read_text(encoding = 'utf-8')).get('pid') == os.getpid():
            info_file().unlink()
    except (OSError, ValueError):
        pass
