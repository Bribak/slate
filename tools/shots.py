"""The README's screenshots (docs/*.png): slate in a real window with a throwaway session, a short analysis script and
glycowork's checkout among its folders, driven through the page and captured with PrintWindow.

python tools/shots.py [--out docs]   Windows only; needs Pillow, and glycowork with matplotlib for the console's Python

Each scene is cropped to the part of the window it is about and saved at twice its CSS size (at most the screen's own
scaling), so it stays sharp on high-resolution screens. The window stays on top while it runs, since Chromium draws no
frames for a covered window. Prints the files written and exits 1 when a scene did not appear.
"""
import argparse
import ctypes
import json
import os
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from slate import state

ap = argparse.ArgumentParser()
ap.add_argument('--out', default = str(ROOT / 'docs'))
args = ap.parse_args()
OUT = Path(args.out)
OUT.mkdir(exist_ok = True)
GLYCOWORK = Path.home() / 'Documents' / 'glycowork'
W, H = 1240, 780  # the window's client area in CSS pixels
DEMO_TEXT = '''"""Sialylated glycans in a serum sample: what they are, and how much of each."""
# %% the glycans
import matplotlib.pyplot as plt
from glycowork.motif.draw import GlycoDraw
from glycowork.motif.tokenization import glycan_to_mass

abundance = {  # % of the glycan signal
    'Neu5Ac(a2-6)Gal(b1-4)GlcNAc(b1-2)Man(a1-3)[Man(a1-6)]Man(b1-4)GlcNAc(b1-4)GlcNAc': 41.2,
    'Gal(b1-4)GlcNAc(b1-2)Man(a1-3)[Man(a1-6)]Man(b1-4)GlcNAc(b1-4)[Fuc(a1-6)]GlcNAc': 18.5,
    'Man(a1-3)[Man(a1-6)]Man(b1-4)GlcNAc(b1-4)GlcNAc': 6.3,
    'Neu5Ac(a2-3)Gal(b1-4)[Fuc(a1-3)]GlcNAc(b1-6)[Gal(b1-3)]GalNAc': 4.1,
    'Neu5Ac(a2-3)Gal(b1-3)[Neu5Ac(a2-6)]GalNAc': 2.7,
    }
reference = 'Neu5Aca2-3Galb1-3GalNAc'  # copied from an older table

# %% the most abundant one
best = max(abundance, key = abundance.get)
GlycoDraw(best)

# %% how much of it is sialylated
sialylated = sum(v for g, v in abundance.items() if 'Neu5Ac' in g)
fig, ax = plt.subplots(figsize = (5, 1.6))
ax.barh(['sialylated', 'other'], [sialylated, sum(abundance.values()) - sialylated], color = ['#A5509C', '#9B9B9B'])
ax.set_xlabel('% of the glycan signal')
plt.show()
'''
LINES = DEMO_TEXT.split('\n')
SLEX_LINE = next(n for n, line in enumerate(LINES, 1) if 'Fuc(a1-3)' in line)
REFERENCE_LINE = next(n for n, line in enumerate(LINES, 1) if line.startswith('reference'))
SLEX = LINES[SLEX_LINE - 1].split("'")[1]

HOME = Path(tempfile.mkdtemp(prefix = 'slate-shots-')).resolve()
state.STATE_FILE = HOME / 'state.json'
PROJECT = HOME / 'serum'
PROJECT.mkdir()
DEMO = PROJECT / 'sialylation.py'
DEMO.write_text(DEMO_TEXT, encoding = 'utf-8')
scale = ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100  # the main monitor's, where the window opens
x0, y0 = round(80 * scale), round(60 * scale)
state.save({'roots': [str(PROJECT), str(GLYCOWORK)], 'tabs': [{'path': str(DEMO)}], 'active': str(DEMO), 'autosave': False, 'consoleOpen': False,
            'usage_log': False, 'update_check': False, 'sidebarWidth': 250, 'consoleHeight': 470,
            'placement': {'rect': [x0, y0, x0 + round(W * scale) + 16, y0 + round(H * scale) + 40], 'maximized': False}})
import webview

user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
for f, res, argt in [(user32.GetDC, ctypes.c_void_p, [wintypes.HWND]), (user32.ReleaseDC, ctypes.c_int, [wintypes.HWND, ctypes.c_void_p]),
                     (gdi32.CreateCompatibleDC, ctypes.c_void_p, [ctypes.c_void_p]), (gdi32.CreateCompatibleBitmap, ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]),
                     (gdi32.SelectObject, ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p]), (gdi32.DeleteObject, wintypes.BOOL, [ctypes.c_void_p]),
                     (gdi32.DeleteDC, wintypes.BOOL, [ctypes.c_void_p]), (user32.PrintWindow, wintypes.BOOL, [wintypes.HWND, ctypes.c_void_p, wintypes.UINT]),
                     (gdi32.GetDIBits, ctypes.c_int, [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT])]:
    f.restype, f.argtypes = res, argt  # handles are pointers: ctypes' default int would cut them to 32 bits


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG), ('biHeight', wintypes.LONG), ('biPlanes', wintypes.WORD),
                ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD), ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD), ('biClrImportant', wintypes.DWORD)]


written, failed = [], []


def handle():
    """slate's window in this process, by its title, as bench.py finds it."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(h, _):
        pid, title = wintypes.DWORD(), ctypes.create_unicode_buffer(64)
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        user32.GetWindowTextW(h, title, 64)
        if pid.value == os.getpid() and user32.IsWindowVisible(h) and title.value == 'slate':
            found.append(h)
        return True
    user32.EnumWindows(each, 0)
    return found[0]


def capture():
    """The window's client area in physical pixels. PW_RENDERFULLCONTENT draws slate's own pixels, whatever covers it."""
    h = handle()
    r = wintypes.RECT()
    user32.GetClientRect(h, ctypes.byref(r))
    w, ht = r.right, r.bottom
    screen = user32.GetDC(None)
    dc, bmp = gdi32.CreateCompatibleDC(screen), gdi32.CreateCompatibleBitmap(screen, w, ht)
    old = gdi32.SelectObject(dc, bmp)
    user32.PrintWindow(h, dc, 3)  # PW_CLIENTONLY | PW_RENDERFULLCONTENT
    info = BITMAPINFOHEADER(biSize = ctypes.sizeof(BITMAPINFOHEADER), biWidth = w, biHeight = -ht, biPlanes = 1, biBitCount = 32)  # top-down rows
    pixels = ctypes.create_string_buffer(w * ht * 4)
    gdi32.GetDIBits(dc, bmp, 0, ht, pixels, ctypes.byref(info), 0)
    gdi32.SelectObject(dc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(dc)
    user32.ReleaseDC(None, screen)
    return Image.frombuffer('RGB', (w, ht), pixels, 'raw', 'BGRX', 0, 1)


def shoot(window, name, region):
    """Save the part of the window that region (a JS expression for {x, y, w, h} in CSS pixels) gives."""
    time.sleep(0.6)  # for fades and the frame after them
    box = json.loads(js(window, f'JSON.stringify({region})') or 'null')
    if not box:
        failed.append(f'{name}: no region')
        return
    ratio = float(js(window, 'devicePixelRatio'))
    image = capture()
    left, top = max(0, round(box['x'] * ratio)), max(0, round(box['y'] * ratio))
    crop = image.crop((left, top, min(image.width, round((box['x'] + box['w']) * ratio)), min(image.height, round((box['y'] + box['h']) * ratio))))
    zoom = min(2, ratio) / ratio
    crop = crop.resize((round(crop.width * zoom), round(crop.height * zoom)), Image.LANCZOS)
    path = OUT / f'{name}.png'
    crop.save(path, optimize = True)
    written.append(f'{path} {crop.width}x{crop.height} {path.stat().st_size // 1024} KB')


def on_top(window, value):
    """TopMost through Invoke, as in bench.py: set from this thread it can deadlock with the GUI thread over the GIL."""
    from System import Func, Type  # pythonnet, loaded by pywebview by now
    form = window.native
    form.Invoke(Func[Type](lambda: setattr(form, 'TopMost', value)))


def js(window, code, wait = 60):
    done, box = threading.Event(), {}
    code = f'(async () => ({code.strip()}))()'  # pywebview calls back only for promises; a plain value never arrives
    threading.Thread(target = window.evaluate_js, args = (code,), kwargs = {'callback': lambda r: (box.update(r = r), done.set())}, daemon = True).start()
    done.wait(wait)
    return box.get('r')


def until(window, condition, what, wait = 60):
    """The page's value of condition once it is truthy; after wait seconds, None and a failure."""
    end = time.time() + wait
    while time.time() < end:
        value = js(window, condition, wait = 10)
        if value:
            return value
        time.sleep(0.5)
    failed.append(f'no {what} in {wait} s')
    return None


# the union of the visible elements a selector finds, widened by pad, as {x, y, w, h}: for shoot
REGION = '''((sels, pad, widen) => {
  const rs = sels.flatMap((s) => [...document.querySelectorAll(s)]).map((e) => e.getBoundingClientRect()).filter((r) => r.width && r.height);
  if (!rs.length) return null;
  let x = Math.min(...rs.map((r) => r.left)) - pad, y = Math.min(...rs.map((r) => r.top)) - pad;
  let r = Math.max(...rs.map((r) => r.right)) + pad, b = Math.max(...rs.map((r) => r.bottom)) + pad;
  if (widen) { const m = document.querySelector(widen).getBoundingClientRect(); x = m.left; r = m.right; }
  return {x, y, w: r - x, h: b - y};
})'''
HOVER = '.monaco-editor .monaco-hover:not(.hidden)'
NO_HOVER = "(editor.getContribution('editor.contrib.contentHover')._hideWidgets(), 1)"  # private, but showHover on an open hover only focuses it


def probe(window):
    try:
        run(window)
    except Exception as e:
        failed.append(f'exception: {type(e).__name__}: {e}')
    finally:
        window.destroy()


def run(window):
    if not until(window, 'typeof S !== "undefined" && S.ready && !!S.active && document.fonts.status === "loaded" && 1', 'page'):
        return
    on_top(window, True)
    js(window, 'editor.updateOptions({hover: {above: false}}) || 1')  # below the line, so the hover covers no glycan above it
    if not until(window, "document.querySelectorAll('#editor .view-lines .glycan').length >= 5 && 1", 'glycan drawings'):  # glycowork takes 1-2 s to start
        return
    until(window, f"monaco.editor.getModelMarkers({{resource: S.active.model.uri}}).some((m) => m.startLineNumber === {REFERENCE_LINE}) && 1", 'glycan hint')
    markers = js(window, "JSON.stringify(monaco.editor.getModelMarkers({resource: S.active.model.uri}).map((m) => [m.startLineNumber, m.owner, m.message]))")
    print('markers', markers, flush = True)
    # the drawings, with the hover card of a sialyl-Lewis X glycan
    js(window, f"glycanCard({json.dumps(SLEX)}).then(() => (editor.setPosition({{lineNumber: {SLEX_LINE}, column: 12}}), editor.focus(), editor.trigger('shots', 'editor.action.showHover', {{}}), 1))")
    if until(window, f"!!document.querySelector('{HOVER} img') && document.querySelector('{HOVER}').innerText.includes('Motifs') && 1", 'glycan hover card', wait = 20):
        shoot(window, 'glycans', f"{REGION}(['#tabs', '{HOVER}'], 16, '#main')")
    # the hint for a glycan in another notation, with its quick fix
    js(window, NO_HOVER)
    js(window, f"(editor.setPosition({{lineNumber: {REFERENCE_LINE}, column: 16}}), editor.trigger('shots', 'editor.action.showHover', {{}}), 1)")
    if until(window, f"document.querySelector('{HOVER}') && document.querySelector('{HOVER}').innerText.includes('IUPAC-condensed') && 1", 'hint hover', wait = 20):
        shoot(window, 'glycan-check', f"{REGION}(['{HOVER}', '#editor .cursor'], 16, '#editor')")
    # glycowork's parameters and their comments while typing a call
    js(window, NO_HOVER)
    js(window, "(() => { const m = S.active.model, n = m.getLineCount(); m.pushEditOperations([], [{range: new monaco.Range(n, 1, n, 1), text: 'glycan_to_mass(best, sample_prep = '}], () => null);"
               " editor.setPosition({lineNumber: n, column: m.getLineMaxColumn(n)}); editor.revealLine(n); editor.focus(); editor.trigger('shots', 'editor.action.triggerParameterHints', {}); return 1; })()")
    if until(window, "!!document.querySelector('.parameter-hints-widget.visible') && 1", 'parameter hints', wait = 30):
        shoot(window, 'hints', f"{REGION}(['.parameter-hints-widget.visible', '#editor .cursor'], 16, '#editor')")
    js(window, 'S.active.model.undo() || 1')  # back to the saved text, so the tab is not marked changed
    # motif search over the script and glycowork, with the script's own match open
    js(window, "searchMotif('SialylLewisX') || 1")
    if until(window, "/hold it/.test($('search-status').textContent) && 1", 'motif search results', wait = 60):
        js(window, "(document.querySelector('.sr-line').click(), 1)")
        time.sleep(1)
        shoot(window, 'motif-search', "(() => { const s = $('sidebar').getBoundingClientRect(); return {x: 0, y: 0, w: s.right + 820, h: 560}; })()")
    # the console, after running the script's cells
    js(window, f"(activate(S.tabs.find((t) => t.path === {json.dumps(str(DEMO))})), toggleConsole(true, false), $('console-clear').click(), editor.setPosition({{lineNumber: 3, column: 1}}), 1)")
    for _ in range(3):
        js(window, 'runAtCursor(true) || 1')
        time.sleep(0.5)
    if until(window, "document.querySelectorAll('#console-out img').length >= 2 && !document.querySelector('#console-busy:not(.hidden)') && 1", 'console figures', wait = 90):
        js(window, "(editor.revealLineNearTop(16), $('console-out').scrollTop = 1e6, 1)")
        shoot(window, 'console', f"{REGION}(['#main'], 0, null)")


def start(*a, **kw):
    threading.Thread(target = probe, args = (webview.windows[0],), daemon = True).start()
    return original(*a, **kw)


original = webview.start
webview.start = start
sys.argv = sys.argv[:1]
from slate.app import Api, main
Api.set_background = lambda self, background: None  # this window never has keyboard focus, and its blur would lower WebView2's memory target

main()
print('\n'.join(written), flush = True)
if failed:
    print('failed:', '; '.join(failed), flush = True)
sys.exit(1 if failed else 0)
