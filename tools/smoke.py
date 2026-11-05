"""Check that slate works in a real window, on Windows, macOS and Linux: the page, the bridge, linting, the console, running a
cell, a restored tab read only when shown, a traceback link, Markdown output with an inline image, viscose figures as stickers, live figures, saving them and redrawing them in another palette, the figure palette,
the settings panel and its keys, a key, find after a diff view, a second launch handing over a file, renaming a file (and moving one to the Trash on macOS and Linux), tables and
a row's panel, local history and the last edit location.

python tools/smoke.py   prints one JSON line of results and exits 1 if any check failed

Runs an isolated session (a temporary folder with its own state.json and a small project), so the real session is
never touched, and removed again at the end. .github/workflows/smoke.yml runs it on GitHub's macOS, Windows and Ubuntu machines (Ubuntu under Xvfb),
where nobody can watch the window; it is how the macOS and Linux sides are checked from a Windows desk. Each probe waits at most 60 s for the page.
"""
import json
import shutil
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
UNREAD = PROJECT / 'unread.py'
UNREAD.write_text('y = 2\n', encoding = 'utf-8')
SCRIPTED = PROJECT / 'scripted' / 'module.py'  # a project whose check_style.py does its work on import and exits, as viscose's did
(SCRIPTED.parent / '.github' / 'scripts').mkdir(parents = True)
(SCRIPTED.parent / '.github' / 'scripts' / 'check_style.py').write_text('import sys\nsys.exit("a script")\n', encoding = 'utf-8')
SCRIPTED.write_text('x = 1\n', encoding = 'utf-8')
state.save({'roots': [str(PROJECT)], 'tabs': [{'path': str(DEMO)}, {'path': str(UNREAD)}], 'active': str(DEMO), 'autosave': False, 'consoleOpen': True,
            'usage_log': False, 'update_check': False})
import webview

from slate import instance
from slate.system import LINUX, MAC, WINDOWS

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
    unread = f"S.tabs.find((t) => t.path === {json.dumps(str(UNREAD))})"
    check('a restored tab is not read before it is shown', js(window, f"{unread}.model === null && 1"), expect = 1)
    js(window, f"activate({unread}) || 1")
    check('an unread tab is read and checked when shown', until(window, f"S.active === {unread} && S.active.linted && S.active.model.getValue().trim()"), expect = 'y = 2')
    js(window, f"activate(S.tabs.find((t) => t.path === {json.dumps(str(DEMO))})) || 1")
    check('platform seen by the page', json.loads(js(window, 'JSON.stringify([S.mac, S.linux, S.sep])') or 'null'), expect = [MAC, LINUX, '\\' if WINDOWS else '/'])
    colors = "S.active.model.getAllDecorations().map((d) => d.options.inlineClassName).filter((c) => /^snfg-(purple|yellow|blue)$/.test(c || '')).length"
    check('glycan residues in SNFG colours', until(window, colors, wait = 10), expect = 3)  # page only, so without glycowork too; only the front file has them
    check('lint marks the SyntaxWarning', until(window, "S.active.linted && (S.active.markers || []).some((m) => m.line === 7) && 1"), expect = 1)  # also style findings where a glycowork checkout is
    check('a checker that is only a script is left alone', until(window, f"api.lint({json.dumps(str(SCRIPTED))}, 'x = 1\\n', false).then((r) => r.note.includes('only a script') && 1)", wait = 10), expect = 1)
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
    # A 2x2 PNG, as viscose's objects would show it; viscose itself is not on the runners
    figures = ("import base64, sys\npng = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAEUlEQVR4nGP4z8DwH4QZYAwAR8oH+WdZbrcAAAAASUVORK5CYII=')\n"
               "class Plain:\n    def _repr_png_(self):\n        return png\n"
               "class Png(Plain):\n    __module__ = 'viscose.figure'\n"
               "display(Plain(), Png())\n"
               "svg = '<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"10\" height=\"10\">%s<rect width=\"10\" height=\"10\"/></svg>'\n"
               "swap = sys.modules['__main__'].svg_figure\nswap(svg % '')\nswap(svg % '<metadata>viscose</metadata>')")
    js(window, f"(consoleInput.setValue({json.dumps(figures)}), submitConsole(), 1)")
    shown = "[...document.querySelectorAll('#console-out img.plot')].map((i) => i.className + ' ' + i.style.width).join('|')"
    check('viscose figures get the sticker', until(window, f"(s => s.split('|').length === 4 && s)({shown})"),
          expect = 'plot 2px|plot figure 1px|plot |plot figure ')
    live = ("(async () => { const n = document.querySelectorAll('#console-out img.plot').length;"
            " appendImage({mime: 'image/svg+xml', data: btoa('<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"100\" height=\"100\"><rect width=\"100\" height=\"100\"/></svg>'), figure: true,"
            " dots: {page: [100, 100], axes: [{x: 'log_{2} <FC>', y: '', dots: [[50, 50, '1.5', '2.5']]}]}});"
            " const img = document.querySelectorAll('#console-out img.plot')[n]; await img.decode(); img.scrollIntoView(); const r = img.getBoundingClientRect();"
            " const move = (dx, dy) => img.dispatchEvent(new MouseEvent('mousemove', {clientX: r.left + 50 + dx, clientY: r.top + 50 + dy}));"
            " move(3, -3); const near = figureTip.box.classList.contains('hidden') ? null : figureTip.box.querySelector('sub').textContent + figureTip.box.innerText.replace(/\\s+/g, ' ');"
            " move(30, 30); const hidden = figureTip.box.classList.contains('hidden'); img.remove(); return [near, hidden]; })()")
    check('a figure names the dot under the mouse', js(window, live), expect = ['2log2 <FC> 1.5 y 2.5', True])
    saved = HOME / 'saved.svg'
    window.create_file_dialog = lambda *args, **kwargs: (str(saved),)  # a dialog nobody could answer here
    js(window, "([...document.querySelectorAll('#console-out img.plot.figure')].pop().dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, clientX: 10, clientY: 10})), [...$('menu').children].find((d) => d.textContent === 'Save as SVG…').click(), 1)")
    check('a figure saved from its menu', until(window, "$('console-out').innerText.includes('Saved ') && 1", wait = 10) and '<metadata>viscose' in saved.read_text(encoding = 'utf-8'))
    # A stand-in with what redraw_figure asks of viscose's Figure, which the runners do not have
    redrawable = ("import types\nclass Fig:\n    __module__ = 'viscose.figure'\n    axes = []\n    def __init__(self, name):\n        self.pal = types.SimpleNamespace(name = name)\n"
                  "    def in_palette(self, name):\n        return Fig(name)\n    def to_svg(self):\n        return '<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"40\" height=\"30\"><metadata>viscose</metadata><title>' + self.pal.name + '</title></svg>'\n"
                  "    def to_png(self, dpi = 144.0):\n        return png\ndisplay(Fig('poster'))")
    js(window, f"(consoleInput.setValue({json.dumps(redrawable)}), submitConsole(), 1)")
    newest = "[...document.querySelectorAll('#console-out img.plot.figure')].pop()"
    check('a figure offers other palettes', until(window, f"{newest}.dataset.palette", wait = 10), expect = 'poster')
    count = "document.querySelectorAll('#console-out img.plot').length"
    before = js(window, count)
    js(window, f"(showPaletteChips({newest}), [...paletteChips.box.children].find((c) => c.dataset.palette === 'aura').click(), 1)")
    check('a figure redrawn in another palette, in place', until(window, f"{newest}.dataset.palette === 'aura' && {count}", wait = 10), expect = before)
    js(window, "(paletteChips.box.classList.add('hidden'), 1)")
    stand_in = "import sys, types\nsys.modules['viscose.palettes'] = types.SimpleNamespace(set_palette = lambda name: print('palette is', name))"
    js(window, f"(consoleInput.setValue({json.dumps(stand_in)}), submitConsole(), 1)")
    js(window, "api.set_setting('figure_palette', 'poster').then(() => 1)")
    check('the figure palette reaches a running console', until(window, "$('console-out').innerText.includes('palette is poster') && 1", wait = 10), expect = 1)
    js(window, "api.set_setting('figure_palette', null).then(() => 1)")
    check('call hints know a method from a call', json.loads(js(window, "JSON.stringify([callAt('axes[0].plot(').qualifier, callAt('a.b.c(').qualifier, callAt('f().g('), callAt('d[k][0].e(')])") or 'null'), expect = ['axes', 'a.b', None, None])
    check('settings panel lists Pythons', until(window, "openSettings().then(() => document.querySelectorAll('#settings select option').length)"))
    check('settings panel offers the palettes', js(window, "[...document.querySelectorAll('#settings select option')].filter((o) => ['lumen', 'couture', 'poster', 'aura'].includes(o.value)).length"), expect = 4)
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
    check('no other tabs', js(window, 'S.tabs.length'), expect = 3)  # on macOS AppKit once also opened smoke.py from the command line
    spare = PROJECT / 'spare.txt'
    spare.write_text('spare\n', encoding = 'utf-8')
    renamed = js(window, f"api.rename({json.dumps(str(spare))}, 'renamed.txt')")
    check('rename in the tree', renamed and Path(renamed).name == 'renamed.txt' and Path(renamed).is_file() and not spare.exists())
    if not WINDOWS:  # Windows' Recycle Bin is checked by hand: a runner without one would show Windows' delete-for-good question and hang
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
    to_row = ("(async () => { const n = document.querySelectorAll('#console-out img.plot').length;"
              " appendImage({mime: 'image/svg+xml', data: btoa('<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"100\" height=\"100\"><rect width=\"100\" height=\"100\"/></svg>'), figure: true,"
              " dots: {page: [100, 100], axes: [{x: 'a', y: 'b', dots: [[20, 20, '1', '2', 'Fuc(a1-2)Gal'], [80, 80, '3', '4', 'Neu5Gc(a2-6)Gal']]}]}});"
              " const img = document.querySelectorAll('#console-out img.plot')[n]; await img.decode(); img.scrollIntoView(); const r = img.getBoundingClientRect();"
              " const at = (x) => ({clientX: r.left + x, clientY: r.top + x}); const tip = (x) => { img.dispatchEvent(new MouseEvent('mousemove', at(x))); return figureTip.box.innerText.includes('Click to open'); };"
              " const hints = [tip(20), tip(80)]; img.dispatchEvent(new MouseEvent('click', {...at(20), bubbles: true})); const row = document.querySelector('#table-view .tv-row.sel .tv-cell').title;"
              " const mark = document.querySelector('#console-out .figure-mark'), marked = [Math.round(mark.offsetLeft + mark.offsetWidth / 2 - img.offsetLeft), Math.round(mark.offsetTop + mark.offsetHeight / 2 - img.offsetTop)];"
              " document.querySelector('#table-view .tv-row.sel').click(); const again = !!document.querySelector('#console-out .figure-mark');"
              " img.dispatchEvent(new MouseEvent('mouseleave')); document.querySelector('#table-view .tv-close').click(); const cleared = !document.querySelector('#console-out .figure-mark'); img.remove(); return [hints, row, marked, again, cleared]; })()")
    check('a dot and its table row lead to each other', js(window, to_row), expect = [[True, False], 'Fuc(a1-2)Gal', [20, 20], True, True])
    js(window, "$('table-view').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})) || 1")
    check('table view closes', js(window, "!$('table-view') && $('st-table').textContent"), expect = '⊞ Table')
    groups = PROJECT / 'groups.csv'  # two sample groups: the table view offers glycowork's differential expression
    groups.write_text('glycan,H1,H2,P1,P2\nNeu5Ac(a2-3)Gal(b1-4)GlcNAc,1,2,3,4\nFuc(a1-2)Gal,2,3,4,5\n', encoding = 'utf-8')
    js(window, f"openFile({json.dumps(str(groups))}).then(() => (toggleTable(S.active, true), 1))")
    check('table view offers Analyze', until(window, "$('table-view') && [...$('table-view').querySelectorAll('.tv-bar button')].map((b) => b.textContent).join(' ')", wait = 10),
          expect = '▶ Analyze ▦ Cluster ≡ Text')
    # The button only where viscose can be imported, which the runners cannot, so not by a fixed answer; an open folder holding it counts
    (HOME / 'checkout' / 'viscose').mkdir(parents = True)
    (HOME / 'checkout' / 'viscose' / '__init__.py').write_text('', encoding = 'utf-8')
    has = js(window, "api.has_viscose(tableCwd(S.active), S.roots)")
    check('Cluster shows only with viscose', until(window, f"!document.querySelector('#table-view .tv-bar .hidden') === {json.dumps(bool(has))} && 1", wait = 15), expect = 1)
    check('an open folder holding viscose counts', js(window, f"api.has_viscose(tableCwd(S.active), [{json.dumps(str(HOME / 'checkout'))}])"), expect = True)
    (HOME / 'checkout' / 'viscose' / 'figure.py').write_text('def linkage(data):\n    pass\n', encoding = 'utf-8')
    check('a viscose with its own linkage says so', js(window, f"api.has_viscose(tableCwd(S.active), [{json.dumps(str(HOME / 'checkout'))}])"), expect = 'linkage')
    typed = ("(() => { const run = runTyped; let code = ''; runTyped = (c) => { code = c; };"
             " [...document.querySelectorAll('#table-view .tv-bar button')].find((b) => b.textContent.includes('Cluster')).click(); runTyped = run;"
             " return code.split('\\n').filter((l) => /read_csv|clustermap/.test(l)).map((l) => l.replace(/r'[^']*'/, 'PATH')); })()")
    check('Cluster writes its code', js(window, typed), expect = ["df = pd.read_csv(PATH, index_col = 0).select_dtypes('number')",
                                                                 "fig, ax = vc.clustermap(z, figsize = (8, 3.3), xlabel = 'Samples', ylabel = 'Glycans', colorbar_label = 'z-score')"])
    # viscose is not on the runners, so a stand-in map, in cell_map's form: 2 x 2 cells over the middle of the figure
    grid = ("(async () => { const n = document.querySelectorAll('#console-out img.plot').length, values = btoa(String.fromCharCode(...new Uint8Array(new Float32Array([1.5, NaN, -2, 3]).buffer)));"
            " appendImage({mime: 'image/svg+xml', data: btoa('<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"100\" height=\"100\"><rect width=\"100\" height=\"100\"/></svg>'), figure: true,"
            " dots: {page: [100, 100], axes: [], cells: [{box: [20, 20, 80, 80], origin: [20, 20], step: [30, 30], rows: ['Neu5Ac(a2-3)Gal(b1-4)GlcNAc', 'Fuc(a1-2)Gal'], columns: ['H1', 'H2'], x: 'Samples', y: 'z-score', values}]}});"
            " const img = document.querySelectorAll('#console-out img.plot')[n]; await img.decode(); img.scrollIntoView(); const r = img.getBoundingClientRect();"
            " const at = (x, y) => ({clientX: r.left + x, clientY: r.top + y, bubbles: true}), tip = () => (figureTip.box.classList.contains('hidden') ? null : figureTip.box.innerText.replace(/\\s+/g, ' ').replace(/ Click.*/, ''));"
            " img.dispatchEvent(new MouseEvent('mousemove', at(70, 30))); const empty = tip(); img.dispatchEvent(new MouseEvent('mousemove', at(30, 70))); const cell = tip(); const ring = [figureTip.ring.style.width, figureTip.ring.style.height];"
            " img.dispatchEvent(new MouseEvent('mousemove', at(10, 10))); const off = tip();"
            " img.dispatchEvent(new MouseEvent('click', at(30, 70))); const row = document.querySelector('.tv-side .tv-title').textContent, mark = document.querySelector('#console-out .figure-mark');"
            " const band = [mark.className, mark.style.width, mark.style.height];"
            " document.querySelector('#table-view .tv-close').click(); const cleared = !document.querySelector('#console-out .figure-mark');"
            " document.querySelector('#table-view .tv-row .tv-cell').click(); const back = !!document.querySelector('#console-out .figure-mark.band'); document.querySelector('#table-view .tv-close').click();"
            " img.remove(); return [empty, cell, ring, off, row, band, cleared, back]; })()")
    check('a heatmap names the cell under the mouse, and it and its row lead to each other', js(window, grid),
          expect = ['Neu5Ac(a2-3)Gal(b1-4)GlcNAc Sample H2 z-score missing', 'Fuc(a1-2)Gal Sample H1 z-score -2', ['30px', '30px'], None, 'Fuc(a1-2)Gal', ['figure-mark band', '60px', '30px'], True, True])
    # What the console's side makes of a heatmap, with stand-ins for viscose's objects; the runners may lack numpy
    stand_in = ("import json, sys\ntry:\n    import numpy\nexcept ImportError:\n    numpy = None\nmain = sys.modules['__main__']\n"
                "class Axes:\n    rect, xlabel, _cbar = (10, 10, 80, 80), 'Samples', None\n    def __init__(self, artists):\n        self.artists = artists\n"
                "    def page(self, x, y):\n        return 10 + 40 * x, 10 + 40 * y\n"
                "class Fig:\n    width, height = 100, 100\n    def __init__(self, *artists):\n        self.axes = [Axes(list(artists))]\n"
                "if numpy is None:\n    print('cell map: no numpy')\nelse:\n"
                "    found = main.dot_map(Fig({'kind': 'heat', 'm': numpy.ones((2, 2)), 'rows': ['a', 'a '], 'columns': ['x', 'y']}))['cells'][0]\n"
                "    print('cell map:', json.dumps([found['box'], found['origin'], found['step'], found['rows'], found['nth'], found['x'], len(found['values'])]), main.dot_map(Fig({'kind': 'line'})))\n")
    js(window, f"(consoleInput.setValue({json.dumps(stand_in)}), submitConsole(), 1)")
    check('the console maps a heatmap', until(window, "(/cell map: .*/.exec($('console-out').innerText) || [])[0]", wait = 15) in ('cell map: no numpy', 'cell map: [[10.0, 10.0, 90.0, 90.0], [10.0, 10.0], [40.0, 40.0], ["a", "a"], [0, 1], "Samples", 24] None'))
    js(window, "(document.querySelector('#table-view .tv-row .tv-cell').click(), 1)")
    check("a row's panel shows its groups", until(window, "(document.querySelector('.tv-plot img') && document.querySelector('.tv-plot img').naturalWidth > 100) || !document.querySelector('.tv-bars').classList.contains('hidden')", wait = 15))  # a box plot, or without glycowork and viscose on the runners, bars
    js(window, "(document.querySelector('#table-view .tv-close').click(), 1)")
    js(window, "(toggleTable(S.active, false), 1)")
    check('Markdown tables', js(window, "markdownHtml('| Glycan | Log2FC |\\n|---|---|\\n| Gal(b1-3)GalNAc | 0.87 |\\n| a\\\\|b | -1e-07 |', true)"),
          expect = '<div class="md-table"><table><thead><tr><th>Glycan</th><th>Log2FC</th></tr></thead><tbody><tr><td>Gal(b1-3)GalNAc</td>'
                   '<td class="num">0.87</td></tr><tr><td>a|b</td><td class="num">-1e-07</td></tr></tbody></table></div>')
    contexts = "['Neu5Ac(a2-3)Gal(b1-', 'Gal(b1-3)[Fuc(a1-4)]Gl', 'Gal(b1-3)[', 'self.f('].map((p) => { const c = glycanContext(p); return c && [c.kind, c.child || '', c.typed].join(' '); })"
    check('glycan typing contexts', js(window, contexts), expect = ['link Gal b1-', 'parent Fuc Gl', 'leaf  ', None])
    refs = "referencesIn('PMID: 37608048, doi:10.1016/S0092-8674(00)81683-9. G00031MO').map((r) => r.kind + ':' + r.id).join(' ')"
    check('PMIDs and DOIs found', js(window, refs), expect = 'pmid:37608048 doi:10.1016/S0092-8674(00)81683-9')
    check('TODO in a comment', js(window, "monaco.editor.tokenize('x = 1  # TODO later', 'py')[0].map((t) => t.type).join(' ')"), expect = 'identifier.py  operator.py  number.py  comment.py comment.todo.py')
    edit = f"openFile({json.dumps(str(OTHER))}).then((t) => {{ revealAt(1, 6); editor.trigger('keyboard', 'type', {{text: '0'}}); return save(t); }}).then(() => api.history({json.dumps(str(OTHER))}))"
    kept = js(window, edit)
    check('local history keeps what a save replaced', kept and js(window, f"api.history_text({json.dumps(str(OTHER))}, {json.dumps(kept[0]['id'])}).then((v) => v.text.trim())"), expect = 'x = 1')  # trimmed: write_text gave it CRLF on Windows
    check('last edit location', js(window, f"openFile({json.dumps(str(DEMO))}, {{line: 3}}).then(() => lastEdit()).then(() => [basename(S.active.path), editor.getPosition().lineNumber])"), expect = ['other.py', 1])
    # demo.py's SyntaxWarning is another tab's finding: Monaco's "next problem in files" went there from the end of this file
    broken = PROJECT / 'zz_broken.py'
    broken.write_text('x = 1\nif x:\nprint(1)\ny = 2\nz = 3\n', encoding = 'utf-8')
    js(window, f"openFile({json.dumps(str(broken))}).then(() => 1)")
    check('the status bar names the error', until(window, "S.active.linted && $('st-lint').textContent", wait = 10), expect = '✕ IndentationError line 3')
    js(window, "(editor.setPosition({lineNumber: 5, column: 1}), $('st-lint').click(), 1)")
    check('its click goes to the error in that file', js(window, "[basename(S.active.path), editor.getPosition().lineNumber]"), expect = ['zz_broken.py', 3])
    js(window, f"openFile({json.dumps(str(DEMO))}, {{line: 9}}).then(() => ($('st-lint').click(), 1))")
    check('its click on findings stays in the file', until(window, "editor.getPosition().lineNumber === 7 && basename(S.active.path)", wait = 10), expect = 'demo.py')
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
for _ in range(20):  # WebView2's processes let go of the profile in HOME a moment after the window closes
    shutil.rmtree(HOME, ignore_errors = True)
    if not HOME.exists():
        break
    time.sleep(0.5)
else:
    print(f'smoke: could not remove {HOME}', file = sys.stderr, flush = True)
sys.exit(1 if failed else 0)
