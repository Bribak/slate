"""Measure slate in a real window: startup, scroll and typing frame times, RAM per process, and RAM while inactive.

python tools/bench.py LABEL [--flags "<chromium flags>"] [--options '<Monaco options as JSON>'] [--state '<state keys as JSON>'] [--line N] [--x 4300]
                            [--behind] [--rect x0,y0,x1,y1]

--behind keeps the window behind every other one, unfocused, with Chromium's occlusion detection off so it still draws:
for measuring while Daniel works on the same screen (never put a window in front of his). Frame times then miss what the
screen adds, so compare them only with other --behind runs. --rect places the window in physical pixels; a maximized
window at 250% is about 0,0,3840,2340, where the GPU memory cap matters (2026-10-25).

Runs an isolated session (%TEMP%\\slate-bench\\LABEL) with 8 tabs from glycowork, scrolls a 2,350-line file, types into a
scratch copy (nothing is saved), then fires the window's blur event and measures again after 15 s, as when you switch to
another app. With --line N it also times scrolling and typing at line N (line_scroll, line_typing), with what that line
shows on screen. Prints one JSON line and writes it to %TEMP%\\slate-bench\\LABEL.json. RAM is Task Manager's number (private
working set) per process role.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

T0 = time.time()
ap = argparse.ArgumentParser()
ap.add_argument('label')
ap.add_argument('--flags', default = '')
ap.add_argument('--options', default = '{}')
ap.add_argument('--x', type = int, default = None)
ap.add_argument('--state', default = '{}', help = 'state keys as JSON, e.g. \'{"glycan_drawings": false}\'')
ap.add_argument('--line', type = int, default = None, help = 'show this line of the front tab before measuring memory')
ap.add_argument('--behind', action = 'store_true', help = 'keep the window behind all others, unfocused, still drawing')
ap.add_argument('--rect', default = None, help = 'window rect in physical pixels, x0,y0,x1,y1')
args = ap.parse_args()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slate import state
from slate.app import LEAN

if args.behind:
    # one --disable-features only: Chromium keeps the last of a repeated switch
    flags, disabled = [], ['CalculateNativeWinOcclusion']
    for f in (args.flags or LEAN).split():
        if f.startswith('--disable-features='):
            disabled += f.split('=', 1)[1].split(',')
        else:
            flags.append(f)
    args.flags = ' '.join(flags + ['--disable-backgrounding-occluded-windows', '--disable-features=' + ','.join(disabled)])
if args.flags:
    os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = args.flags

OUT = Path(tempfile.gettempdir()) / 'slate-bench'
home = OUT / args.label
home.mkdir(parents = True, exist_ok = True)
state.STATE_FILE = home / 'state.json'
G = Path.home() / 'Documents' / 'glycowork' / 'glycowork'
tabs = [G / 'motif' / f for f in ('processing.py', 'analysis.py', 'draw.py', 'graph.py')] + [G / 'glycan_data' / 'loader.py', G / 'network' / 'biosynthesis.py', G / 'ml' / 'processing.py', G / 'motif' / 'annotate.py']
tabs = [str(t) for t in tabs if t.is_file()]
placement = {'rect': [args.x, 120, args.x + 1400, 120 + 900], 'maximized': False} if args.x is not None else None
if args.rect:
    placement = {'rect': [int(v) for v in args.rect.split(',')], 'maximized': False}
state.save({'roots': [str(G.parent)], 'tabs': [{'path': t} for t in tabs], 'active': tabs[0], 'autosave': False, 'consoleOpen': True,
            **({'placement': placement} if placement else {}), **json.loads(args.state)})
import webview

MEMORY = r"""
param([int]$Root)
function Get-Tree($id) { foreach ($k in Get-CimInstance Win32_Process -Filter "ParentProcessId=$id") { $k; Get-Tree $k.ProcessId } }
$all = @(Get-CimInstance Win32_Process -Filter "ProcessId=$Root") + @(Get-Tree $Root)
$perf = @{}; $commit = @{}
foreach ($x in Get-CimInstance Win32_PerfFormattedData_PerfProc_Process) { $perf[[int]$x.IDProcess] = [double]$x.WorkingSetPrivate; $commit[[int]$x.IDProcess] = [double]$x.PrivateBytes }
$gpu = @{}
foreach ($x in Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUProcessMemory) { if ($x.Name -match '^pid_(\d+)_') { $gpu[[int]$Matches[1]] += [double]$x.SharedUsage + [double]$x.DedicatedUsage } }
$out = @{}; $com = @{}; $shared = @{}
foreach ($c in $all) {
  if ($c.Name -eq 'powershell.exe' -or $c.Name -eq 'conhost.exe') { continue }  # this measurement itself
  $role = if ($c.CommandLine -match '--type=(\S+)') { $Matches[1] } elseif ($c.Name -eq 'msedgewebview2.exe') { 'browser' } elseif ($c.ProcessId -eq $Root) { 'slate' } else { $c.Name }
  $out[$role] = [math]::Round($out[$role] + $perf[[int]$c.ProcessId] / 1MB)
  $com[$role] = [math]::Round($com[$role] + $commit[[int]$c.ProcessId] / 1MB)
  if ($gpu[[int]$c.ProcessId]) { $shared[$role] = [math]::Round($shared[$role] + $gpu[[int]$c.ProcessId] / 1MB) }
}
@{ws = $out; commit = $com; gpu = $shared} | ConvertTo-Json -Compress
"""
PROBE = r"""
(async () => {
  const frame = () => new Promise((r) => requestAnimationFrame(r));
  const stats = (a) => { const s = [...a].sort((x, y) => x - y); return {mean: +(a.reduce((x, y) => x + y, 0) / a.length).toFixed(1), p95: +s[Math.floor(s.length * 0.95)].toFixed(1)}; };
  editor.updateOptions(OPTIONS);
  const keep = editor.getModel();
  const scratch = monaco.editor.createModel(keep.getValue(), keep.getLanguageId(), monaco.Uri.parse('inmemory://bench/scratch' + (keep.uri.path.match(/\.[^./]+$/) || [''])[0]));  // a table stays a table
  editor.setModel(scratch);
  for (let i = 0; i < 5; i++) await frame();
  const scroll = []; let last = performance.now();
  for (let i = 0; i < 300; i++) { editor.setScrollTop(i * 40); await frame(); const now = performance.now(); scroll.push(now - last); last = now; }
  editor.setScrollTop(3000); await frame();
  editor.setPosition({lineNumber: editor.getVisibleRanges()[0].startLineNumber + 8, column: 1}); editor.focus();
  const typing = [];
  for (let i = 0; i < 80; i++) { const t0 = performance.now(); editor.trigger('bench', 'type', {text: i % 10 === 9 ? '\n' : 'x'}); await frame(); await frame(); typing.push(performance.now() - t0); }
  editor.setModel(keep); scratch.dispose();
  for (const t of S.tabs) activate(t);
  activate(S.tabs[0]);
  return JSON.stringify({scroll: stats(scroll), typing: stats(typing)});
})()
"""
CAPTURE = r"""
param([int]$Root, [string]$Out)
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System; using System.Runtime.InteropServices; using System.Text;
public static class BenchCap {
  public delegate bool EnumProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr p);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr c);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  public static IntPtr Find(uint want) {
    IntPtr found = IntPtr.Zero;
    EnumWindows((h, p) => { uint pid; GetWindowThreadProcessId(h, out pid); var sb = new StringBuilder(64); GetWindowText(h, sb, 64);
      if (pid == want && IsWindowVisible(h) && sb.ToString() == "slate") found = h; return true; }, IntPtr.Zero);
    return found;
  }
}
'@
[BenchCap]::SetThreadDpiAwarenessContext([IntPtr](-4)) | Out-Null
$h = [BenchCap]::Find([uint32]$Root)
$r = New-Object BenchCap+RECT; [BenchCap]::GetWindowRect($h, [ref]$r) | Out-Null
$bmp = New-Object Drawing.Bitmap ($r.R - $r.L), ($r.B - $r.T)
$g = [Drawing.Graphics]::FromImage($bmp); $dc = $g.GetHdc()
[BenchCap]::PrintWindow($h, $dc, 2) | Out-Null  # PW_RENDERFULLCONTENT: slate's own pixels, whatever covers it on screen
$g.ReleaseHdc($dc)
$bmp.Save($Out, [Drawing.Imaging.ImageFormat]::Png)
"""
# With --line: scrolling and typing where that line is, with what it shows (glycan drawings) on screen. The probe above
# cannot see those: it scrolls a fresh copy 600 lines from where anything was drawn, faster than drawings are asked for.
LINE_PROBE = r"""
(async () => {
  const frame = () => new Promise((r) => requestAnimationFrame(r));
  const stats = (a) => { const s = [...a].sort((x, y) => x - y); return {mean: +(a.reduce((x, y) => x + y, 0) / a.length).toFixed(1), p95: +s[Math.floor(s.length * 0.95)].toFixed(1)}; };
  const keep = editor.getModel(), view = editor.saveViewState(), top = editor.getScrollTop();
  const scroll = []; let last = performance.now();
  for (let i = 0; i < 120; i++) { editor.setScrollTop(top + (i % 60 < 30 ? i % 60 : 60 - i % 60) * 20); await frame(); const now = performance.now(); scroll.push(now - last); last = now; }
  const scratch = monaco.editor.createModel(keep.getValue(), keep.getLanguageId(), monaco.Uri.parse('inmemory://bench/line' + (keep.uri.path.match(/\.[^./]+$/) || [''])[0]));
  editor.setModel(scratch); editor.restoreViewState(view);
  await new Promise((r) => setTimeout(r, 1500));  // its drawings come back from the page's cache
  editor.setPosition({lineNumber: editor.getVisibleRanges()[0].startLineNumber + 8, column: 1}); editor.focus();
  const typing = [];
  for (let i = 0; i < 80; i++) { const t0 = performance.now(); editor.trigger('bench', 'type', {text: i % 10 === 9 ? '\n' : 'x'}); await frame(); await frame(); typing.push(performance.now() - t0); }
  editor.setModel(keep); editor.restoreViewState(view); scratch.dispose();
  return JSON.stringify({line_scroll: stats(scroll), line_typing: stats(typing)});
})()
"""
RESUME = r"""
(async () => {
  window.dispatchEvent(new Event('focus'));
  const t0 = performance.now();
  editor.setScrollTop(editor.getScrollTop() + 200);
  await new Promise((r) => requestAnimationFrame(r)); await new Promise((r) => requestAnimationFrame(r));
  return performance.now() - t0;
})()
"""


def on_top(window, value):
    """pywebview's window.on_top sets TopMost from the calling thread, which keeps the GIL while it waits for the GUI thread:
    a deadlock whenever the GUI thread is waiting for the GIL to handle a message from the page. Invoke releases it."""
    if args.behind:
        return
    from System import Func, Type  # pythonnet, loaded by pywebview by now
    form = window.native
    form.Invoke(Func[Type](lambda: setattr(form, 'TopMost', value)))


def js(window, code, wait = 120):
    done, box = threading.Event(), {}
    code = f'(async () => ({code.strip()}))()'  # pywebview calls back only for promises; a plain value never arrives
    threading.Thread(target = window.evaluate_js, args = (code,), kwargs = {'callback': lambda r: (box.update(r = r), done.set())}, daemon = True).start()
    done.wait(wait)
    return box.get('r')


def memory():
    out = subprocess.run(['powershell', '-NoProfile', '-Command', f'& {{ {MEMORY} }} {os.getpid()}'], capture_output = True, text = True, creationflags = subprocess.CREATE_NO_WINDOW).stdout.strip()
    parts = json.loads(out or '{}')
    ws, commit, gpu = parts.get('ws', {}), parts.get('commit', {}), parts.get('gpu', {})
    # commit (private bytes) next to Task Manager's number: Windows trims working sets when RAM runs short (2026-10-25, with
    # 0.6 GB free every process read half its size), which commit does not see. gpu: GPU memory per process (shared +
    # dedicated), Task Manager's GPU columns; on integrated graphics that is system RAM too, and where tiles live
    return {'total': sum(ws.values()), **ws, 'commit': {'total': sum(commit.values()), **commit}, 'gpu': gpu}


def log(msg):
    with open(OUT / f'{args.label}.log', 'a', encoding = 'utf-8') as f:
        f.write(f'{time.time() - T0:7.1f}s {msg}\n')


def need(value, step):
    if value is None:  # Chromium draws no frames for a window that is covered, minimised or on a sleeping screen, so a probe waiting on requestAnimationFrame never answers
        raise RuntimeError(f'{step}: no answer from the page in 120 s; keep the bench window visible and the screen on')
    return value


def probe(window):
    try:
        measure(window)
    except Exception as e:
        log(f'failed: {e}')
        print(f'bench failed: {e}', file = sys.stderr, flush = True)
    finally:
        window.destroy()  # also on failure: a window left open keeps WebView2 running, and its locked profile stops the next run with this label


def measure(window):
    window.events.loaded.wait(60)
    log('page loaded')
    while True:
        time.sleep(1)
        state_now = js(window, 'JSON.stringify([typeof S !== "undefined", typeof S !== "undefined" && !!S.active, document.fonts.status])', wait = 5)
        log(f'ready check {state_now}')
        if state_now and json.loads(state_now)[:2] == [True, True] and json.loads(state_now)[2] == 'loaded':
            break
    result = {'label': args.label, 'flags': args.flags, 'options': json.loads(args.options), 'ready_s': round(time.time() - T0, 2)}
    time.sleep(3)
    on_top(window, True)
    log('probe start')
    result.update(json.loads(need(js(window, PROBE.replace('OPTIONS', args.options)), 'scroll and typing probe')))
    log('probe done')
    if args.line:
        js(window, f'editor.revealLineNearTop({args.line}) || 1')
        time.sleep(3)  # for what that line shows, such as glycan drawings, whose process takes 2 s to start
    time.sleep(3)
    result['ram_active'] = memory()
    subprocess.run(['powershell', '-NoProfile', '-Command', f'& {{ {CAPTURE} }} {os.getpid()} "{OUT / (args.label + ".png")}"'], capture_output = True, creationflags = subprocess.CREATE_NO_WINDOW)
    if args.line:
        result.update(json.loads(need(js(window, LINE_PROBE), 'probe at --line')))
    on_top(window, False)
    BACKGROUND.set()
    js(window, "window.dispatchEvent(new Event('blur')) && 1")
    time.sleep(15)
    result['ram_inactive'] = memory()
    log('memory done')
    on_top(window, True)  # coming back to slate brings it forward, and a covered window would draw no frame to time
    resume = js(window, RESUME)  # a synthetic focus event does not always get a frame (see need), and the rest is measured by now
    result['resume_ms'] = round(resume, 1) if resume is not None else None
    if resume is None:
        log('resume probe: no frame in 120 s')
    line = json.dumps(result)
    print(line, flush = True)
    (OUT / f'{args.label}.json').write_text(line)


orig_start = webview.start


def start(*a, **kw):
    threading.Thread(target = probe, args = (webview.windows[0],), daemon = True).start()
    return orig_start(*a, **kw)


def to_back():
    """Send this process's window behind all others, without activating it, while it starts up."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(h, _):
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value == os.getpid() and u.IsWindowVisible(h):
            found.append(h)
        return True

    for _ in range(200):
        found.clear()
        u.EnumWindows(each, 0)
        for h in found:
            u.SetWindowPos(h, wintypes.HWND(1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # HWND_BOTTOM; no move, size or activation
        time.sleep(0.05)


webview.start = start
if args.behind:
    create_window = webview.create_window
    webview.create_window = lambda *a, **kw: create_window(*a, **{**kw, 'focus': False})
    threading.Thread(target = to_back, daemon = True).start()
sys.argv = sys.argv[:1]
from slate.app import Api, main
BACKGROUND = threading.Event()
real_set_background = Api.set_background
Api.set_background = lambda self, background: real_set_background(self, background) if BACKGROUND.is_set() else None  # the bench window never gets keyboard focus, so ignore blur until the background phase
main()
