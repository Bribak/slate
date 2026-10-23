"""Session state (open folders, tabs, layout, console history) in %APPDATA%/slate/state.json."""
import json
import os
from pathlib import Path

STATE_FILE = Path(os.environ.get('APPDATA', Path.home())) / 'slate' / 'state.json'


def load():
    try:
        return json.loads(STATE_FILE.read_text(encoding = 'utf-8'))
    except (OSError, ValueError):
        return {}


def save(state):
    STATE_FILE.parent.mkdir(parents = True, exist_ok = True)
    tmp = STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, indent = 1), encoding = 'utf-8')
    tmp.replace(STATE_FILE)  # write-then-rename, so a crash mid-save cannot lose the session
