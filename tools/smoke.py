"""Check that slate works in a real window, on Windows and macOS: the page, the bridge, linting, the console, running a
cell, a traceback link, Markdown output with an inline image, the settings panel and its keys, a key, find after a diff
view, a second launch handing over a file, renaming a file (and moving one to the Trash on macOS), tables, local history
and the last edit location.

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
from slate import history, state

HOME = Path(tempfile.mkdtemp(prefix = 'slate-smoke-')).resolve()  # the long path, as slate opens files: TEMP can be a short 8.3 one (RUNNER~1)
state.STATE_FILE = HOME / 'state.json'
history.ROOT = HOME / 'history'
PROJECT = HOME / 'project'
PROJECT.mkdir()
DEMO = PROJECT / 'demo.py'
DEMO.write_text('# %% first\nimport os\nvalue = 6 * 7\nprint("smoke", value, os.path.basename(os.getcwd()))\n\n# %% second\npattern = "\\d"\n1 / 0\nglycan = "Neu5Ac(a2-3)Gal(b1-4)GlcNAc"\n',
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
    # pywebview calls back only for promises; a plain value never arrives. A value its JSON drops (undefined) loses the
    # message's `params`, and on macOS its handler then raised KeyError and aborted the app. So the page sends a JSON string
    # of its own: undefined as null, a rejection as {error}, and anything JSON cannot hold as {unserializable}. The macOS
    # runs of 2026-10-24 got such a message although undefined and rejections were already mapped; which probe sent it is
    # what the stderr line below is for
    script = (f'(async () => ({code.strip()}))().then((v) => v === undefined ? null : v, (e) => ({{error: String(e && e.stack || e)}}))'
              ".then((v) => { try { const s = JSON.stringify(v); if (s !== undefined) return s; } catch (e) {} "
              "return JSON.stringify({unserializable: typeof v + ': ' + String(v).slice(0, 200)}); })")
    threading.Thread(target = window.evaluate_js, args = (script,), kwargs = {'callback': lambda r: (box.update(r = r), done.set())}, daemon = True).start()
    done.wait(wait)
    value = json.loads(box['r']) if isinstance(box.get('r'), str) else box.get('r')
    if isinstance(value, dict) and 'unserializable' in value:
        print(f'smoke: {code.strip()[:120]!r} gave {value["unserializable"]}', file = sys.stderr, flush = True)
    return value


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
    colors = "S.active.model.getAllDecorations().map((d) => d.options.inlineClassName).filter((c) => /^snfg-(purple|yellow|blue)$/.test(c || '')).length"
    check('glycan residues in SNFG colours', until(window, colors, wait = 10), expect = 3)  # page only, so without glycowork too; only the front file has them
    check('lint marks the SyntaxWarning', until(window, "S.active.linted && (S.active.markers || []).some((m) => m.line === 7) && 1"), expect = 1)  # also style findings where a glycowork checkout is
    check('cells marked', js(window, "S.active.model.getAllDecorations().filter((d) => d.options.className === 'cell-head').length"), expect = 2)
    js(window, 'editor.setPosition({lineNumber: 2, column: 1}) || runAtCursor(true) || 1')
    check('cell ran in the console', until(window, "$('console-out').innerText.includes('smoke 42 project') && 1"), expect = 1)
    check('Shift+Enter moved to the next cell', js(window, 'editor.getPosition().lineNumber'), expect = 7)
    js(window, 'runAtCursor(false) || 1')
    check('traceback links to the file', until(window, "[...document.querySelectorAll('#console-out .link')].map((s) => s.textContent.trim()).join('|')"))
    markdown = ("import base64\nsvg = base64.b64encode(b'<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"10\" height=\"10\"><rect width=\"10\" height=\"10\"/></svg>').decode()\n"
                "class Note:\n    def _repr_markdown_(self):\n        return '**bold** <img src=\"data:image/svg+xml;base64,' + svg + '\" height=\"12\">'\n"
                "display(Note())")  # display() is a builtin, as in Jupyter; the image stands in for GlycoDraw's as_markdown()
    js(window, f"(consoleInput.setValue({json.dumps(markdown)}), submitConsole(), 1)")
    check('Markdown output with its image', until(window, "(document.querySelector('#console-out div.md b') && document.querySelector('#console-out div.md img.chip') || {}).naturalWidth"), expect = 10)
    check('settings panel lists Pythons', until(window, "openSettings().then(() => document.querySelectorAll('#settings select option').length)"))
    keys = "[...document.querySelectorAll('#settings .keys-row')].map((r) => r.textContent)"
    check('settings panel lists the keys', js(window, f"(rows => rows.length > 25 && rows.find((r) => r.includes('find in files')).split('find')[0])({keys})"),
          expect = '⌘⇧F' if MAC else 'Ctrl+Shift+F')
    js(window, "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})) || 1")
    key = "{key: 'p', code: 'KeyP', metaKey: true, bubbles: true}" if MAC else "{key: 'p', code: 'KeyP', ctrlKey: true, bubbles: true}"
    js(window, f"window.dispatchEvent(new KeyboardEvent('keydown', {key})) || 1")
    check('Ctrl/Cmd+P opens the finder', until(window, "!$('finder').classList.contains('hidden') && 1", wait = 10), expect = 1)
    js(window, "closeFinder() || 1")
    # a diff view closed before the session's first Ctrl+F once left Monaco unable to build the find widget at all
    js(window, "(showDiff('a\\n', 'b\\n'), 1)")  # showDiff returns its close function, which JSON cannot carry
    js(window, "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})) || 1")
    check('find works after a diff view', until(window, "editor.getAction('actions.find').run().then(() => document.querySelectorAll('#editor .find-widget').length)", wait = 10), expect = 1)
    check('second launch hands over a file', instance.hand_off([str(OTHER)]))
    check('handed file opened', until(window, f"S.tabs.some((t) => t.path === {json.dumps(str(OTHER))}) && 1"), expect = 1)
    check('no other tabs', js(window, 'S.tabs.length'), expect = 2)  # on macOS AppKit once also opened smoke.py from the command line
    spare = PROJECT / 'spare.txt'
    spare.write_text('spare\n', encoding = 'utf-8')
    renamed = js(window, f"api.rename({json.dumps(str(spare))}, 'renamed.txt')")
    check('rename in the tree', renamed and Path(renamed).name == 'renamed.txt' and Path(renamed).is_file() and not spare.exists())
    if MAC:  # Windows' Recycle Bin is checked by hand: a runner without one would show Windows' delete-for-good question and hang
        js(window, f"api.trash({json.dumps(str(PROJECT / 'renamed.txt'))}) || 1")
        check('moved to the Trash', not (PROJECT / 'renamed.txt').exists())
    table = PROJECT / 'table.csv'
    table.write_text('glycan,abundance\nNeu5Ac(a2-3)Gal(b1-4)GlcNAc,1\n"Fuc(a1-2)Gal",2\n', encoding = 'utf-8')
    js(window, f"openFile({json.dumps(str(table))}).then(() => 1)")
    check('glycan cells of a table in SNFG colours', until(window, colors.replace('|blue', '|blue|red'), wait = 10), expect = 5)
    js(window, "(toggleTable(S.active, true), 1)")
    check('table view rows', until(window, "$('table-view') && $('table-view').querySelector('.tv-count').textContent", wait = 10), expect = '2 of 2 rows')
    sort = "(() => { const h = $('table-view').querySelectorAll('.tv-head .tv-cell')[1]; h.click(); h.click(); return $('table-view').querySelector('.tv-row .tv-cell').textContent; })()"
    check('table view sorts a column', js(window, sort), expect = 'Fuc(a1-2)Gal')  # abundance, descending
    js(window, "$('table-view').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})) || 1")
    check('table view closes', js(window, "!$('table-view') && $('st-table').textContent"), expect = '⊞ Table')
    contexts = "['Neu5Ac(a2-3)Gal(b1-', 'Gal(b1-3)[Fuc(a1-4)]Gl', 'Gal(b1-3)[', 'self.f('].map((p) => { const c = glycanContext(p); return c && [c.kind, c.child || '', c.typed].join(' '); })"
    check('glycan typing contexts', js(window, contexts), expect = ['link Gal b1-', 'parent Fuc Gl', 'leaf  ', None])
    refs = "referencesIn('PMID: 37608048, doi:10.1016/S0092-8674(00)81683-9. G00031MO').map((r) => r.kind + ':' + r.id).join(' ')"
    check('PMIDs and DOIs found', js(window, refs), expect = 'pmid:37608048 doi:10.1016/S0092-8674(00)81683-9')
    check('TODO in a comment', js(window, "monaco.editor.tokenize('x = 1  # TODO later', 'py')[0].map((t) => t.type).join(' ')"), expect = 'identifier.py  operator.py  number.py  comment.py comment.todo.py')
    edit = f"openFile({json.dumps(str(OTHER))}).then((t) => {{ revealAt(1, 6); editor.trigger('keyboard', 'type', {{text: '0'}}); return save(t); }}).then(() => api.history({json.dumps(str(OTHER))}))"
    kept = js(window, edit)
    check('local history keeps what a save replaced', kept and js(window, f"api.history_text({json.dumps(str(OTHER))}, {json.dumps(kept[0]['id'])}).then((v) => v.text.trim())"), expect = 'x = 1')  # trimmed: write_text gave it CRLF on Windows
    check('last edit location', js(window, f"openFile({json.dumps(str(DEMO))}, {{line: 3}}).then(() => lastEdit()).then(() => [basename(S.active.path), editor.getPosition().lineNumber])"), expect = ['other.py', 1])
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
