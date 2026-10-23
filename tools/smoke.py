"""Check that slate works in a real window, on Windows and macOS: the page, the bridge, linting, the console, running a
cell, a traceback link, the settings panel, a key, and a second launch handing over a file.

python tools/smoke.py   prints one JSON line of results and exits 1 if any check failed

Runs an isolated session (a temporary folder with its own state.json and a small project), so the real session is
never touched. .github/workflows/smoke.yml runs it on GitHub's macOS and Windows machines, where nobody can watch the
window; it is how the macOS side is checked from a Windows desk. Each probe waits at most 60 s for the page.
"""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slate import state

HOME = Path(tempfile.mkdtemp(prefix = 'slate-smoke-')).resolve()  # the long path, as slate opens files: TEMP can be a short 8.3 one (RUNNER~1)
state.STATE_FILE = HOME / 'state.json'
PROJECT = HOME / 'project'
PROJECT.mkdir()
DEMO = PROJECT / 'demo.py'
DEMO.write_text('# %% first\nimport os\nvalue = 6 * 7\nprint("smoke", value, os.path.basename(os.getcwd()))\n\n# %% second\npattern = "\\d"\n1 / 0\n',
                encoding = 'utf-8')  # "\d" is a SyntaxWarning, which slate marks without any check_style.py
OTHER = PROJECT / 'other.py'
OTHER.write_text('x = 1\n', encoding = 'utf-8')
state.save({'roots': [str(PROJECT)], 'tabs': [{'path': str(DEMO)}], 'active': str(DEMO), 'autosave': False, 'consoleOpen': True,
            'usage_log': False, 'update_check': False})
import webview

from slate import instance
from slate.system import MAC

results, failed = {'platform': sys.platform}, []


def js(window, code, wait = 60):
    done, box = threading.Event(), {}
    code = f'(async () => ({code.strip()}))()'  # pywebview calls back only for promises; a plain value never arrives
    threading.Thread(target = window.evaluate_js, args = (code,), kwargs = {'callback': lambda r: (box.update(r = r), done.set())}, daemon = True).start()
    done.wait(wait)
    return box.get('r')


def until(window, condition, wait = 60):
    """The page's value of condition once it is truthy, or None after wait seconds."""
    end = time.time() + wait
    while time.time() < end:
        value = js(window, condition, wait = 10)
        if value:
            return value
        time.sleep(0.5)
    return None


def check(name, value, expect = True):
    ok = value == expect if expect is not True else bool(value)
    results[name] = value if ok else {'got': value, 'expected': expect}
    if not ok:
        failed.append(name)


def probe(window):
    try:
        run(window)
    except Exception as e:
        failed.append(f'exception: {type(e).__name__}: {e}')
    finally:
        window.destroy()


def run(window):
    check('page ready', until(window, 'typeof S !== "undefined" && S.ready && !!S.active && S.active.path'), expect = str(DEMO))
    check('platform seen by the page', json.loads(js(window, 'JSON.stringify([S.mac, S.sep])') or 'null'), expect = [MAC, '/' if MAC else '\\'])
    check('lint marks the SyntaxWarning', until(window, "S.active.linted && (S.active.markers || []).some((m) => m.line === 7) && 1"), expect = 1)  # also style findings where a glycowork checkout is
    check('cells marked', js(window, "S.active.model.getAllDecorations().filter((d) => d.options.className === 'cell-head').length"), expect = 2)
    js(window, 'editor.setPosition({lineNumber: 2, column: 1}) || runAtCursor(true) || 1')
    check('cell ran in the console', until(window, "$('console-out').innerText.includes('smoke 42 project') && 1"), expect = 1)
    check('Shift+Enter moved to the next cell', js(window, 'editor.getPosition().lineNumber'), expect = 7)
    js(window, 'runAtCursor(false) || 1')
    check('traceback links to the file', until(window, "[...document.querySelectorAll('#console-out .link')].map((s) => s.textContent.trim()).join('|')"))
    check('settings panel lists Pythons', until(window, "openSettings().then(() => document.querySelectorAll('#settings select option').length)"))
    js(window, "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})) || 1")
    key = "{key: 'p', code: 'KeyP', metaKey: true, bubbles: true}" if MAC else "{key: 'p', code: 'KeyP', ctrlKey: true, bubbles: true}"
    js(window, f"window.dispatchEvent(new KeyboardEvent('keydown', {key})) || 1")
    check('Ctrl/Cmd+P opens the finder', until(window, "!$('finder').classList.contains('hidden') && 1", wait = 10), expect = 1)
    js(window, "closeFinder() || 1")
    check('second launch hands over a file', instance.hand_off([str(OTHER)]))
    check('handed file opened', until(window, f"S.tabs.some((t) => t.path === {json.dumps(str(OTHER))}) && 1"), expect = 1)
    errors = js(window, "JSON.stringify(usage.events.filter((e) => /error/.test(e.kind)).map((e) => e.call || e.message))")
    check('no page or API errors', json.loads(errors or '["no answer"]'), expect = [])


def start(*a, **kw):
    threading.Thread(target = probe, args = (webview.windows[0],), daemon = True).start()
    return original(*a, **kw)


original = webview.start
webview.start = start
sys.argv = sys.argv[:1]
from slate.app import main

T0 = time.time()
main()
results['seconds'] = round(time.time() - T0, 1)
results['failed'] = failed
print(json.dumps(results), flush = True)
sys.exit(1 if failed else 0)
