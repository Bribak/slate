"""Session state (open folders, tabs, layout, console history) in state.json: %APPDATA%\\slate, or ~/Library/Application Support/slate."""
import json
import threading

from slate.system import data_dir

STATE_FILE = data_dir() / 'state.json'
_lock = threading.Lock()  # the page's saves and the window's closing save arrive on different threads, and share one .tmp file


def load():
    try:
        return json.loads(STATE_FILE.read_text(encoding = 'utf-8'))
    except (OSError, ValueError):
        return {}


def save(state):
    with _lock:
        STATE_FILE.parent.mkdir(parents = True, exist_ok = True)
        tmp = STATE_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(state, indent = 1), encoding = 'utf-8')
        tmp.replace(STATE_FILE)  # write-then-rename, so a crash mid-save cannot lose the session
