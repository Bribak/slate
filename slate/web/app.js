// slate's page: tabs, folder tree, finder, find-in-files and the console around one Monaco editor.
// Python does the disk, style, jedi and interpreter work behind window.pywebview.api (slate/app.py).
'use strict';

const $ = (id) => document.getElementById(id);
const ROOT_COLORS = ['#4A88C7', '#CC7832', '#6A8759', '#9876AA', '#BBB529', '#E06C75', '#56B6C2', '#D19A66'];
const FONT = "'slate mono', 'JetBrains Mono', 'Cascadia Mono', Consolas, Menlo, monospace";  // 'slate mono' = PyCharm's own JetBrains Mono files, see loadFonts
const S = {
  roots: [], tabs: [], active: null, mru: [], expanded: new Set(), closed: [], history: [],
  view: 'files', sidebarWidth: 260, sidebarOpen: true, consoleHeight: 240, consoleOpen: false, autosave: true,
  ready: false,  // the saved session is restored
  sep: '\\', mac: false,  // the platform's path separator, and whether this is macOS (Api.get_state)
  pathOf: new Map(),  // model uri -> real path, since Monaco's uris lower-case the drive letter
};
let api, monaco, editor, consoleInput;

const keyOf = (p) => p.replace(/\//g, '\\').replace(/\\+$/, '').toLowerCase();
const basename = (p) => p.replace(/[\\/]+$/, '').split(/[\\/]/).pop();
const dirname = (p) => p.replace(/[\\/][^\\/]*$/, '');
const join = (a, b) => a.replace(/[\\/]+$/, '') + S.sep + b;
const nativeSep = (p) => (S.sep === '\\' ? p.replace(/\//g, '\\') : p);  // a path as the platform writes it: C:\a\b, /Users/a/b
// Key names in slate's own hints as a Mac writes them: Cmd for Ctrl (see comboOf), except Ctrl+` and Ctrl+Tab, which stay Ctrl
const keyText = (s) => (S.mac ? s.replace(/Ctrl\+(`|Tab)/g, '⌃$1').replace(/Ctrl\+/g, '⌘').replace(/Alt\+/g, '⌥').replace(/Shift\+/g, '⇧') : s);
const errMsg = (e) => String((e && e.message) || e).replace(/^\w*Error: /, '');
const escapeHtml = (s) => s.replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function toast(msg, kind = 'error') {
  const t = $('toast');
  t.textContent = msg;
  t.className = kind === 'info' ? 'info' : '';
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.add('hidden'), kind === 'info' ? 2500 : 7000);
}

function rootIndexOf(path) {
  const k = keyOf(path);
  let best = -1, len = 0;
  S.roots.forEach((r, i) => {
    const rk = keyOf(r);
    if ((k === rk || k.startsWith(rk + '\\')) && rk.length > len) { best = i; len = rk.length; }
  });
  return best;
}

function relPath(path) {
  const i = rootIndexOf(path);
  return i < 0 ? path : basename(S.roots[i]) + path.slice(S.roots[i].replace(/[\\/]+$/, '').length);
}

const rootColor = (i) => (i < 0 ? '#606366' : ROOT_COLORS[i % ROOT_COLORS.length]);

// ---------------------------------------------------------------- usage log (slate/usage.py: python -m slate.usage)

const usage = {calls: {}, keys: {}, long: [0, 0, 0], events: [], seen: new Set()};

function note(kind, fields) {
  const key = kind + fields.call + fields.message;
  if (usage.seen.has(key)) return;  // each distinct error once per session; the call counts keep the totals
  usage.seen.add(key);
  usage.events.push({kind, ...fields, stack: String(fields.stack || '').slice(0, 2000)});
}

// The API with every call timed: count, total ms, max ms and failures per method, plus each call slower than a second.
function track(raw) {
  return new Proxy(raw, {get(target, name) {
    const fn = target[name];
    if (typeof fn !== 'function' || name === 'log_usage') return fn;
    return (...args) => {
      const t0 = performance.now();
      const done = (e) => {
        const ms = performance.now() - t0;
        const c = usage.calls[name] || (usage.calls[name] = [0, 0, 0, 0]);
        c[0]++; c[1] += ms; c[2] = Math.max(c[2], ms);
        if (e) { c[3]++; note('api_error', {call: name, message: String(e.message || e).slice(0, 300), stack: e.stack}); }
        if (ms > 1000 && name !== 'pick_folder') usage.events.push({kind: 'slow', call: name, ms: Math.round(ms)});
      };
      return Promise.resolve(fn.apply(target, args)).then((r) => { done(); return r; }, (e) => { done(e); throw e; });
    };
  }});
}

function flushUsage() {
  if (!api || !(Object.keys(usage.calls).length || Object.keys(usage.keys).length || usage.long[0] || usage.events.length)) return;
  const calls = {};
  for (const [name, [n, total, worst, errors]] of Object.entries(usage.calls)) calls[name] = [n, Math.round(total), Math.round(worst), errors];
  const batch = [{kind: 'activity', calls, keys: usage.keys, long: usage.long.map(Math.round)}, ...usage.events];
  Object.assign(usage, {calls: {}, keys: {}, long: [0, 0, 0], events: []});
  api.log_usage(batch).catch(() => {});
}

addEventListener('error', (e) => note('js_error', {message: String(e.message).slice(0, 300), where: `${e.filename}:${e.lineno}:${e.colno}`, stack: e.error && e.error.stack}));
// Monaco rejects with 'Canceled' whenever it drops work it no longer needs, which is not an error
addEventListener('unhandledrejection', (e) => (e.reason && e.reason.name === 'Canceled') || note('js_error', {message: String((e.reason && e.reason.message) || e.reason).slice(0, 300), stack: e.reason && e.reason.stack}));
try {
  new PerformanceObserver((list) => {
    for (const t of list.getEntries()) {
      usage.long[0]++;  // tasks over 50 ms on the page's thread, which delay keystrokes and frames
      if (t.duration > 200) usage.long[1]++;
      usage.long[2] = Math.max(usage.long[2], t.duration);
    }
  }).observe({type: 'longtask'});
} catch (e) { /* no long-task timing in this browser */ }

// ---------------------------------------------------------------- boot

// Monaco needs nothing from Python, so it loads while pywebview connects its bridge, and the fonts load alongside it
window.MonacoEnvironment = {getWorkerUrl() {
  const base = new URL('vendor/', location.href).href;
  const code = `self.MonacoEnvironment = {baseUrl: '${base}'}; importScripts('${base}vs/base/worker/workerMain.js');`;
  if (/Macintosh/.test(navigator.userAgent)) return URL.createObjectURL(new Blob([code], {type: 'text/javascript'}));  // WebKit runs no data: workers
  return 'data:text/javascript;charset=utf-8,' + encodeURIComponent(code);
}};
require.config({paths: {vs: 'vendor/vs'}});
const monacoLoaded = new Promise((resolve) => require(['vs/editor/editor.main'], resolve));

function boot() {
  if (boot.done) return;
  boot.done = true;
  api = track(window.pywebview.api);
  Promise.all([api.get_state(), monacoLoaded, loadFonts()]).then(([info]) => {
    S.python = info.python;
    S.sep = info.sep || '\\';
    S.mac = !!info.mac;
    S.home = info.home;
    monaco = window.monaco;
    init(info.state || {});
  });
}

if (window.pywebview && window.pywebview.api && window.pywebview.api.get_state) boot();
else window.addEventListener('pywebviewready', boot);

// Load the JetBrains Mono that PyCharm ships (Python finds it) under the name 'slate mono', before the editor measures the font.
async function loadFonts() {
  let files = [];
  try { files = await api.editor_fonts(); } catch (e) { return; }
  await Promise.all(files.map((f) => {
    const bytes = Uint8Array.from(atob(f.data), (c) => c.charCodeAt(0));
    const face = new FontFace('slate mono', bytes, {style: f.style, weight: f.weight});
    document.fonts.add(face);
    return face.load();
  }));
}

async function init(saved) {
  registerSlatePython(monaco);
  editor = monaco.editor.create($('editor'), {
    model: null, theme: 'darcula', fontFamily: FONT, fontSize: 13, lineHeight: 20,  // PyCharm's defaults: size 13, line spacing 1.2
    automaticLayout: true, minimap: {enabled: false}, scrollBeyondLastLine: false, bracketPairColorization: {enabled: false},
    guides: {indentation: true, bracketPairs: false}, mouseWheelZoom: true,
    stickyScroll: {enabled: true, defaultModel: 'indentationModel'},  // as before the outline provider: it would show only def and class lines, and ask Python on every edit
    renderWhitespace: 'selection', fixedOverflowWidgets: true, unicodeHighlight: {ambiguousCharacters: false},
    wordBasedSuggestions: 'allDocuments', definitionLinkOpensInPeek: false, 'semanticHighlighting.enabled': false,
  });
  registerProviders();
  registerGlycanFixes();
  setupRunning();
  setupGlycans(saved);
  editor.onDidChangeCursorSelection(updateStatusPos);
  editor.onDidChangeModelContent(() => persist());
  S.roots = saved.roots || [];
  if (!saved.roots) {  // first run: the folders slate was built around, when they exist
    for (const name of ['glycowork', 'candycrunch', 'CandyCrunch']) {  // a Mac's paths can be case-sensitive, and the repository is CandyCrunch
      const p = join(join(S.home, 'Documents'), name);
      if (S.roots.some((r) => keyOf(r) === keyOf(p))) continue;
      try { await api.list_dir(p); S.roots.push(p); S.expanded.add(keyOf(p)); } catch (e) { /* not on this machine */ }
    }
  }
  for (const k of saved.expanded || []) S.expanded.add(k);
  S.history = saved.history || [];
  S.closed = saved.closed || [];
  S.view = saved.view || 'files';
  S.sidebarWidth = saved.sidebarWidth || 260;
  S.sidebarOpen = saved.sidebarOpen !== false;
  S.consoleHeight = saved.consoleHeight || 240;
  S.autosave = saved.autosave !== false;
  bindUi();
  if (S.mac) macLabels();
  setupConsole();
  layout();
  // files double-clicked in Explorer come first, so the one asked for shows before the rest of the session is back
  const savedTabs = saved.tabs || [];
  const launched = await api.page_ready();
  if (launched.length) {
    await openPaths(launched, new Map(savedTabs.map((t) => [keyOf(t.path), t.viewState])));
    S.launchedMs = Math.round(performance.now());
  }
  await loadExtensions();  // before showView, which may restore a view an extension adds
  showView(S.view);
  renderTree();
  const reads = savedTabs.map((t) => {  // all at once, since each is a round trip over the bridge; openFile reports failures
    const read = api.read_file(t.path);
    read.catch(() => {});
    return read;
  });
  for (const [i, t] of savedTabs.entries()) await openFile(t.path, {background: true, viewState: t.viewState, quiet: true, file: reads[i]});
  const rank = new Map(savedTabs.map((t, i) => [keyOf(t.path), i]));
  const rankOf = (tab) => (rank.has(tab.key) ? rank.get(tab.key) : rank.size);
  S.tabs.sort((a, b) => rankOf(a) - rankOf(b));  // the saved order, then the launched files, as before
  S.mru = [...S.mru, ...(saved.mru || []).filter((k) => !S.mru.includes(k) && S.tabs.some((t) => t.key === k))];
  const active = saved.active && S.tabs.find((t) => t.key === keyOf(saved.active));
  if (!S.active && (active || S.tabs.length)) activate(active || S.tabs[0]);
  else renderTabs();
  if (saved.consoleOpen) toggleConsole(true);
  updateStatus();
  S.ready = true;
  persist();
  drainLints();  // the front tab's check, started by activate, goes first
  setInterval(checkDisk, 2000);
  setInterval(flushUsage, 60000);
  setTimeout(checkUpdate, 5000);  // well after startup; Python asks GitHub on a thread of its own
  usage.events.push({kind: 'ready', ms: Math.round(performance.now()), tabs: S.tabs.length, roots: S.roots.length, dpr: devicePixelRatio,
    launched: launched.length, file_ms: S.launchedMs});
  addEventListener('resize', debounce(() => {
    const box = $('editor'), info = editor.getLayoutInfo();
    usage.events.push({kind: 'resize', size: [innerWidth, innerHeight], dpr: devicePixelRatio, editor: [info.width, info.height], box: [box.clientWidth, box.clientHeight]});
  }, 1000));
  window.addEventListener('focus', () => { api.set_background(false); checkDisk(); renderTree(); emit('focus'); });
  window.addEventListener('blur', () => { api.set_background(true); flushUsage(); if (S.autosave) S.tabs.filter((t) => isDirty(t) && !t.deleted && !t.conflict).forEach((t) => save(t, true)); });
}

// ---------------------------------------------------------------- tabs and files

function languageFor(path) {
  const ext = (path.match(/\.[^.\\/]+$/) || [''])[0].toLowerCase();
  if (['.py', '.pyw', '.pyi'].includes(ext)) return 'py';
  if (['.toml', '.cfg', '.ini', '.conf'].includes(ext)) return 'ini';
  const lang = monaco.languages.getLanguages().find((l) => l.id !== 'python' && (l.extensions || []).includes(ext));
  return lang ? lang.id : 'plaintext';
}

function modelFor(path, text) {
  const uri = monaco.Uri.file(path);
  S.pathOf.set(uri.toString(), path);
  let model = monaco.editor.getModel(uri);
  if (model) {
    if (model.getValue(monaco.editor.EndOfLinePreference.LF) !== text.replace(/\r\n/g, '\n')) model.setValue(text);
  } else {
    model = monaco.editor.createModel(text, languageFor(path), uri);
  }
  return model;
}

const isDirty = (tab) => tab.model.getAlternativeVersionId() !== tab.savedVersion;

async function openFile(path, opts = {}) {
  let tab = S.tabs.find((t) => t.key === keyOf(path));
  if (!tab) {
    let file;
    try {
      file = await (opts.file || api.read_file(path));
    } catch (e) {
      if (!opts.quiet) toast(errMsg(e));
      return null;
    }
    tab = S.tabs.find((t) => t.key === keyOf(file.path));  // opened by another call while this one waited
    if (!tab) {
      const model = modelFor(file.path, file.text);
      tab = {path: file.path, key: keyOf(file.path), model, savedVersion: model.getAlternativeVersionId(),
        mtime: file.mtime, encoding: file.encoding, viewState: opts.viewState || null, markers: []};
      model.onDidChangeContent(() => onEdit(tab));
      const at = S.active && !opts.background ? S.tabs.indexOf(S.active) + 1 : S.tabs.length;
      S.tabs.splice(at, 0, tab);
      if (model.getLanguageId() === 'py') { lintLater(tab); markCells(tab); }
    }
  }
  if (opts.background) return tab;
  withoutNoting(() => {  // one place for Back, where it lands, not where the tab was first
    activate(tab);
    if (opts.line) revealAt(opts.line, opts.col || 1, opts.endCol);
  });
  return tab;
}

// Files from Explorer open as tabs (the last one in front); folders become roots in the tree.
async function openPaths(items, viewStates = new Map()) {
  let last = null;
  for (const it of items) {
    if (!it.dir) { last = (await openFile(it.path, {background: true, viewState: viewStates.get(keyOf(it.path))})) || last; continue; }
    if (S.roots.some((r) => keyOf(r) === keyOf(it.path))) continue;
    S.roots.push(it.path);
    S.expanded.add(keyOf(it.path));
    finderCache = null;
    await renderTree();
  }
  if (last) activate(last);
  else renderTabs();
  persist();
}

function revealAt(line, col, endCol) {
  const sel = new monaco.Selection(line, endCol || col, line, col);
  editor.setSelection(sel);
  editor.revealRangeInCenterIfOutsideViewport(sel);
  editor.focus();
}

function activate(tab, {mru = true} = {}) {
  const switching = S.active !== tab || editor.getModel() !== tab.model;  // the tab in front keeps its cursor: its viewState is from when it was last left
  if (S.active && S.active !== tab && !S.active.model.isDisposed()) S.active.viewState = editor.saveViewState();
  S.active = tab;
  if (mru) S.mru = [tab.key, ...S.mru.filter((k) => k !== tab.key)];
  if (switching) {
    withoutNoting(() => {  // the new model's first cursor and its restored one are one arrival, not two jumps
      editor.setModel(tab.model);
      if (tab.viewState) editor.restoreViewState(tab.viewState);
    });
  }
  if (!tab.linted && tab.model.getLanguageId() === 'py') lint(tab);  // ahead of the background tabs still queued
  else if (tab.model.getLanguageId() === 'py') checkGlycans(tab);  // linted in the background, when glycan diagnostics wait for the front tab
  editor.focus();
  $('empty').classList.add('hidden');
  renderTabs();
  renderBanner();
  updateStatus();
  markTreeActive();
  persist();
}

function onEdit(tab) {
  updateTabEl(tab);
  syncDirty();
  if (tab.model.getLanguageId() === 'py') { lintSoon(tab); markCellsSoon(tab); }
}

let lastDirty = false;
function syncDirty() {
  const dirty = S.tabs.some(isDirty);
  if (dirty !== lastDirty) { lastDirty = dirty; api.set_dirty(dirty); }
}

async function save(tab = S.active, quiet = false) {
  if (!tab) return;
  const version = tab.model.getAlternativeVersionId();
  const text = tab.model.getValue(monaco.editor.EndOfLinePreference.TextDefined);
  try {
    tab.mtime = await api.write_file(tab.path, text, tab.encoding);
  } catch (e) {
    if (!quiet) toast(`Could not save ${basename(tab.path)}: ${errMsg(e)}`);
    return;
  }
  tab.savedVersion = version;
  tab.deleted = false;
  tab.conflict = null;
  updateTabEl(tab);
  renderBanner();
  syncDirty();
  emit('saved', tab.path);
}

function closeTab(tab, {force = false} = {}) {
  if (!tab) return;
  if (!force && isDirty(tab) && !confirm(`${basename(tab.path)} has unsaved changes. Close it anyway?`)) return;
  const i = S.tabs.indexOf(tab);
  S.tabs.splice(i, 1);
  S.mru = S.mru.filter((k) => k !== tab.key);
  S.closed = [...S.closed.filter((p) => keyOf(p) !== tab.key), tab.path].slice(-30);
  if (S.active === tab) {
    S.active = null;
    const next = S.tabs.find((t) => t.key === S.mru[0]) || S.tabs[Math.min(i, S.tabs.length - 1)];
    if (next) activate(next);
    else { editor.setModel(null); $('empty').classList.remove('hidden'); renderBanner(); updateStatus(); }
  }
  tab.model.dispose();
  syncDirty();
  renderTabs();
  persist();
}

function renderTabs() {
  const bar = $('tab-strip');
  bar.innerHTML = '';
  const names = {};
  for (const t of S.tabs) names[basename(t.path).toLowerCase()] = (names[basename(t.path).toLowerCase()] || 0) + 1;
  for (const tab of S.tabs) {
    const root = rootIndexOf(tab.path);
    const e = el('div', 'tab');
    e.title = tab.path;
    e.style.borderBottomColor = tab === S.active ? rootColor(root) : 'transparent';
    e.append(el('span', 'name', basename(tab.path)));
    if (names[basename(tab.path).toLowerCase()] > 1) e.append(el('span', 'proj', relPath(dirname(tab.path))));
    else if (root >= 0) e.append(el('span', 'proj', basename(S.roots[root])));
    const close = el('span', 'close');
    close.append(el('span', null, '✕'));
    close.addEventListener('click', (ev) => { ev.stopPropagation(); closeTab(tab); });
    e.append(close);
    e.addEventListener('mousedown', (ev) => { if (ev.button === 1) { ev.preventDefault(); closeTab(tab); } });
    e.addEventListener('click', () => activate(tab));
    e.addEventListener('contextmenu', (ev) => showMenu(ev, tabMenu(tab)));
    e.draggable = true;
    e.addEventListener('dragstart', (ev) => { ev.dataTransfer.setData('text/slate-tab', tab.key); });
    e.addEventListener('dragover', (ev) => ev.preventDefault());
    e.addEventListener('drop', (ev) => {
      const moved = S.tabs.find((t) => t.key === ev.dataTransfer.getData('text/slate-tab'));
      if (!moved || moved === tab) return;
      S.tabs.splice(S.tabs.indexOf(moved), 1);
      S.tabs.splice(S.tabs.indexOf(tab), 0, moved);
      renderTabs();
      persist();
    });
    tab.el = e;
    updateTabEl(tab);
    bar.append(e);
  }
  fitTabs();
}

// Tabs wrap to two rows at most, as PyCharm's do. With more, the strip gets the width that splits them into two rows most
// evenly, and #tabs scrolls both rows sideways together (the wheel too).
function fitTabs() {
  const strip = $('tab-strip'), tabs = [...strip.children];
  strip.style.width = '';
  if (new Set(tabs.map((e) => e.offsetTop)).size > 2) {
    const widths = tabs.map((e) => e.getBoundingClientRect().width), total = widths.reduce((a, b) => a + b, 0);
    let first = 0, width = total;
    for (const w of widths.slice(0, -1)) { first += w; width = Math.min(width, Math.max(first, total - first)); }
    strip.style.width = Math.ceil(width) + 1 + 'px';
  }
  if (S.active && S.active.el) S.active.el.scrollIntoView({block: 'nearest', inline: 'nearest'});
}

function tabMenu(tab) {
  const closeAll = (tabs) => tabs.forEach((t) => closeTab(t));  // each unsaved one still asks
  return [
    ['Close', () => closeTab(tab)],
    ['Close others', () => closeAll(S.tabs.filter((t) => t !== tab))],
    ['Close tabs to the right', () => closeAll(S.tabs.slice(S.tabs.indexOf(tab) + 1))],
    ['Close unchanged tabs', () => closeAll(S.tabs.filter((t) => !isDirty(t)))],
    ['Copy path', () => navigator.clipboard.writeText(tab.path)],
    ['Reveal in folder tree', () => { activate(tab); revealActiveInTree(); }],
    [S.mac ? 'Reveal in Finder' : 'Reveal in Explorer', () => api.reveal(tab.path)],
  ];
}

function updateTabEl(tab) {
  if (!tab.el) return;
  tab.el.classList.toggle('active', tab === S.active);
  tab.el.classList.toggle('dirty', isDirty(tab));
  tab.el.classList.toggle('deleted', !!tab.deleted);
  tab.el.classList.toggle('broken', (tab.markers || []).some((m) => m.severity === 'error'));  // a file Python cannot compile
}

function cycleTab(step) {
  if (S.tabs.length < 2 || !S.active) return;
  const i = (S.tabs.indexOf(S.active) + step + S.tabs.length) % S.tabs.length;
  activate(S.tabs[i]);
}

// Ctrl+Tab walks the most-recently-used list while Ctrl is held, and commits the choice on release.
let mruCycle = null;
function mruStep(step) {
  if (S.mru.length < 2) return;
  if (mruCycle === null) mruCycle = 0;
  mruCycle = (mruCycle + step + S.mru.length) % S.mru.length;
  const tab = S.tabs.find((t) => t.key === S.mru[mruCycle]);
  if (tab) activate(tab, {mru: false});
}

// ---------------------------------------------------------------- back and forward (PyCharm's Ctrl+Alt+Left/Right)

// Back returns to where a jump left from: a definition, a search or finder result, a traceback link, another tab, or a
// move of NAV_LINES or more that was not typing, arrow keys or a click (those only move the current place).
const nav = {back: [], forward: [], here: null, hold: false, moving: false};
const NAV_KEPT = 50, NAV_LINES = 10;

function notePlace(e) {
  const tab = S.active, pos = editor.getPosition();
  if (!tab || !pos || nav.hold || nav.moving || mruCycle !== null || editor.getModel() !== tab.model) return;
  const place = {path: tab.path, line: pos.lineNumber, col: pos.column}, h = nav.here;
  const far = h && Math.abs(h.line - place.line) >= NAV_LINES && !(e && (e.source === 'keyboard' || e.source === 'mouse'));
  if (S.ready && h && (keyOf(h.path) !== tab.key || far)) {
    nav.back = [...nav.back, h].slice(-NAV_KEPT);
    nav.forward = [];
  }
  nav.here = place;
}

// Runs fn as one move: the cursor changes inside it note nothing, and the place it ends at is noted once
function withoutNoting(fn) {
  const held = nav.hold;
  nav.hold = true;
  try { fn(); } finally { nav.hold = held; }
  if (!held) notePlace();
}

async function goPlace(step) {
  const [from, to] = step < 0 ? [nav.back, nav.forward] : [nav.forward, nav.back];
  const here = nav.here;
  while (from.length) {
    const place = from.pop();
    nav.moving = true;
    let tab;
    try { tab = await openFile(place.path, {line: place.line, col: place.col, quiet: true}); } finally { nav.moving = false; }
    if (!tab) continue;  // a file deleted since
    if (here) to.push(here);
    nav.here = place;
    return;
  }
}

// ---------------------------------------------------------------- files changed outside slate (Claude, git, other tools)

let checking = false;
async function checkDisk() {
  if (checking || !S.tabs.length) return;
  checking = true;
  try {
    const stats = await api.stat_files(S.tabs.map((t) => t.path));
    for (const tab of [...S.tabs]) {
      if (tab.model.isDisposed()) continue;
      const mtime = stats[tab.path];
      if (mtime === undefined) continue;
      if (mtime === null) {
        if (!tab.deleted) { tab.deleted = true; updateTabEl(tab); }
        continue;
      }
      if (tab.deleted) { tab.deleted = false; updateTabEl(tab); }
      if (mtime !== tab.mtime) await diskChanged(tab);
    }
  } catch (e) {
    console.error(e);
  } finally {
    checking = false;
  }
}

async function diskChanged(tab) {
  let file;
  try { file = await api.read_file(tab.path); } catch (e) { return; }
  tab.mtime = file.mtime;
  const disk = file.text.replace(/\r\n/g, '\n');
  if (disk === tab.model.getValue(monaco.editor.EndOfLinePreference.LF)) {
    tab.savedVersion = tab.model.getAlternativeVersionId();
    tab.conflict = null;
  } else if (!isDirty(tab)) {
    applyText(tab, file.text);
    tab.savedVersion = tab.model.getAlternativeVersionId();
  } else {
    tab.conflict = file.text;
  }
  updateTabEl(tab);
  renderBanner();
  syncDirty();
}

// The changed stretches between two versions of a file, as [oldStart, oldEnd, newStart, newEnd) line ranges.
// Common lines at both ends are trimmed first; the middle is diffed by LCS when small enough, else replaced whole.
function lineHunks(old, neu) {
  let p = 0;
  while (p < old.length && p < neu.length && old[p] === neu[p]) p++;
  let s = 0;
  while (s < old.length - p && s < neu.length - p && old[old.length - 1 - s] === neu[neu.length - 1 - s]) s++;
  const a = old.slice(p, old.length - s), b = neu.slice(p, neu.length - s);
  if (!a.length && !b.length) return [];
  if (a.length > 3000 || b.length > 3000) return [[p, p + a.length, p, p + b.length]];
  const n = a.length, m = b.length, w = m + 1;
  const lcs = new Uint16Array((n + 1) * w);
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i * w + j] = a[i] === b[j] ? lcs[(i + 1) * w + j + 1] + 1 : Math.max(lcs[(i + 1) * w + j], lcs[i * w + j + 1]);
    }
  }
  const hunks = [];
  let i = 0, j = 0, open = null;
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) {
      if (open) { hunks.push([p + open[0], p + i, p + open[1], p + j]); open = null; }
      i++; j++;
    } else {
      if (!open) open = [i, j];
      if (j >= m || (i < n && lcs[(i + 1) * w + j] >= lcs[i * w + j + 1])) i++;
      else j++;
    }
  }
  if (open) hunks.push([p + open[0], p + n, p + open[1], p + m]);
  return hunks;
}

// Edit only the stretches that differ, so the cursor, scroll position and undo history survive an outside edit.
function applyText(tab, text) {
  const model = tab.model;
  const eol = text.includes('\r\n') ? '\r\n' : '\n';
  model.setEOL(eol === '\r\n' ? monaco.editor.EndOfLineSequence.CRLF : monaco.editor.EndOfLineSequence.LF);
  const neu = text.split(/\r?\n/);
  const last = model.getLineCount();
  const edits = lineHunks(model.getLinesContent(), neu).map(([a0, a1, b0, b1]) => {
    const inserted = neu.slice(b0, b1);
    if (a1 > a0 && inserted.length) {
      return {range: new monaco.Range(a0 + 1, 1, a1, model.getLineMaxColumn(a1)), text: inserted.join(eol)};
    }
    if (a1 > a0) {  // lines removed
      const range = a1 < last ? new monaco.Range(a0 + 1, 1, a1 + 1, 1)
        : a0 > 0 ? new monaco.Range(a0, model.getLineMaxColumn(a0), last, model.getLineMaxColumn(last)) : model.getFullModelRange();
      return {range, text: ''};
    }
    if (a0 < last) return {range: new monaco.Range(a0 + 1, 1, a0 + 1, 1), text: inserted.join(eol) + eol};  // lines added
    return {range: new monaco.Range(last, model.getLineMaxColumn(last), last, model.getLineMaxColumn(last)), text: eol + inserted.join(eol)};
  });
  if (!edits.length) return;
  model.pushStackElement();
  model.pushEditOperations(editor.getModel() === model ? editor.getSelections() : [], edits, () => null);
  model.pushStackElement();
}

function renderBanner() {
  const b = $('banner');
  const tab = S.active;
  if (!tab || !tab.conflict) { b.classList.add('hidden'); return; }
  b.innerHTML = '';
  b.append(el('span', null, `${basename(tab.path)} changed on disk while you had unsaved edits.`));
  const load = el('button', null, 'Load disk version');
  load.onclick = () => { applyText(tab, tab.conflict); tab.savedVersion = tab.model.getAlternativeVersionId(); tab.conflict = null; updateTabEl(tab); renderBanner(); syncDirty(); };
  const keep = el('button', null, 'Keep mine');
  keep.onclick = () => { tab.conflict = null; renderBanner(); };
  b.append(load, keep);
  b.classList.remove('hidden');
}

// ---------------------------------------------------------------- style, formatting, go-to-definition

function lintSoon(tab, delay = 400) {
  clearTimeout(tab.lintTimer);
  tab.lintTimer = setTimeout(() => lint(tab), delay);
}

// Newly opened tabs are checked one at a time, after the one in front: Python runs one check at a time anyway, and at
// startup eight tabs at once left the visible one waiting about 2 s behind the others.
const lintQueue = [];
let lintDraining = false;
function lintLater(tab) {
  lintQueue.push(tab);
  if (S.ready) drainLints();
}

async function drainLints() {
  if (lintDraining) return;
  lintDraining = true;
  try {
    while (lintQueue.length) {
      const tab = lintQueue.shift();
      if (!tab.linted && !tab.model.isDisposed()) await lint(tab);
    }
  } finally {
    lintDraining = false;
  }
}

async function lint(tab) {
  if (tab.model.isDisposed()) return;
  tab.linted = true;
  const version = tab.model.getVersionId();
  let result;
  try {
    const glycanCheck = tab === S.active && !glycans.off && !glycans.noProcess;  // as checkGlycans: otherwise Python need not keep the tree for it
    result = await api.lint(tab.path, tab.model.getValue(monaco.editor.EndOfLinePreference.LF), glycanCheck);
  } catch (e) {
    tab.lintNote = errMsg(e);
    if (tab === S.active) updateStatus();
    return;
  }
  if (tab.model.isDisposed() || tab.model.getVersionId() !== version) return;
  const model = tab.model, n = model.getLineCount();
  tab.lintNote = result.note;
  tab.markers = result.markers;
  monaco.editor.setModelMarkers(model, 'style', result.markers.map((m) => {
    const line = Math.min(Math.max(m.line, 1), n);
    const maxCol = model.getLineMaxColumn(line);
    let start = m.col > 0 ? Math.min(m.col, maxCol) : model.getLineFirstNonWhitespaceColumn(line) || 1;
    let end = m.col > 0 ? Math.min(start + 1, maxCol) : maxCol;
    if (end <= start) { start = 1; end = Math.max(maxCol, 2); }
    return {severity: m.severity === 'error' ? monaco.MarkerSeverity.Error : monaco.MarkerSeverity.Warning,
      message: `${m.code}  ${m.message}`, source: 'style', startLineNumber: line, startColumn: start, endLineNumber: line, endColumn: end};
  }));
  // a blank line (GW1) has no text to underline, so mark it in the gutter too
  tab.blankDecos = model.deltaDecorations(tab.blankDecos || [], result.markers.filter((m) => m.code === 'GW1').map((m) => ({
    range: new monaco.Range(m.line, 1, m.line, 1), options: {isWholeLine: true, linesDecorationsClassName: 'gw1-gutter'}})));
  updateTabEl(tab);
  if (tab === S.active) { updateStatus(); checkGlycans(tab); }
}

// ---------------------------------------------------------------- hints: signatures and docstrings of the projects' own code (slate/hints.py)

// The call the text ends inside: {name, qualifier, index of the argument being typed, its keyword}, or null.
// Strings and comments are skipped, so a '(' in 'Gal(b1-4)' is no call; inner brackets keep their own commas.
function callAt(text) {
  const open = [];
  let quote = null;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quote) {
      if (c === '\\') i++;
      else if (text.startsWith(quote, i)) { i += quote.length - 1; quote = null; }
      else if (c === '\n' && quote.length === 1) quote = null;
      continue;
    }
    if (c === '#') {
      const nl = text.indexOf('\n', i);
      if (nl < 0) return null;  // typing a comment
      i = nl;
    } else if (c === '"' || c === "'") {
      quote = text.startsWith(c.repeat(3), i) ? c.repeat(3) : c;
      i += quote.length - 1;
    } else if ('([{'.includes(c)) {
      open.push({c, at: i, commas: 0, arg: i + 1});
    } else if (')]}'.includes(c)) {
      open.pop();
    } else if (c === ',' && open.length) {
      Object.assign(open[open.length - 1], {commas: open[open.length - 1].commas + 1, arg: i + 1});
    }
  }
  const call = [...open].reverse().find((o) => o.c === '(');
  if (!call) return null;
  const before = text.slice(0, call.at);
  const m = before.match(/((?:[A-Za-z_]\w*\s*\.\s*)*[A-Za-z_]\w*)\s*$/);
  if (!m || /\b(def|class)\s+$/.test(before.slice(0, before.length - m[0].length))) return null;  // a definition, not a call
  const parts = m[1].replace(/\s+/g, '').split('.');
  const kw = text.slice(call.arg).match(/^\s*([A-Za-z_]\w*)\s*=(?!=)/);
  return {name: parts.pop(), qualifier: parts.join('.') || null, index: call.commas, keyword: kw ? kw[1] : null};
}

async function hintsFor(model, name, qualifier) {
  const inConsole = consoleInput && model === consoleInput.getModel();
  const path = inConsole ? null : S.pathOf.get(model.uri.toString()) || model.uri.fsPath;
  try {
    return await api.hints(path, name, qualifier, S.roots, inConsole ? S.history.slice(-100) : null);  // the console resolves names through its history's imports
  } catch (e) {
    return [];
  }
}

function hintDoc(d, withParams) {
  const parts = [d.doc];
  if (withParams) parts.push(d.params.filter((p) => p[2]).map((p) => `\`${p[3]}\`: ${p[2]}`).join('  \n'));
  if (d.returns) parts.push(`Returns: ${d.returns}`);
  return parts.filter(Boolean).join('\n\n');
}

// PyCharm's file structure popup (Ctrl+F12), which is Monaco's quick outline (also Ctrl+Shift+O)
function fileStructure() {
  if (!S.active) return;
  editor.focus();
  editor.trigger('slate', 'editor.action.quickOutline', {});
}

const SYMBOL_KIND = () => ({class: monaco.languages.SymbolKind.Class, method: monaco.languages.SymbolKind.Method, function: monaco.languages.SymbolKind.Function});

function registerProviders() {
  // Classes and functions from Python's ast (navigate.outline), asked for only when the popup opens; text that does not
  // parse keeps the tab's last outline
  monaco.languages.registerDocumentSymbolProvider('py', {
    async provideDocumentSymbols(model) {
      const tab = S.tabs.find((t) => t.model === model), kind = SYMBOL_KIND(), n = model.getLineCount();
      let found = null;
      try { found = await api.outline(model.getValue(monaco.editor.EndOfLinePreference.LF)); } catch (e) { /* the last outline below */ }
      if (found && tab) tab.outline = found;
      const symbol = (s) => {
        const line = Math.min(s.line, n), end = Math.min(s.end, n), text = model.getLineContent(line);
        const at = text.search(new RegExp(`\\b(?:def|class)\\s+${s.name}\\b`));
        const col = at < 0 ? 1 : text.indexOf(s.name, at) + 1;
        return {name: s.name, detail: '', kind: kind[s.kind], tags: [], range: new monaco.Range(line, 1, end, model.getLineMaxColumn(end)),
          selectionRange: new monaco.Range(line, col, line, Math.min(col + s.name.length, model.getLineMaxColumn(line))), children: s.children.map(symbol)};  // an outline kept from before an edit may not fit the line
      };
      return (found || (tab && tab.outline) || []).map(symbol);
    },
  });
  monaco.languages.registerDocumentFormattingEditProvider('py', {
    async provideDocumentFormattingEdits(model) {
      const path = S.pathOf.get(model.uri.toString()) || model.uri.fsPath;
      const before = model.getValue(monaco.editor.EndOfLinePreference.LF);
      try {
        const after = await api.format(path, before);
        if (after === before) { toast('Already in style', 'info'); return []; }
        return [{range: model.getFullModelRange(), text: after}];
      } catch (e) {
        toast(errMsg(e));
        return [];
      }
    },
  });
  monaco.languages.registerDefinitionProvider('py', {
    async provideDefinition(model, position) {
      const path = S.pathOf.get(model.uri.toString()) || model.uri.fsPath;
      let found;
      try {
        found = await api.goto(path, model.getValue(monaco.editor.EndOfLinePreference.LF), position.lineNumber, position.column - 1, S.roots);
      } catch (e) {
        return null;
      }
      const out = [];
      for (const d of found) {
        let target = monaco.editor.getModel(monaco.Uri.file(d.path));
        if (!target) {
          try { const f = await api.read_file(d.path); target = modelFor(f.path, f.text); } catch (e) { continue; }
        }
        out.push({uri: target.uri, range: new monaco.Range(d.line, d.col, d.line, d.col + (d.name || '').length)});
      }
      return out;
    },
  });
  monaco.languages.registerSignatureHelpProvider('py', {
    signatureHelpTriggerCharacters: ['(', ','],
    signatureHelpRetriggerCharacters: [')'],
    async provideSignatureHelp(model, position) {
      const start = Math.max(1, position.lineNumber - 80);  // calls longer than this are rare, and reading the whole model per keystroke is not
      const call = callAt(model.getValueInRange(new monaco.Range(start, 1, position.lineNumber, position.column)));
      if (!call) return null;
      const found = await hintsFor(model, call.name, call.qualifier);
      if (!found.length) return null;
      const signatures = found.map((d) => {
        const named = d.params.findIndex((p) => p[3] === call.keyword);
        const varargs = d.params.findIndex(([s]) => d.label[s] === '*' && d.label[s + 1] !== '*');  // *args takes every positional argument from there on
        const active = call.keyword ? named : varargs >= 0 && call.index >= varargs ? varargs : call.index;
        return {label: d.label, documentation: {value: hintDoc(d, false)}, activeParameter: active,
          parameters: d.params.map(([s, e, doc]) => ({label: [s, e], documentation: doc ? {value: doc} : undefined}))};
      });
      return {value: {signatures, activeSignature: 0, activeParameter: signatures[0].activeParameter}, dispose() {}};
    },
  });
  monaco.languages.registerHoverProvider('py', {
    async provideHover(model, position) {
      const word = model.getWordAtPosition(position);
      if (!word || !/^[A-Za-z_]/.test(word.word)) return null;
      const before = model.getLineContent(position.lineNumber).slice(0, word.startColumn - 1);
      if (/#/.test(before.replace(/(['"]).*?\1/g, ''))) return null;  // in a comment
      const q = before.match(/((?:[A-Za-z_]\w*\s*\.\s*)*[A-Za-z_]\w*)\s*\.\s*$/);
      const found = await hintsFor(model, word.word, q ? q[1].replace(/\s+/g, '') : null);
      if (!found.length) return null;
      return {range: new monaco.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn),
        contents: found.slice(0, 2).flatMap((d) => [{value: '```python\n' + d.label + '\n```'}, {value: hintDoc(d, true)}])};
    },
  });
  monaco.editor.registerEditorOpener({
    openCodeEditor(source, resource, where) {
      const path = S.pathOf.get(resource.toString()) || resource.fsPath;
      const at = !where ? {} : where.startLineNumber
        ? {line: where.startLineNumber, col: where.startColumn, endCol: where.endLineNumber === where.startLineNumber ? where.endColumn : undefined}
        : {line: where.lineNumber, col: where.column};
      openFile(path, at);  // with the place, so Back sees one jump
      return true;
    },
  });
}

// ---------------------------------------------------------------- glycan drawings (slate/glycans.py)

// A string on one line that reads as an IUPAC-condensed glycan: only glycan characters, and at least one linkage. No spaces,
// as in glycans.GLYCAN: all 13 spaced ones in glycowork and CandyCrunch were prose ("Transform -1)Fruf(b2-3)Fruf(b2- repeat
// structure into ..."), which GlycoDraw drew with the words as residues. f-strings (f"{{{p}(?1-?)}}") and bytes are skipped.
// Of the 1,121 distinct strings left in glycowork and CandyCrunch, GlycoDraw drew all but 4 deliberately broken test inputs.
const GLYCAN_STRING = /(['"])([A-Za-z[{][A-Za-z0-9?\-()[\]{},.|/]*?)\1/g;
const NOT_TEXT = /[fFbB][A-Za-z]?$/;  // a string prefix that makes the quotes something other than plain text
const LINKAGE = /\([abo?][\d?]?-[\d?][\d?|/,]*\)/;
// A glycan cell in a .csv, .tsv or .txt tab: a quoted cell, which may hold commas (3,6-Anhydro-L-Gal), or glycans.IN_TEXT's
// characters (commas only inside a linkage) from one delimiter, space or quote to the next, so it may start with a digit
// (6dTal, 3-Anhydro-Gal). In glycowork's and CandyCrunch's tables: 66,216 cells, 29,074 distinct glycans, none of them prose.
const TABLE_GLYCAN = /(["'])([A-Za-z0-9[{][A-Za-z0-9?\-()[\]{},.|/]*?)\1|(^|[\s,;"'])([A-Za-z0-9[{](?:[A-Za-z0-9?\-.|/[\]{}]|\([^()\s]*\))*)(?![^\s,;"'])/g;
const TABLE_TAB = /\.(csv|tsv|txt)$/i;
const TABLE_LINE = 10_000;  // Monaco renders no more of a line (stopRenderingLineAfter); glycowork's binding table has a 201,567-character header of glycans
const TABLE_GLYCANS = 150;  // distinct glycans a table shows at once, nearest the middle of the screen, so they never outnumber GLYCANS_KEPT
const GLYCANS_KEPT = 300;  // drawings, most recently shown first; each is about 8 KB of data URL
const glycans = {drawn: new Map(), waiting: new Set(), decorations: null, colors: null, off: false, noProcess: false, count: 0, cards: new Map(), full: new Map()};

// SNFG colours by monosaccharide, as glycowork's GlycoDraw colours them (motif/draw.py sugar_dict), lightened for Darcula's
// dark background. A residue takes the colour of the longest name it starts with: GlcNAc6S is a GlcNAc, Neu5Ac9Ac a Neu5Ac.
const SNFG = {
  white: 'Assigned Unknown dHexNAc HexNAc blank ddHex ddNon HexA HexN Hexf Penf dHex dNon Hex Pen',
  blue: 'GlcfNAc GlcNAc QuiNAc Apif GlcA GlcN Glcf Api Bac Glc Oli Qui',
  green: 'LDManHep ManfNAc ManNAc RhaNAc Araf Fruf ManA ManN Manf Rhaf Ara Fru Kdn Man Pse Rha Tyv',
  yellow: 'GalfNAc GalNAc GalA GalN Galf Kdof Lyxf Tagf Gal Kdo Leg Lyx Tag',
  orange: '6dGul GulNAc GulA GulN Sorf Xylf Abe Dha Gul Sor Xyl',
  pink: '6dAltNAc DDManHep 6dAltf AltNAc 6dAlt AltA AltN Altf Parf Psif Ribf Aci Alt Par Psi Rib',
  purple: 'AllNAc MurNAc Neu5Ac AllA AllN All Dig',
  lightblue: '6dTalNAc 6dTalf MurNGc Neu5Gc TalNAc 4eLeg 6dTal TalA TalN Col Tal',
  brown: 'IdoNAc IdoA IdoN Ido Mur Neu',
  red: 'FucfNAc FucNAc Fucf Fuc Sia',
};
const SNFG_NAMES = Object.entries(SNFG).flatMap(([color, names]) => names.split(' ').map((n) => [n, color])).sort((a, b) => b[0].length - a[0].length);
const snfgColor = (residue) => (SNFG_NAMES.find(([n]) => residue.replace(/^[DL]-/, '').startsWith(n)) || [])[1];

// Inline decorations colouring a glycan string's residues by SNFG colour and dimming its linkages: [range, class] pairs.
function snfgRuns(line, from, text) {
  const runs = [];
  let i = 0;
  while (i < text.length) {
    if (text[i] === '(') {
      const close = text.indexOf(')', i);
      const end = close < 0 ? text.length : close + 1;
      runs.push([from + i, from + end, 'snfg-link']);
      i = end;
    } else if ('[]{}'.includes(text[i])) {
      i++;
    } else {
      let end = i;
      while (end < text.length && !'()[]{}'.includes(text[end])) end++;
      const color = snfgColor(text.slice(i, end));
      if (color) runs.push([from + i, from + end, 'snfg-' + color]);
      i = end;
    }
  }
  return runs.map(([s, e, cls]) => ({range: new monaco.Range(line, s, line, e), options: {description: 'snfg', inlineClassName: cls}}));
}

function glycansIn(model, from, to) {
  const out = [];
  for (let n = from; n <= to; n++) {
    const line = model.getLineContent(n);
    if (line.length > 3000 || !line.includes('-')) continue;
    for (const m of line.matchAll(GLYCAN_STRING)) {
      if (LINKAGE.test(m[2]) && !NOT_TEXT.test(line.slice(Math.max(0, m.index - 2), m.index))) out.push({glycan: m[2], line: n, start: m.index + 1, from: m.index + 2, end: m.index + m[0].length + 1});
    }
  }
  return out;
}

// The glycan cells on lines from..to, those nearest `middle` when there are more than TABLE_GLYCANS. start..end is the hover's
// range (quotes included), `from` the glycan's first column, and `end` where its drawing goes.
function tableGlycansIn(model, from, to, middle = from) {
  const out = [];
  for (let n = from; n <= to; n++) {
    const line = model.getLineContent(n);
    if (!line.includes('-')) continue;
    for (const m of line.slice(0, TABLE_LINE).matchAll(TABLE_GLYCAN)) {
      const glycan = m[2] || m[4].replace(/\.+$/, '');  // a cell at the end of a sentence in a .txt
      if (!LINKAGE.test(glycan) || (line.length > TABLE_LINE && m.index + m[0].length >= TABLE_LINE)) continue;
      if (m[2] && NOT_TEXT.test(line.slice(Math.max(0, m.index - 2), m.index))) continue;  // f"{p}(?1-?)" quoted in a .txt
      const start = m.index + 1 + (m[2] ? 0 : m[3].length);
      out.push({glycan, line: n, start, from: m[2] ? start + 1 : start, end: m[2] ? start + m[0].length : start + glycan.length});
    }
  }
  const shown = new Set();
  for (const f of [...out].sort((a, b) => Math.abs(a.line - middle) - Math.abs(b.line - middle))) if (shown.size < TABLE_GLYCANS) shown.add(f.glycan);
  return shown.size < TABLE_GLYCANS ? out : out.filter((f) => shown.has(f.glycan));
}

// The glycans of a Python tab's strings or a table tab's cells on lines from..to; null for any other tab
function glycansOn(model, from, to, middle) {
  if (model.getLanguageId() === 'py') return glycansIn(model, from, to);
  return TABLE_TAB.test(model.uri.path) ? tableGlycansIn(model, from, to, middle) : null;
}

const svgUrl = (svg) => 'data:image/svg+xml;base64,' + btoa(Array.from(new TextEncoder().encode(svg), (b) => String.fromCharCode(b)).join(''));
const viewBox = (svg) => ((svg.match(/viewBox="([^"]+)"/) || [])[1] || '0 0 100 100').split(/[\s,]+/).map(Number);

// The drawing goes into a style rule of its own, since injected text can only carry a class: GlycoDraw's die-cut sticker,
// or on a glycowork without stickers (before 1.10.1) a light chip
function glycanDrawing([svg, sticker]) {
  const [, , w, h] = viewBox(svg);
  const cls = 'glycan-' + (++glycans.count), height = sticker ? 20 : 16;
  const style = el('style', null, `.${cls} { width: ${Math.max(4, Math.round(height * w / h))}px; background-image: url("${svgUrl(svg)}"); }`);
  document.head.append(style);
  return {cls: (sticker ? 'sticker ' : 'chip ') + cls, style, size: [w, h]};
}

// The hover's full drawing, with linkages, asked for when a glycan is first hovered: drawing it for every glycan on screen
// took half the drawing time and 40% of the page's drawing data for hovers that mostly never come. On white, because
// GlycoDraw draws its lines in charcoal on nothing. Cached per glycan, the newest GLYCANS_KEPT.
function glycanFull(glycan) {
  if (!glycans.full.has(glycan)) {
    glycans.full.set(glycan, api.glycan_full(glycan, S.roots).then((r) => {
      if (!r || !r.svg) return null;
      const [x, y, w, h] = viewBox(r.svg);
      const onWhite = r.svg.replace(/<svg[^>]*>/, (open) => `${open}<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="#FFFFFF"/>`);
      return {url: svgUrl(onWhite), size: [w, h]};
    }, () => null));
    for (const g of glycans.full.keys()) { if (glycans.full.size <= GLYCANS_KEPT) break; glycans.full.delete(g); }
  }
  return glycans.full.get(glycan);
}

function drawnGlycan(glycan) {
  const d = glycans.drawn.get(glycan);
  if (d) { glycans.drawn.delete(glycan); glycans.drawn.set(glycan, d); }  // most recently shown last, so the oldest go first
  return d;
}

function showGlycans(found) {
  glycans.decorations.set(found.filter((f) => glycans.drawn.get(f.glycan)).map((f) => ({
    range: new monaco.Range(f.line, f.end, f.line, f.end),
    options: {description: 'glycan', showIfCollapsed: true,  // without showIfCollapsed, Monaco drops injected text at an empty range
      after: {content: '​', inlineClassName: 'glycan ' + drawnGlycan(f.glycan).cls, inlineClassNameAffectsLetterSpacing: true,
        cursorStops: monaco.editor.InjectedTextCursorStops.None}},
  })));
}

// Glycans on the visible lines and 30 either side: the 2,167 in glycowork's test_core_functions.py would take 4 s to draw at once
const refreshGlycans = debounce(async () => {
  const model = editor.getModel(), ranges = editor.getVisibleRanges();
  const first = ranges.length && ranges[0].startLineNumber, last = ranges.length && ranges[ranges.length - 1].endLineNumber;
  const found = glycans.off || !model || !ranges.length ? null : glycansOn(model, Math.max(1, first - 30), Math.min(model.getLineCount(), last + 30), (first + last) / 2);
  if (!found) { glycans.decorations.clear(); glycans.colors.clear(); return; }
  glycans.colors.set(found.flatMap((f) => snfgRuns(f.line, f.from, f.glycan)));
  if (glycans.noProcess) return;
  showGlycans(found);
  const missing = [...new Set(found.map((f) => f.glycan))].filter((g) => !glycans.drawn.has(g) && !glycans.waiting.has(g));
  if (!missing.length) return;
  missing.forEach((g) => glycans.waiting.add(g));
  let drawn;
  try { drawn = await api.glycans(missing, S.roots); } catch (e) { drawn = {error: errMsg(e)}; }
  missing.forEach((g) => glycans.waiting.delete(g));
  if (drawn.error) {
    glycans.noProcess = true;  // the colours stay: they need no glycowork
    glycans.decorations.clear();
    toast(`No glycan drawings this session: ${drawn.error}`);
    return;
  }
  for (const [g, svgs] of Object.entries(drawn)) glycans.drawn.set(g, svgs && glycanDrawing(svgs));
  if (S.active && S.active.glycanPending) { S.active.glycanPending = false; checkGlycans(S.active); }
  for (const [g, d] of glycans.drawn) {
    if (glycans.drawn.size <= GLYCANS_KEPT) break;
    glycans.drawn.delete(g);
    if (d) d.style.remove();
  }
  if (editor.getModel() === model) refreshGlycans();
}, 150);

function setupGlycans(saved) {
  glycans.off = saved.glycan_drawings === false;
  glycans.decorations = editor.createDecorationsCollection();
  glycans.colors = editor.createDecorationsCollection();
  editor.onDidChangeModel(refreshGlycans);
  editor.onDidChangeModelContent(refreshGlycans);
  editor.onDidScrollChange((e) => e.scrollTopChanged && refreshGlycans());
  editor.onDidLayoutChange(refreshGlycans);
  const hover = {
    async provideHover(model, position) {
      const f = glycans.off || glycans.noProcess ? null : (glycansOn(model, position.lineNumber, position.lineNumber) || []).find((g) => position.column >= g.start && position.column <= g.end);
      const d = f && glycans.drawn.get(f.glycan);
      if (!d) return null;
      // A process busy with a motif search answers late: the hover then shows the sticker, and the card if it came
      const within = (promise, ms, otherwise) => Promise.race([promise, new Promise((r) => setTimeout(() => r(otherwise), ms))]);
      const [full, card] = await Promise.all([within(glycanFull(f.glycan), 1000, null), within(glycanCard(f.glycan), 400, '')]);
      const {url, size} = full || {url: d.style.textContent.match(/url\("([^"]+)"\)/)[1], size: d.size};
      // Monaco caps a hover at a quarter of the editor's height, at least 250 px: a taller drawing pushed the card out of sight
      const room = Math.max(250, editor.getLayoutInfo().height / 4) - (card ? 110 : 16);
      const scale = Math.min(full ? 1 : 2, 480 / size[0], room / size[1]);
      return {range: new monaco.Range(f.line, f.start, f.line, f.end),
        contents: [{value: `![SNFG drawing](${url}|width=${Math.round(size[0] * scale)},height=${Math.round(size[1] * scale)})`},
          ...(card ? [{value: card, isTrusted: true}] : [])]};
    },
  };
  monaco.languages.registerHoverProvider('py', hover);
  monaco.languages.registerHoverProvider('plaintext', hover);  // .csv, .tsv and .txt tabs (glycansOn)
  monaco.editor.registerCommand('slate.motifSearch', (accessor, motif) => searchMotif(motif));
}

// The hover card under a glycan's drawing (Api.glycan_info): class, mass, composition, glycowork's motifs (each a link
// to the glycans holding it) and the canonical form when it differs. Cached per glycan, the newest GLYCANS_KEPT.
const GLYCAN_CLASS = {N: 'N-glycan', O: 'O-glycan', free: 'Free glycan', lipid: 'Glycolipid glycan', repeat: 'Repeat unit'};
function glycanCard(glycan) {
  if (!glycans.cards.has(glycan)) {
    glycans.cards.set(glycan, api.glycan_info(glycan, S.roots).then((info) => {
      if (!info || info.error) return info && info.error ? `glycowork cannot read this glycan: ${escapeMd(info.error)}` : '';
      const lines = [[GLYCAN_CLASS[info.class] && `**${GLYCAN_CLASS[info.class]}**`, info.mass && `${info.mass.toFixed(4)} Da monoisotopic`].filter(Boolean).join(' · ')];
      if (info.composition) lines.push(Object.entries(info.composition).map(([k, v]) => `${k} ${v}`).join(' · '));
      if (info.motifs && info.motifs.length) {
        const link = (m) => `[${escapeMd(m)}](command:slate.motifSearch?${encodeURIComponent(JSON.stringify([m]))} "Find the glycans holding ${m}")`;
        lines.push('Motifs: ' + info.motifs.map(link).join(', '));
      }
      if (info.canonical && info.canonical !== glycan) lines.push(`glycowork writes it \`${info.canonical}\``);
      return lines.filter(Boolean).join('  \n');
    }, () => ''));
    for (const g of glycans.cards.keys()) { if (glycans.cards.size <= GLYCANS_KEPT) break; glycans.cards.delete(g); }
  }
  return glycans.cards.get(glycan);
}

const escapeMd = (s) => String(s).replace(/[\\`*_[\]()<>#|]/g, (c) => '\\' + c);

// Diagnostics for the glycans written as values in the front file (Api.glycan_check: not in tests, regexes, .replace and
// the like), with quick fixes: IUPAC-condensed for another notation, glycowork's branch order. Hints, not squiggles,
// except for a glycan glycowork cannot read.
async function checkGlycans(tab) {
  if (glycans.off || glycans.noProcess || tab !== S.active || tab.model.isDisposed()) return;
  const model = tab.model, version = model.getVersionId();
  let found;
  try { found = await api.glycan_check(tab.path, model.getValue(monaco.editor.EndOfLinePreference.LF), S.roots); } catch (e) { return; }
  if (model.isDisposed() || model.getVersionId() !== version) return;
  tab.glycanPending = found === null;  // no drawing process yet: checked once a glycan on screen is drawn (refreshGlycans)
  if (found === null) return;
  tab.glycanFixes = new Map(found.filter((d) => d.fix).map((d) => [`${d.line}:${d.start}`, d]));
  monaco.editor.setModelMarkers(model, 'glycan', found.map((d) => ({
    severity: d.severity === 'warning' ? monaco.MarkerSeverity.Warning : monaco.MarkerSeverity.Hint, message: d.message, source: 'glycowork',
    startLineNumber: d.line, startColumn: d.start, endLineNumber: d.line, endColumn: d.end})));
}

function registerGlycanFixes() {
  monaco.languages.registerCodeActionProvider('py', {
    provideCodeActions(model, range, context) {
      const tab = S.tabs.find((t) => t.model === model);
      const actions = context.markers.filter((m) => m.source === 'glycowork').map((m) => [m, tab && tab.glycanFixes && tab.glycanFixes.get(`${m.startLineNumber}:${m.startColumn}`)])
        .filter(([, d]) => d).map(([m, d]) => ({title: `${d.title}: ${d.fix}`, kind: 'quickfix', diagnostics: [m], isPreferred: true,
          edit: {edits: [{resource: model.uri, versionId: model.getVersionId(),
            textEdit: {range: new monaco.Range(m.startLineNumber, m.startColumn, m.endLineNumber, m.endColumn), text: d.fix}}]}}));
      return {actions, dispose() {}};
    },
  });
}

// ---------------------------------------------------------------- folder tree

function fileIcon(name) {
  const ext = (name.match(/\.([^.]+)$/) || ['', ''])[1].toLowerCase();
  const map = {py: ['py', '#4A88C7'], ipynb: ['nb', '#CC7832'], md: ['md', '#9876AA'], json: ['{}', '#BBB529'], csv: ['≡', '#6A8759'],
    tsv: ['≡', '#6A8759'], txt: ['≡', '#8C8C8C'], toml: ['⚙', '#8C8C8C'], cfg: ['⚙', '#8C8C8C'], yml: ['⚙', '#8C8C8C'], yaml: ['⚙', '#8C8C8C']};
  return map[ext] || ['·', '#8C8C8C'];
}

function treeRow(entry, depth, rootIdx, isRoot) {
  const row = el('div', 'node' + (entry.dir ? ' dir' : '') + (isRoot ? ' root' : ''));
  row.style.paddingLeft = (4 + depth * 14) + 'px';
  row.dataset.key = keyOf(entry.path);
  if (entry.dir) row.append(el('span', 'twisty', S.expanded.has(keyOf(entry.path)) ? '▾' : '▸'));
  if (isRoot) {
    const dot = el('span', 'dot');
    dot.style.background = rootColor(rootIdx);
    row.append(dot);
  } else if (!entry.dir) {
    const [glyph, color] = fileIcon(entry.name);
    const icon = el('span', 'icon', glyph);
    icon.style.color = color;
    row.append(icon);
  }
  row.append(el('span', 'label', entry.name));
  if (isRoot) {
    row.title = entry.path;
    const rm = el('span', 'remove', '✕');
    rm.title = 'Remove from slate (nothing is deleted)';
    rm.addEventListener('click', (ev) => { ev.stopPropagation(); removeRoot(rootIdx); });
    row.append(rm);
  }
  return row;
}

async function buildDir(entry, depth, rootIdx, isRoot, container) {
  const row = treeRow(entry, depth, rootIdx, isRoot);
  container.append(row);
  if (!entry.dir) {
    row.addEventListener('click', () => openFile(entry.path));
    row.addEventListener('contextmenu', (ev) => showMenu(ev, fileMenu(entry)));
    return;
  }
  const kids = el('div');
  container.append(kids);
  const key = keyOf(entry.path);
  row.addEventListener('click', async () => {
    if (S.expanded.has(key)) { S.expanded.delete(key); kids.innerHTML = ''; row.firstChild.textContent = '▸'; }
    else { S.expanded.add(key); row.firstChild.textContent = '▾'; await fillDir(entry, depth, rootIdx, kids); markTreeActive(); }
    persist();
  });
  row.addEventListener('contextmenu', (ev) => showMenu(ev, dirMenu(entry, isRoot, rootIdx)));
  if (S.expanded.has(key)) await fillDir(entry, depth, rootIdx, kids);
}

async function fillDir(entry, depth, rootIdx, kids) {
  let list;
  try { list = await api.list_dir(entry.path); } catch (e) { list = []; }
  const frag = document.createDocumentFragment();
  await Promise.all(list.map((child) => {
    const slot = el('div');
    frag.append(slot);
    return buildDir(child, depth + 1, rootIdx, false, slot);
  }));
  kids.innerHTML = '';
  kids.append(frag);
}

let treeBuilding = null;
async function renderTree() {
  if (treeBuilding) return treeBuilding;
  treeBuilding = (async () => {
    const tree = $('tree');
    const frag = el('div');
    await Promise.all(S.roots.map((root, i) => {
      const slot = el('div');
      frag.append(slot);
      return buildDir({name: basename(root), path: root, dir: true}, 0, i, true, slot);
    }));
    if (!S.roots.length) {
      const hint = el('div', 'dim', 'No folders yet. Add the projects you work on with +');
      hint.style.padding = '10px';
      frag.append(hint);
    }
    const scroll = tree.scrollTop;
    tree.innerHTML = '';
    tree.append(frag);
    tree.scrollTop = scroll;
    markTreeActive();
  })();
  try { await treeBuilding; } finally { treeBuilding = null; }
}

function markTreeActive() {
  for (const e of document.querySelectorAll('#tree .node.active')) e.classList.remove('active');
  if (!S.active) return;
  const row = document.querySelector(`#tree .node[data-key="${CSS.escape(S.active.key)}"]`);
  if (row) row.classList.add('active');
}

async function addRoot() {
  const path = await api.pick_folder();
  if (!path || S.roots.some((r) => keyOf(r) === keyOf(path))) return;
  S.roots.push(path);
  S.expanded.add(keyOf(path));
  finderCache = null;
  await renderTree();
  renderTabs();
  persist();
}

function removeRoot(i) {
  S.roots.splice(i, 1);
  finderCache = null;
  renderTree();
  renderTabs();
  persist();
}

async function revealActiveInTree() {
  if (!S.active) return;
  const i = rootIndexOf(S.active.path);
  if (i < 0) return;
  let dir = dirname(S.active.path);
  while (keyOf(dir).startsWith(keyOf(S.roots[i]))) {
    S.expanded.add(keyOf(dir));
    if (keyOf(dir) === keyOf(S.roots[i])) break;
    dir = dirname(dir);
  }
  showView('files');
  await renderTree();
  const row = document.querySelector('#tree .node.active');
  if (row) row.scrollIntoView({block: 'center'});
}

// ---------------------------------------------------------------- context menus

function showMenu(ev, items) {
  ev.preventDefault();
  const m = $('menu');
  m.innerHTML = '';
  for (const [label, fn] of items) {
    const d = el('div', null, label);
    d.addEventListener('click', () => { hideMenu(); fn(); });
    m.append(d);
  }
  m.classList.remove('hidden');
  m.style.left = Math.min(ev.clientX, innerWidth - m.offsetWidth - 4) + 'px';
  m.style.top = Math.min(ev.clientY, innerHeight - m.offsetHeight - 4) + 'px';
}

const hideMenu = () => $('menu').classList.add('hidden');

function fileMenu(entry) {
  return [
    ['Open', () => openFile(entry.path)],
    ['Copy path', () => navigator.clipboard.writeText(entry.path)],
    [S.mac ? 'Reveal in Finder' : 'Reveal in Explorer', () => api.reveal(entry.path)],
    ['Rename…', () => renameEntry(entry)],
    [S.mac ? 'Move to Trash' : 'Delete', () => trashEntry(entry)],
  ];
}

function dirMenu(entry, isRoot, rootIdx) {
  const items = [
    ['New file…', () => createIn(entry, false)],
    ['New folder…', () => createIn(entry, true)],
    ['Copy path', () => navigator.clipboard.writeText(entry.path)],
    [S.mac ? 'Reveal in Finder' : 'Reveal in Explorer', () => api.reveal(join(entry.path, '.'))],
  ];
  if (isRoot) items.push(['Remove from slate', () => removeRoot(rootIdx)]);
  else items.push(['Rename…', () => renameEntry(entry)], [S.mac ? 'Move to Trash' : 'Delete', () => trashEntry(entry)]);
  return items;
}

// A path at or under base (a renamed or deleted file or folder), by keyOf's identity
const under = (path, base) => keyOf(path) === keyOf(base) || keyOf(path).startsWith(keyOf(base) + '\\');

async function renameEntry(entry) {
  const name = prompt(`New name for ${entry.name}`, entry.name);
  if (!name || name === entry.name) return;
  const open = S.tabs.filter((t) => under(t.path, entry.path)), unsaved = open.filter(isDirty);
  if (unsaved.length) { toast(`Save ${unsaved.map((t) => basename(t.path)).join(', ')} first`); return; }
  let to;
  try { to = await api.rename(entry.path, name); } catch (e) { toast(errMsg(e)); return; }
  const moved = (p) => (under(p, entry.path) ? to + p.slice(entry.path.length) : p);
  for (const p of [...nav.back, ...nav.forward, nav.here].filter(Boolean)) p.path = moved(p.path);  // first: retarget's activate compares with nav.here
  for (const tab of open) await retarget(tab, moved(tab.path));
  S.closed = S.closed.map(moved);
  S.expanded = new Set([...S.expanded].map((k) => (under(k, entry.path) ? keyOf(to) + k.slice(entry.path.length) : k)));
  finderCache = null;
  await renderTree();
  renderTabs();
  persist();
}

// A renamed file's tab keeps its place, now with a model at the new path (models are keyed by path). Never an unsaved one.
async function retarget(tab, path) {
  let file;
  try { file = await api.read_file(path); } catch (e) { toast(errMsg(e)); return; }
  const front = tab === S.active, old = tab.model, oldKey = tab.key;
  clearTimeout(tab.lintTimer);
  Object.assign(tab, {path: file.path, key: keyOf(file.path), model: modelFor(file.path, file.text), mtime: file.mtime, encoding: file.encoding,
    viewState: front ? editor.saveViewState() : tab.viewState, markers: [], linted: false, cellDecos: null, blankDecos: null, glycanFixes: null, outline: null, deleted: false});
  tab.savedVersion = tab.model.getAlternativeVersionId();
  tab.model.onDidChangeContent(() => onEdit(tab));
  S.mru = S.mru.map((k) => (k === oldKey ? tab.key : k));
  if (front) activate(tab, {mru: false});
  old.dispose();
  if (tab.model.getLanguageId() === 'py') { markCells(tab); if (!front) lintLater(tab); }
}

async function trashEntry(entry) {
  const unsaved = S.tabs.filter((t) => under(t.path, entry.path) && isDirty(t));
  const lost = unsaved.length ? `\n\nUnsaved changes in ${unsaved.map((t) => basename(t.path)).join(', ')} will be lost.` : '';
  if (!confirm(`Move ${entry.name} to ${S.mac ? 'the Trash' : 'the Recycle Bin'}?${lost}`)) return;
  try { await api.trash(entry.path); } catch (e) { toast(errMsg(e)); return; }
  nav.back = nav.back.filter((p) => !under(p.path, entry.path));
  nav.forward = nav.forward.filter((p) => !under(p.path, entry.path));
  if (nav.here && under(nav.here.path, entry.path)) nav.here = null;  // so closing its tab notes no place in it
  for (const tab of S.tabs.filter((t) => under(t.path, entry.path))) closeTab(tab, {force: true});
  S.closed = S.closed.filter((p) => !under(p, entry.path));
  finderCache = null;
  await renderTree();
  persist();
}

async function createIn(entry, isDir) {
  const name = prompt(isDir ? 'New folder name' : 'New file name (may include subfolders, e.g. scripts/run.py)');
  if (!name) return;
  try {
    const path = await api.create(join(entry.path, nativeSep(name)), isDir);
    S.expanded.add(keyOf(entry.path));
    finderCache = null;
    await renderTree();
    if (!isDir) openFile(path);
  } catch (e) {
    toast(errMsg(e));
  }
}

// ---------------------------------------------------------------- Ctrl+P finder

let finderCache = null, finderItems = [], finderSel = 0;

async function openFinder() {
  $('finder').classList.remove('hidden');
  const input = $('finder-input');
  input.value = '';
  input.focus();
  renderFinder();
  if (!finderCache || Date.now() - finderCache.at > 20000) {
    const files = await api.list_files(S.roots);
    finderCache = {at: Date.now(), files: files.map(([i, rel]) => ({i, rel, path: join(S.roots[i], rel), name: basename(rel), lower: rel.toLowerCase().replace(/\//g, '\\')}))};
    renderFinder();
  }
}

const closeFinder = () => { $('finder').classList.add('hidden'); if (S.active) editor.focus(); };

function fuzzy(q, s) {
  let qi = 0, score = 0, last = -2;
  const pos = [];
  for (let i = 0; i < s.length && qi < q.length; i++) {
    if (s[i] !== q[qi]) continue;
    score += 1 + (last === i - 1 ? 5 : 0) + (i === 0 || '\\/_-. '.includes(s[i - 1]) ? 6 : 0);
    pos.push(i);
    last = i;
    qi++;
  }
  return qi === q.length ? {score: score - (pos[pos.length - 1] - pos[0]) * 0.05, pos} : null;
}

function renderFinder() {
  const raw = $('finder-input').value.trim();
  const m = raw.match(/^(.*?)(?::(\d+))?$/);
  const q = m[1].toLowerCase().replace(/\//g, '\\');
  const open = new Set(S.tabs.map((t) => t.key));
  let items = [];
  if (!q) {
    items = S.mru.map((k) => S.tabs.find((t) => t.key === k)).filter(Boolean)
      .map((t) => ({path: t.path, name: basename(t.path), i: rootIndexOf(t.path), rel: relPath(t.path), namePos: [], relPos: []}));
  } else if (finderCache) {
    const nameQ = q.includes('\\') ? null : q;
    for (const f of finderCache.files) {
      let hit = null;
      const lname = f.name.toLowerCase();
      if (nameQ) {
        const nm = fuzzy(nameQ, lname);
        if (nm) hit = {score: nm.score + 20 + (lname === nameQ ? 60 : lname.startsWith(nameQ) ? 25 : 0), namePos: nm.pos, relPos: []};
      }
      if (!hit) {
        const pm = fuzzy(q, f.lower);
        if (pm) hit = {score: pm.score, namePos: [], relPos: pm.pos};
      }
      if (!hit) continue;
      hit.score += (open.has(keyOf(f.path)) ? 8 : 0) - f.rel.length * 0.02;
      items.push({...f, ...hit, rel: f.rel});
    }
    items.sort((a, b) => b.score - a.score);
    items = items.slice(0, 80);
  }
  finderItems = items.map((it) => ({...it, line: m[2] ? +m[2] : null}));
  finderSel = 0;
  const list = $('finder-list');
  list.innerHTML = '';
  finderItems.forEach((it, idx) => {
    const row = el('div', 'fi' + (idx === 0 ? ' sel' : ''));
    const name = el('span', 'name');
    name.innerHTML = mark(it.name, it.namePos);
    const where = el('span', 'where');
    const rootName = it.i >= 0 ? basename(S.roots[it.i]) + S.sep : '';
    const rel = nativeSep(it.rel);
    const relShown = it.relPos.length ? mark(rel, it.relPos) : escapeHtml(rel.slice(0, Math.max(0, rel.length - it.name.length)));
    where.innerHTML = escapeHtml(rootName) + relShown;
    const dot = el('span', 'dot');
    dot.style.cssText = `width:8px;height:8px;border-radius:50%;flex:none;align-self:center;background:${rootColor(it.i)}`;
    row.append(dot, name, where);
    if (open.has(keyOf(it.path))) row.append(el('span', 'open', 'open'));
    row.addEventListener('click', () => pickFinder(idx));
    list.append(row);
  });
  if (!finderItems.length) list.append(el('div', 'fi dim', finderCache || !q ? 'No matching files' : 'Listing files…'));
}

function mark(text, pos) {
  const set = new Set(pos);
  return [...text].map((c, i) => (set.has(i) ? `<b>${escapeHtml(c)}</b>` : escapeHtml(c))).join('');
}

function moveFinder(step) {
  const rows = $('finder-list').children;
  if (!finderItems.length) return;
  rows[finderSel].classList.remove('sel');
  finderSel = (finderSel + step + finderItems.length) % finderItems.length;
  rows[finderSel].classList.add('sel');
  rows[finderSel].scrollIntoView({block: 'nearest'});
}

async function pickFinder(idx) {
  const it = finderItems[idx];
  if (!it) return;
  $('finder').classList.add('hidden');
  await openFile(it.path, it.line ? {line: it.line} : {});
}

// ---------------------------------------------------------------- find in files

let searchSeq = 0;
const search = {case: false, regex: false, glycan: false};

// Find the glycans holding a motif from the hover card, through the Search view's glycan mode.
function searchMotif(motif) {
  search.glycan = true;
  $('search-glycan').classList.add('on');
  glycanMode();
  showView('search');
  $('search-input').value = motif;
  runSearch();
}

async function runSearch() {
  const q = $('search-input').value;
  const seq = ++searchSeq;
  const results = $('search-results');
  if (!q) { results.innerHTML = ''; $('search-status').textContent = ''; return; }
  $('search-status').textContent = 'Searching…';
  let r;
  try {
    r = search.glycan ? await api.glycan_search(S.roots, q) : await api.search(S.roots, q, search.regex, search.case);
  } catch (e) {
    if (seq === searchSeq) $('search-status').textContent = errMsg(e);
    return;
  }
  if (seq !== searchSeq) return;
  results.innerHTML = '';
  $('search-status').textContent = `${r.matches.length} matching line${r.matches.length === 1 ? '' : 's'} in ${r.files} file${r.files === 1 ? '' : 's'}${r.truncated ? ' (stopped at 2000)' : ''}`
    + (search.glycan ? ` · ${r.glycans} glycan${r.glycans === 1 ? '' : 's'} hold it` : '');
  let current = null;
  for (const m of r.matches) {
    if (m.path !== current) {
      current = m.path;
      const head = el('div', 'sr-file');
      const dot = el('span', 'dot');
      dot.style.cssText = `width:8px;height:8px;border-radius:50%;flex:none;background:${rootColor(m.root)}`;
      const dir = el('span', 'dir', basename(S.roots[m.root]) + S.sep + dirname(S.sep + m.rel).replace(/^[\\/]/, ''));
      head.append(dot, el('span', null, basename(m.rel)), dir);
      head.title = m.path;
      head.addEventListener('click', () => openFile(m.path));
      results.append(head);
    }
    const row = el('div', 'sr-line');
    const lead = m.text.slice(0, m.start).replace(/^\s+/, '');
    const shift = lead.length > 40 ? lead.length - 40 : 0;
    row.innerHTML = `<span class="ln">${m.line}</span>${escapeHtml(lead.slice(shift))}<mark>${escapeHtml(m.text.slice(m.start, m.end))}</mark>${escapeHtml(m.text.slice(m.end))}`;
    row.addEventListener('click', () => openFile(m.path, {line: m.line, col: m.start + 1, endCol: m.end + 1}));
    results.append(row);
  }
}

// In glycan mode the query is a motif name (suggested from glycowork's list), a glycan substructure or a glyco-regex.
async function glycanMode() {
  const input = $('search-input');
  input.placeholder = search.glycan ? 'Glycans with motif, substructure or glyco-regex' : 'Find in all folders';
  if (search.glycan) input.setAttribute('list', 'motif-names');
  else input.removeAttribute('list');  // motif names are no suggestions for plain text
  $('search-case').disabled = $('search-regex').disabled = search.glycan;
  if (!search.glycan || $('motif-names').childElementCount) return;
  let names = [];
  try { names = await api.glycan_motifs(S.roots); } catch (e) { return; }
  $('motif-names').append(...names.map((n) => { const o = el('option'); o.value = n; return o; }));
}

function showView(view) {
  if (!$(view + '-view')) view = 'files';  // a view from an extension that is gone
  S.view = view;
  for (const b of document.querySelectorAll('#side-tabs button')) b.classList.toggle('active', b.dataset.view === view);
  for (const v of document.querySelectorAll('#sidebar > .view')) v.classList.toggle('hidden', v.id !== view + '-view');
  if (!S.sidebarOpen) { S.sidebarOpen = true; layout(); }
  if (view === 'search') {
    const sel = editor && editor.getModel() ? editor.getModel().getValueInRange(editor.getSelection()) : '';
    if (sel && !sel.includes('\n')) { $('search-input').value = sel; runSearch(); }
    $('search-input').focus();
    $('search-input').select();
  }
  persist();
  emit('view', view);
}

// ---------------------------------------------------------------- console

let consoleBusy = 0, histIdx = null, consoleStarted = false, outLine = null;

function setupConsole() {
  consoleInput = monaco.editor.create($('console-in'), {
    model: monaco.editor.createModel('', 'py'), theme: 'darcula', fontFamily: FONT, fontSize: 13, lineHeight: 19,
    lineNumbers: 'off', glyphMargin: false, folding: false, minimap: {enabled: false}, scrollBeyondLastLine: false,
    overviewRulerLanes: 0, overviewRulerBorder: false, hideCursorInOverviewRuler: true, renderLineHighlight: 'none',
    scrollbar: {vertical: 'hidden', horizontal: 'auto', alwaysConsumeMouseWheel: false}, lineDecorationsWidth: 0,
    automaticLayout: true, contextmenu: false, stickyScroll: {enabled: false}, wordBasedSuggestions: 'allDocuments',
    unicodeHighlight: {ambiguousCharacters: false}, fixedOverflowWidgets: true,
  });
  const fit = () => {
    const h = Math.min(consoleInput.getModel().getLineCount(), 12) * 19;
    $('console-in').style.height = h + 'px';
    consoleInput.layout();
  };
  consoleInput.onDidChangeModelContent(fit);
  // Standalone Monaco registers addCommand keybindings for every editor on the page, so each one is gated on a
  // context key that only the console's editor carries; otherwise Enter in the main editor would run the console.
  consoleInput.createContextKey('slateConsole', true);
  const K = monaco.KeyCode, M = monaco.KeyMod;
  const add = (keys, fn, when) => consoleInput.addCommand(keys, fn, 'slateConsole' + (when ? ' && ' + when : ''));
  add(K.Enter, consoleEnter, '!suggestWidgetVisible');
  add(M.CtrlCmd | K.Enter, () => submitConsole());
  add(M.Shift | K.Enter, () => consoleInput.trigger('keyboard', 'type', {text: '\n'}));
  add(K.UpArrow, () => historyStep(-1), '!suggestWidgetVisible && consoleOnFirstLine');
  add(K.DownArrow, () => historyStep(1), '!suggestWidgetVisible && consoleOnLastLine');
  add(M.CtrlCmd | K.KeyC, () => api.console_interrupt(), '!editorHasSelection');
  if (S.mac) add(M.WinCtrl | K.KeyC, () => api.console_interrupt());  // Ctrl+C, as in a Mac's Terminal (WinCtrl is Ctrl on macOS)
  add(M.CtrlCmd | K.KeyL, clearConsole);
  add(K.Escape, () => { if (S.active) editor.focus(); }, '!suggestWidgetVisible');
  const onFirst = consoleInput.createContextKey('consoleOnFirstLine', true);
  const onLast = consoleInput.createContextKey('consoleOnLastLine', true);
  consoleInput.onDidChangeCursorPosition((e) => {
    onFirst.set(e.position.lineNumber === 1);
    onLast.set(e.position.lineNumber === consoleInput.getModel().getLineCount());
  });
  $('console-interrupt').onclick = () => api.console_interrupt();
  $('console-restart').onclick = restartConsole;
  $('console-clear').onclick = clearConsole;
  $('console-close').onclick = () => toggleConsole(false);
  $('console-out').addEventListener('click', () => { if (!getSelection().toString()) consoleInput.focus(); });
}

function consoleCwd() {
  const i = S.active ? rootIndexOf(S.active.path) : -1;
  return i >= 0 ? S.roots[i] : S.roots[0] || S.home;
}

// Enter runs the input unless it is still open, like the plain REPL: an unclosed bracket or string, a trailing
// backslash, or a block header (a line ending in ':') that has not been closed by a blank line yet.
function needsMoreLines(code) {
  const lines = code.split('\n');
  if (lines.length > 1 && !lines[lines.length - 1].trim()) return false;
  let depth = 0, quote = null, lastCode = '';
  for (let i = 0; i < code.length; i++) {
    const c = code[i];
    if (quote) {
      if (c === '\\') i++;
      else if (code.startsWith(quote, i)) { i += quote.length - 1; quote = null; }
      else if (c === '\n' && quote.length === 1) quote = null;
      continue;
    }
    if (c === '#') { while (i < code.length && code[i] !== '\n') i++; continue; }
    if (c === '"' || c === "'") { quote = code.startsWith(c.repeat(3), i) ? c.repeat(3) : c; i += quote.length - 1; lastCode += c; continue; }
    if ('([{'.includes(c)) depth++;
    else if (')]}'.includes(c)) depth--;
    if (c === '\n') lastCode = '';
    else if (c !== ' ' && c !== '\t') lastCode += c;
  }
  if (quote && quote.length === 3) return true;
  if (depth > 0 || lastCode.endsWith('\\')) return true;
  const header = lines.some((l) => /:\s*(#.*)?$/.test(l) && /^\s*(def|class|for|while|if|elif|else|try|except|finally|with|async|match|case)\b|^\s*@/.test(l));
  return header || lastCode.endsWith(':');
}

function consoleEnter() {
  const code = consoleInput.getValue();
  if (!code.trim()) return;
  if (needsMoreLines(code)) consoleInput.trigger('keyboard', 'type', {text: '\n'});
  else submitConsole();
}

function submitConsole() {
  const code = consoleInput.getValue().replace(/\s+$/, '');
  if (!code) return;
  if (S.history[S.history.length - 1] !== code) S.history.push(code);
  S.history = S.history.slice(-300);
  histIdx = null;
  consoleInput.setValue('');
  echo(code);
  consoleBusy++;
  $('console-busy').classList.remove('hidden');
  consoleStarted = true;
  api.console_run(code, consoleCwd(), S.roots);
  persist();
}

function historyStep(step) {
  if (!S.history.length) return;
  if (histIdx === null) { if (step > 0) return; histIdx = S.history.length; S.draft = consoleInput.getValue(); }
  histIdx += step;
  if (histIdx < 0) histIdx = 0;
  if (histIdx >= S.history.length) { histIdx = null; consoleInput.setValue(S.draft || ''); return; }
  consoleInput.setValue(S.history[histIdx]);
  const m = consoleInput.getModel();
  consoleInput.setPosition({lineNumber: m.getLineCount(), column: m.getLineMaxColumn(m.getLineCount())});
}

function echo(code) {
  const out = $('console-out');
  const block = el('span', 'echo');
  out.append(block);
  outLine = null;
  monaco.editor.colorize(code, 'py', {tabSize: 4}).then((html) => {
    const lines = html.split('<br/>').filter((l, i, a) => i < a.length - 1 || l.replace(/<[^>]*>/g, ''));
    block.innerHTML = lines.map((l, i) => `<span class="p">${i ? '...' : '&gt;&gt;&gt;'} </span>${l}`).join('\n') + '\n';
    out.scrollTop = out.scrollHeight;
  });
  out.scrollTop = out.scrollHeight;
}

function appendOut(text, cls) {
  const out = $('console-out');
  const atBottom = out.scrollHeight - out.scrollTop - out.clientHeight < 30;
  text = text.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '');
  for (const part of text.split(/(\r\n|\n|\r)/)) {
    if (!part) continue;
    if (part === '\r') { if (outLine) outLine.textContent = ''; continue; }  // progress bars redraw their line
    if (!outLine || outLine.dataset.cls !== cls) {
      outLine = el('span', cls);
      outLine.dataset.cls = cls;
      out.append(outLine);
    }
    if (part === '\n' || part === '\r\n') { outLine.textContent += '\n'; if (cls === 'e') linkTraceback(outLine); outLine = null; }
    else outLine.textContent += part;
  }
  while (out.childNodes.length > 4000) out.firstChild.remove();
  if (atBottom) out.scrollTop = out.scrollHeight;
}

// Figures and drawings from the console (repl_child.py). An <img> never runs an SVG's scripts. Only the newest
// MAX_IMAGES stay, so a plotting loop cannot fill the page's memory.
const MAX_IMAGES = 40;
function appendImage({mime, data, width, height}) {
  const out = $('console-out');
  const atBottom = out.scrollHeight - out.scrollTop - out.clientHeight < 30;
  const img = el('img', 'plot');
  img.src = `data:${mime};base64,${data}`;
  if (width) img.style.width = width + 'px';
  if (width && height) img.style.aspectRatio = `${width} / ${height}`;  // keeps its place while it decodes, so scrolling does not jump
  img.addEventListener('contextmenu', (ev) => showMenu(ev, [['Copy image', () => copyImage(img)]]));
  out.append(img);
  outLine = null;
  keepNewest(out);
  if (atBottom) { out.scrollTop = out.scrollHeight; img.addEventListener('load', () => { out.scrollTop = out.scrollHeight; }, {once: true}); }
}

// Markdown from the console (_repr_markdown_, e.g. IPython's Markdown), with the glycans GlycoDraw's as_markdown() puts in its
// text. It counts as one figure towards MAX_IMAGES.
function appendMarkdown(md) {
  const out = $('console-out');
  const atBottom = out.scrollHeight - out.scrollTop - out.clientHeight < 30;
  const box = el('div', 'md');
  box.innerHTML = markdownHtml(md, true);
  for (const img of box.querySelectorAll('img')) {
    img.addEventListener('contextmenu', (ev) => showMenu(ev, [['Copy image', () => copyImage(img)]]));
    if (atBottom) img.addEventListener('load', () => { out.scrollTop = out.scrollHeight; }, {once: true});
  }
  out.append(box);
  outLine = null;
  keepNewest(out);
  if (atBottom) out.scrollTop = out.scrollHeight;
}

function keepNewest(out) {
  const shown = out.querySelectorAll('img.plot, div.md');
  for (let i = 0; i < shown.length - MAX_IMAGES; i++) shown[i].replaceWith(el('span', 'sys', shown[i].tagName === 'IMG' ? '(older figure removed)\n' : '(older output removed)\n'));
}

async function copyImage(img) {
  const canvas = document.createElement('canvas');  // the clipboard takes PNG only, so an SVG is drawn first
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  canvas.getContext('2d').drawImage(img, 0, 0);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
  try {
    await navigator.clipboard.write([new ClipboardItem({'image/png': blob})]);
    toast('Image copied', 'info');
  } catch (e) {
    toast(`Could not copy the image: ${errMsg(e)}`);
  }
}

window.slate = {
  openPaths: (items) => openPaths(items),
  consoleEvents(batch) {
    for (const [kind, payload] of batch) {
      if (kind === 'o' || kind === 'e') appendOut(payload, kind);
      else if (kind === 'img') appendImage(payload);
      else if (kind === 'md') appendMarkdown(payload);
      else if (kind === 'ready') { appendOut(`Python ${payload} · variables persist until Restart · Enter runs, Shift+Enter adds a line\n`, 'sys'); nameConsole(payload); }
      else if (kind === 'sys') appendOut(payload + '\n', 'sys');
      else if (kind === 'done') consoleBusy = Math.max(0, consoleBusy - 1);
      // exit() sends 'exit' and then 'stopped' with code 0, which needs no second line
      else if (kind === 'exit') appendOut('exit() called; the next command starts a fresh interpreter\n', 'sys');
      else if (kind === 'stopped') { consoleBusy = 0; if (payload !== 0) appendOut(`interpreter stopped${payload != null ? ` (exit code ${payload})` : ''}\n`, 'sys'); }
    }
    $('console-busy').classList.toggle('hidden', consoleBusy === 0);
  },
};

// The console's header names the Python it runs: '3.11.5 · glycowork\.venv', or a conda environment's folder.
function nameConsole(ready) {
  const m = ready.match(/^(\S+) \((.*)\)$/);
  if (!m) return;
  let home = dirname(m[2]);
  if (['scripts', 'bin'].includes(basename(home).toLowerCase())) home = dirname(home);
  const name = /^\.?v?env$/i.test(basename(home)) ? basename(dirname(home)) + S.sep + basename(home) : basename(home);
  $('console-title').textContent = `Python console · ${m[1]} · ${name}`;
  $('console-title').title = m[2];
}

// A traceback's 'File "...", line N' opens the file at that line.
function linkTraceback(span) {
  const m = span.textContent.match(/^\s*File "([^"<>]+)", line (\d+)/);
  if (!m) return;
  span.classList.add('link');
  span.title = 'Open at this line';
  span.addEventListener('click', (ev) => { ev.stopPropagation(); openFile(m[1], {line: +m[2]}); });
}

function restartConsole() {
  consoleBusy = 0;
  $('console-busy').classList.add('hidden');
  appendOut('restarting…\n', 'sys');
  consoleStarted = true;
  api.console_restart(consoleCwd(), S.roots);
}

function clearConsole() {
  $('console-out').innerHTML = '';
  outLine = null;
}

function toggleConsole(show = !S.consoleOpen, focus = true) {
  S.consoleOpen = show;
  layout();
  if (show) {
    if (!consoleStarted) { consoleStarted = true; api.console_restart(consoleCwd(), S.roots); }
    if (focus) setTimeout(() => consoleInput.focus(), 0);
  } else if (S.active) {
    editor.focus();
  }
  persist();
}

// ---------------------------------------------------------------- cells, and running code from the editor in the console

// A line starting with '# %%' begins a cell, as in VS Code, Spyder and PyCharm; the lines before the first one are a cell too.
const CELL_HEAD = '^[ \\t]*#[ \\t]*%%';
const cellHeads = (model) => model.findMatches(CELL_HEAD, false, true, false, null, false).map((m) => m.range.startLineNumber);
const linesOf = (model, from, to) => model.getValueInRange(new monaco.Range(from, 1, to, model.getLineMaxColumn(to)), monaco.editor.EndOfLinePreference.LF);

// A rule above each cell, and a ▶ in the gutter that runs it; redrawn 300 ms after typing stops, in Python tabs only.
function markCells(tab) {
  tab.cellDecos = tab.model.deltaDecorations(tab.cellDecos || [], cellHeads(tab.model).map((n) => ({range: new monaco.Range(n, 1, n, 1),
    options: {isWholeLine: true, className: 'cell-head', linesDecorationsClassName: 'cell-run'}})));
}

function markCellsSoon(tab) {
  clearTimeout(tab.cellTimer);
  tab.cellTimer = setTimeout(() => tab.model.isDisposed() || markCells(tab), 300);
}

// Lines indented as a block (a selection inside a function) run from the left margin; line numbers stay the file's.
function dedent(code) {
  const indent = Math.min(...code.split('\n').filter((l) => l.trim()).map((l) => l.match(/^[ \t]*/)[0].length));
  return indent > 0 && indent < Infinity ? code.split('\n').map((l) => l.slice(Math.min(indent, l.match(/^[ \t]*/)[0].length))).join('\n') : code;
}

// What Ctrl+Enter runs: the selection (whole lines once it spans several); else the cell at the cursor in a file with
// cells; else the statement at the cursor: its line, and the next ones while a bracket or block is still open.
function codeAt(model, sel) {
  if (!sel.isEmpty()) {
    if (sel.startLineNumber === sel.endLineNumber) return {code: dedent(model.getValueInRange(sel)), line: sel.startLineNumber, end: sel.endLineNumber};
    const end = sel.endColumn === 1 ? sel.endLineNumber - 1 : sel.endLineNumber;  // a selection ending at a line's start leaves that line out
    return {code: dedent(linesOf(model, sel.startLineNumber, end)), line: sel.startLineNumber, end};
  }
  const at = sel.positionLineNumber, heads = cellHeads(model);
  if (heads.length) {
    const start = heads.filter((n) => n <= at).pop() || 1, next = heads.find((n) => n > at);
    return {code: linesOf(model, start, next ? next - 1 : model.getLineCount()), line: start, end: next ? next - 1 : model.getLineCount(), cell: true};
  }
  let end = at;
  while (end < model.getLineCount() && needsMoreLines(linesOf(model, at, end))) end++;
  return {code: dedent(linesOf(model, at, end)), line: at, end};
}

// Run code of a tab in the console, with the Python set for the tab's project (Api.console_run). Tracebacks give the file's
// own line numbers, and the console's working directory becomes the file's folder (repl_child.run_file).
function runInConsole(tab, code, {line = 1, whole = false, what = ''} = {}) {
  const root = rootIndexOf(tab.path);
  consoleStarted = true;  // before toggleConsole, which would otherwise start a console of its own alongside this run's
  if (!S.consoleOpen) toggleConsole(true, false);
  if (!whole && !code.includes('\n')) {
    echo(code);
    if (S.history[S.history.length - 1] !== code) S.history = [...S.history, code].slice(-300);
  } else {
    appendOut(`▶ ${basename(tab.path)}${what}\n`, 'run');
  }
  consoleBusy++;
  $('console-busy').classList.remove('hidden');
  const source = {file: tab.path, line, whole};
  if (!whole) source.text = tab.model.getValue(monaco.editor.EndOfLinePreference.LF);  // for tracebacks, which quote the editor's lines
  api.console_run(code, root >= 0 ? S.roots[root] : dirname(tab.path), S.roots, source);
  persist();
}

function runAtCursor(advance) {
  const tab = S.active, model = editor.getModel();
  if (!tab || model !== tab.model) return;
  const part = codeAt(model, editor.getSelection());
  if (part.code.trim()) {
    const what = part.cell ? `, cell at line ${part.line}` : part.end > part.line ? `, lines ${part.line}-${part.end}` : '';
    runInConsole(tab, part.code, {line: part.line, what});
  }
  if (!advance) return;
  let next = part.end + 1;  // Shift+Enter goes on to the next cell's first line, or the next line with code
  if (part.cell) next = Math.min(next + 1, model.getLineCount());
  else while (next < model.getLineCount() && !model.getLineContent(next).trim()) next++;
  if (next > model.getLineCount()) next = model.getLineCount();
  editor.setPosition({lineNumber: next, column: model.getLineFirstNonWhitespaceColumn(next) || 1});
  editor.revealLine(next);
}

function runFile() {
  const tab = S.active;
  if (!tab || tab.model.getLanguageId() !== 'py') { toast('Open a Python file to run it'); return; }
  runInConsole(tab, tab.model.getValue(monaco.editor.EndOfLinePreference.LF), {whole: true});
}

function setupRunning() {
  const K = monaco.KeyCode, M = monaco.KeyMod;
  const when = "editorLangId == 'py' && !slateConsole && !editorReadonly && !suggestWidgetVisible";  // see setupConsole: keybindings are page-wide
  editor.addAction({id: 'slate.run', label: 'Run selection or cell in console', keybindings: [M.CtrlCmd | K.Enter, M.Alt | M.Shift | K.KeyE],
    precondition: when, contextMenuGroupId: 'slate-run', contextMenuOrder: 1, run: () => runAtCursor(false)});
  editor.addAction({id: 'slate.runNext', label: 'Run selection or cell, and go on', keybindings: [M.Shift | K.Enter],
    precondition: when, contextMenuGroupId: 'slate-run', contextMenuOrder: 2, run: () => runAtCursor(true)});
  editor.addAction({id: 'slate.runFile', label: 'Run file in console (F5)', precondition: "editorLangId == 'py'",
    contextMenuGroupId: 'slate-run', contextMenuOrder: 3, run: runFile});
  editor.onMouseDown((e) => {
    const n = e.target.position && e.target.position.lineNumber;
    if (e.target.type !== monaco.editor.MouseTargetType.GUTTER_LINE_DECORATIONS || !n || !S.active) return;
    if (!new RegExp(CELL_HEAD).test(editor.getModel().getLineContent(n))) return;
    editor.setPosition({lineNumber: n, column: 1});
    runAtCursor(false);
  });
}

// ---------------------------------------------------------------- settings (Ctrl+Alt+S) and updates

// The settings that took editing state.json by hand. Python keeps them (Api.set_setting); autosave is the page's own.
async function openSettings() {
  if ($('settings')) return;
  let info;
  try { info = await api.settings(S.roots); } catch (e) { toast(errMsg(e)); return; }
  const v = info.values, box = el('div'), body = el('div', 'settings-body');
  box.id = 'settings';
  const head = el('div', 'diff-head');
  const closeButton = el('button', null, '✕');
  head.append(el('span', 'diff-title', 'Settings'), el('span', 'spacer'), closeButton);
  box.append(head, body);
  const section = (title) => body.append(el('div', 'settings-section', title));
  const row = (label, control, note) => {
    const r = el('label', 'settings-row');
    r.append(el('span', 'settings-label', label), control);
    if (note) r.append(el('span', 'settings-note', note));
    body.append(r);
    return r;
  };
  const check = (label, on, change, note) => {
    const c = el('input');
    c.type = 'checkbox';
    c.checked = on;
    c.onchange = () => change(c.checked);
    const r = row('', c, note);
    r.querySelector('.settings-label').remove();
    c.after(el('span', null, label));
    return c;
  };
  const set = (key, value) => api.set_setting(key, value).catch((e) => toast(errMsg(e)));

  section('Python console');
  const pythons = info.pythons, own = info.own;
  const pythonSelect = (current, first) => {
    const s = el('select');
    const option = (value, text, title) => { const o = el('option', null, text); o.value = value; o.title = title || value; s.append(o); };
    option('', first, own);
    for (const p of pythons) option(p.path, `${p.label}${p.version ? ' · ' + p.version : ''}`, p.path);
    if (current && !pythons.some((p) => keyOf(p.path) === keyOf(current))) option(current, current);
    option('?', 'Browse…');
    s.value = current ? (pythons.find((p) => keyOf(p.path) === keyOf(current)) || {path: current}).path : '';
    s.dataset.value = s.value;
    s.title = s.value || own;
    return s;
  };
  const choose = async (s, store) => {
    let value = s.value;
    if (value === '?') {
      value = await api.pick_python();
      if (!value) { s.value = s.dataset.value; return; }
      if (![...s.options].some((o) => o.value === value)) { const o = el('option', null, value); o.value = value; s.insertBefore(o, s.lastChild); }
      s.value = value;
    }
    s.dataset.value = value;
    s.title = value || own;
    await store(value || null);
    $('settings-restart').classList.remove('hidden');
  };
  const fallback = pythonSelect(v.console_python, `slate's own (${S.python})`);
  fallback.onchange = () => choose(fallback, (value) => set('console_python', value));
  row('Default', fallback);
  const perProject = {...(v.console_pythons || {})};
  for (const root of new Map(S.roots.map((r) => [keyOf(r), r])).values()) {
    const s = pythonSelect(perProject[keyOf(root)], 'Default');
    s.onchange = () => choose(s, (value) => {
      if (value) perProject[keyOf(root)] = value;
      else delete perProject[keyOf(root)];
      return set('console_pythons', Object.keys(perProject).length ? {...perProject} : null);
    });
    row(basename(root), s).querySelector('.settings-label').style.color = rootColor(rootIndexOf(root));
  }
  const restart = el('button', null, 'Restart the console now');
  restart.id = 'settings-restart';
  restart.className = 'hidden';
  restart.onclick = () => { restart.classList.add('hidden'); if (!S.consoleOpen) toggleConsole(true, false); restartConsole(); };
  body.append(el('div', 'settings-note', keyText("A project's Python runs code from its files (Ctrl+Enter, F5) and the console when it starts or restarts with one of its files in front. Any Python 3.8 or newer works; packages come from it.")), restart);

  section('Editor');
  check('Save changed files when slate loses focus', S.autosave, (on) => { S.autosave = on; persist(); });
  check('Glycan drawings next to glycans in the code', !glycans.off && v.glycan_drawings !== false, (on) => {
    glycans.off = !on;
    set('glycan_drawings', on ? null : false);
    refreshGlycans();
  }, 'GlycoDraw, from the default Python above or a glycowork folder');
  const style = el('input');
  style.placeholder = ['Documents', 'glycowork', '.github', 'scripts', 'check_style.py'].join(S.sep);
  style.value = v.style_script || '';
  style.spellcheck = false;
  style.onchange = async () => { await set('style_script', style.value.trim() || null); if (S.active && S.active.model.getLanguageId() === 'py') lint(S.active); };
  row('Style checker', style, 'for files outside a project with its own .github/scripts/check_style.py');

  section('slate');
  check('GPU rendering', v.gpu !== false, (on) => set('gpu', on ? null : false), 'off saves about 100 MB, but typing is slower on high-DPI screens; from the next start');
  check('Usage log', v.usage_log !== false, (on) => set('usage_log', on ? null : false), 'local only, never sent: python -m slate.usage');
  check('Check GitHub for a new version once a day', v.update_check !== false, (on) => set('update_check', on ? null : false));
  body.append(el('div', 'settings-note', `slate ${info.version} · Python ${S.python} · settings are kept in ${info.state_file}`));

  document.body.append(box);
  const close = () => { box.remove(); removeEventListener('keydown', esc, true); removeEventListener('mousedown', outside, true); if (S.active) editor.focus(); };
  const esc = (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); } };
  const outside = (e) => { if (!box.contains(e.target)) close(); };
  closeButton.onclick = close;
  addEventListener('keydown', esc, true);
  addEventListener('mousedown', outside, true);
}

// Markdown's common lines (headings, bullets, numbered lists, paragraphs, `code`, **bold**, *italics*) as HTML: CHANGELOG.md, and
// with `images` the console's Markdown, where an <img> with an image data URL (GlycoDraw's as_markdown) is kept. Everything else
// is escaped, and links stay text: following one would take the window away from slate.
function markdownHtml(md, images = false) {
  const kept = [];
  const keep = (html) => `\u0000${kept.push(html) - 1}\u0000`;
  if (images) md = md.replace(/<img\s[^>]*>/gi, (tag) => {
    const src = tag.match(/\ssrc="(data:image\/(svg\+xml|png|jpeg);base64,([A-Za-z0-9+/=]+))"/i);
    if (!src) return tag;
    const height = tag.match(/\sheight="(\d+(?:\.\d+)?)"/i), alt = tag.match(/\salt="([^"]*)"/i);
    let sticker = false;  // GlycoDraw(sticker = True) brings its own white outline; other drawings get a white chip, since GlycoDraw draws charcoal on nothing
    try { sticker = src[2] === 'svg+xml' && atob(src[3]).includes('feDropShadow'); } catch (e) { }
    return keep(`<img class="${sticker ? 'sticker' : 'chip'}" src="${src[1]}" alt="${escapeHtml(alt ? alt[1] : '')}"${height ? ` style="height: ${height[1]}px"` : ''}>`);
  });
  const inline = (s) => escapeHtml(s.replace(/`([^`]+)`/g, (m, code) => keep(`<code>${escapeHtml(code)}</code>`)))
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>').replace(/\*([^*\s][^*]*)\*/g, '<i>$1</i>').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1');
  const block = (b) => {
    const heading = b.match(/^(#{1,4}) (.*)(?:\n([\s\S]*))?/);
    if (heading) return `<h${heading[1].length + 2}>${inline(heading[2])}</h${heading[1].length + 2}>${heading[3] ? block(heading[3]) : ''}`;
    if (/^[-*] /.test(b)) return `<ul>${b.split(/\n(?=[-*] )/).map((item) => `<li>${inline(item.slice(2))}</li>`).join('')}</ul>`;
    if (/^\d+\. /.test(b)) return `<ol>${b.split(/\n(?=\d+\. )/).map((item) => `<li>${inline(item.replace(/^\d+\. /, ''))}</li>`).join('')}</ol>`;
    return `<p>${inline(b)}</p>`;
  };
  return md.trim().split(/\n\s*\n/).map(block).join('').replace(/\u0000(\d+)\u0000/g, (m, i) => kept[i]);
}

// What changed, in an overlay like the settings panel, with `actions` ([label, fn]) next to Close.
function showNotes(title, md, actions = []) {
  if ($('notes')) $('notes').remove();
  const box = el('div'), head = el('div', 'diff-head'), body = el('div', 'notes-body');
  box.id = 'notes';
  head.append(el('span', 'diff-title', title), el('span', 'spacer'));
  body.innerHTML = markdownHtml(md);
  box.append(head, body);
  document.body.append(box);
  const close = () => { box.remove(); removeEventListener('keydown', esc, true); removeEventListener('mousedown', outside, true); if (S.active) editor.focus(); };
  const esc = (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); } };
  const outside = (e) => { if (!box.contains(e.target)) close(); };
  for (const [label, fn] of [...actions, ['Close', () => {}]]) {
    const b = el('button', null, label);
    b.onclick = () => { close(); fn(); };
    head.append(b);
  }
  addEventListener('keydown', esc, true);
  addEventListener('mousedown', outside, true);
}

// The first start of a newer release shows "Updated to slate X" in the status bar once, which opens what changed
// (Api.whats_new); otherwise a line when GitHub has a newer release than this one (Api.update_check, at most once a day),
// which opens what it changes and installs it.
async function checkUpdate() {
  const notice = $('st-update');
  let fresh = null;
  try { fresh = await api.whats_new(); } catch (e) {}
  if (fresh) {
    notice.textContent = `Updated to slate ${fresh.version}: what's new`;
    notice.title = 'What changed since the version you had';
    notice.classList.remove('hidden');
    notice.onclick = () => showNotes(`What's new in slate ${fresh.version}`, fresh.notes);
    return;
  }
  let version = null;
  try { version = await api.update_check(); } catch (e) { return; }
  if (!version) return;
  notice.textContent = `slate ${version} is available`;
  notice.title = 'Click to see what changed and install it; it runs from the next start of slate';
  notice.classList.remove('hidden');
  const install = async () => {
    notice.onclick = null;
    notice.textContent = `Installing slate ${version}…`;
    try {
      notice.textContent = `slate ${await api.update_install(version)} installed: restart slate to use it`;
      notice.title = '';
    } catch (e) {
      toast(`The update failed: ${errMsg(e)}`);
      notice.classList.add('hidden');
    }
  };
  notice.onclick = async () => {
    let md = null;
    try { md = await api.update_notes(version); } catch (e) {}
    showNotes(`slate ${version} is available`, `${md || 'GitHub could not be reached for the notes.'}\n\n` +
      'The open window keeps running this version until slate is restarted.', [['Install', install]]);
  };
}

// ---------------------------------------------------------------- personal extensions

// Every .js file in %APPDATA%\slate\extensions (Api.extensions) runs once at startup with `host` below. That folder is
// outside slate's repo, so an extension reaches no one else, and without it nothing is loaded. An extension runs with the
// page's full rights: only ever put your own files there.
const handlers = {};
const on = (name, fn) => { (handlers[name] = handlers[name] || []).push(fn); };

function emit(name, ...args) {
  for (const fn of handlers[name] || []) {
    try { fn(...args); } catch (e) { note('js_error', {message: `extension ${name} handler: ${errMsg(e)}`.slice(0, 300), stack: e.stack}); }
  }
}

// A sidebar view next to Folders and Search; returns its element for the extension to fill.
function addView(id, title) {
  const button = el('button', null, title);
  button.dataset.view = id;
  button.onclick = () => showView(id);
  $('side-tabs').append(button);
  const view = el('section', 'view hidden');
  view.id = id + '-view';
  $('sidebar').append(view);
  return {element: view, button, visible: () => S.view === id && S.sidebarOpen};
}

// Two texts side by side in Monaco's diff editor, made on demand and disposed on close. actions: [[label, fn], ...].
function showDiff(original, modified, {title = '', language = 'plaintext', actions = []} = {}) {
  const box = el('div');
  box.id = 'diff';
  const head = el('div', 'diff-head');
  head.append(el('span', 'diff-title', title), el('span', 'spacer'));
  const body = el('div', 'diff-body');
  box.append(head, body);
  document.body.append(box);
  const left = monaco.editor.createModel(original, language), right = monaco.editor.createModel(modified, language);
  const view = monaco.editor.createDiffEditor(body, {theme: 'darcula', fontFamily: FONT, fontSize: 13, lineHeight: 20, readOnly: true,
    automaticLayout: true, minimap: {enabled: false}, scrollBeyondLastLine: false, renderSideBySide: true});
  view.setModel({original: left, modified: right});
  const first = view.onDidUpdateDiff(() => {  // open at the first change, not at the top of the file; once, so scrolling stays yours
    first.dispose();
    const change = (view.getLineChanges() || [])[0];
    if (change) view.getModifiedEditor().revealLineInCenter(Math.max(change.modifiedStartLineNumber, 1));
  });
  const close = () => { view.dispose(); left.dispose(); right.dispose(); box.remove(); removeEventListener('keydown', esc, true); if (S.active) editor.focus(); };
  const esc = (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); } };
  addEventListener('keydown', esc, true);
  for (const [label, fn] of [...actions, ['Close', () => {}]]) {
    const b = el('button', null, label);
    b.onclick = () => { close(); fn(); };
    head.append(b);
  }
  return close;
}

const host = {
  get api() { return api; }, get monaco() { return monaco; }, get editor() { return editor; },
  roots: () => [...S.roots], tabFor: (path) => S.tabs.find((t) => t.key === keyOf(path)) || null,
  openFile, revealAt, toast, el, escapeHtml, keyOf, join, basename, dirname, relPath, rootColor, rootIndexOf,
  showMenu, addView, showDiff, on,
};

async function loadExtensions() {
  let found = [];
  try { found = await api.extensions(); } catch (e) { return; }
  for (const ext of found) {
    try {
      new Function('host', `${ext.code}\n//# sourceURL=${encodeURI('slate-extension/' + ext.name)}`)(host);  // named in stack traces and the dev tools
    } catch (e) {
      toast(`Extension ${ext.name} failed: ${errMsg(e)}`);
      note('js_error', {message: `extension ${ext.name}: ${errMsg(e)}`.slice(0, 300), stack: e.stack});
    }
  }
}

// ---------------------------------------------------------------- layout, status, keys

function layout() {
  $('sidebar').style.width = S.sidebarWidth + 'px';
  $('sidebar').classList.toggle('hidden', !S.sidebarOpen);
  $('side-sash').classList.toggle('hidden', !S.sidebarOpen);
  $('console').style.height = S.consoleHeight + 'px';
  $('console').classList.toggle('hidden', !S.consoleOpen);
  $('console-sash').classList.toggle('hidden', !S.consoleOpen);
}

function dragSash(sash, onMove) {
  sash.addEventListener('mousedown', (ev) => {
    ev.preventDefault();
    const move = (e) => onMove(e);
    const up = () => { removeEventListener('mousemove', move); removeEventListener('mouseup', up); persist(); };
    addEventListener('mousemove', move);
    addEventListener('mouseup', up);
  });
}

function updateStatus() {
  const tab = S.active;
  $('st-path').textContent = tab ? relPath(tab.path) : '';
  $('st-path').title = tab ? tab.path : '';
  const lint = $('st-lint');
  lint.className = '';
  if (tab && tab.model.getLanguageId() === 'py') {
    const error = (tab.markers || []).find((m) => m.severity === 'error');
    const n = (tab.markers || []).length;
    lint.textContent = error ? `✕ ${error.code} line ${error.line}` : n ? `⚠ ${n} style` : tab.lintNote ? 'style: n/a' : '✓ style';
    lint.title = tab.lintNote || 'Style findings in this file (F8: next)';
    if (error) lint.classList.add('err');
    else if (n) lint.classList.add('warn');
  } else {
    lint.textContent = '';
  }
  $('st-eol').textContent = tab ? (tab.model.getEOL() === '\r\n' ? 'CRLF' : 'LF') : '';
  $('st-enc').textContent = tab ? (tab.encoding === 'utf-8' ? 'UTF-8' : tab.encoding.toUpperCase()) : '';
  $('st-lang').textContent = tab ? (tab.model.getLanguageId() === 'py' ? `Python ${S.python}` : tab.model.getLanguageId()) : '';
  updateStatusPos();
}

function updateStatusPos() {
  if (!S.active || !editor.getModel()) { $('st-pos').textContent = ''; return; }
  const sel = editor.getSelection();
  const n = editor.getModel().getValueLengthInRange(sel);
  $('st-pos').textContent = `Ln ${sel.positionLineNumber}, Col ${sel.positionColumn}` + (n ? ` (${n} selected)` : '');
}

const persist = debounce(() => {
  if (!S.ready) return;  // a save halfway through restoring would drop the tabs not yet back
  api.save_state({
    roots: S.roots,
    tabs: S.tabs.map((t) => ({path: t.path, viewState: t === S.active ? editor.saveViewState() : t.viewState})),
    active: S.active ? S.active.path : null,
    mru: S.mru, expanded: [...S.expanded], closed: S.closed, history: S.history, view: S.view,
    sidebarWidth: S.sidebarWidth, sidebarOpen: S.sidebarOpen, consoleHeight: S.consoleHeight, consoleOpen: S.consoleOpen,
    autosave: S.autosave,
  });
}, 500);

// 'ctrl+shift+f' and the like. On a Mac, Cmd stands in for Ctrl, as Monaco's own keys do; the Ctrl key itself is 'macctrl',
// which keeps Ctrl+Tab, Ctrl+` and Ctrl+R as on a Mac's VS Code and PyCharm. Option changes the character a letter types
// (Option+S is ß), so with Option the physical key counts.
function comboOf(e) {
  let key = e.key.toLowerCase();
  if (e.code === 'Backquote' || (e.ctrlKey && (e.key === 'ö' || e.key === 'Ö'))) key = '`';  // Nordic layouts: the § key or Ö, as in VS Code
  if (e.code === 'Tab') key = 'tab';
  if (!S.mac) return (e.ctrlKey ? 'ctrl+' : '') + (e.shiftKey ? 'shift+' : '') + (e.altKey ? 'alt+' : '') + key;
  if (e.altKey && /^Key[A-Z]$/.test(e.code)) key = e.code.slice(3).toLowerCase();
  return (e.ctrlKey ? 'macctrl+' : '') + (e.metaKey ? 'ctrl+' : '') + (e.shiftKey ? 'shift+' : '') + (e.altKey ? 'alt+' : '') + key;
}

// The hints in index.html (titles, the empty editor's keys) with a Mac's key names.
function macLabels() {
  for (const e of document.querySelectorAll('[title]')) e.title = keyText(e.title);
  for (const row of document.querySelectorAll('#empty > div')) {
    const keys = [...row.querySelectorAll('kbd')];
    const combo = keyText(keys.map((k) => k.textContent).join('+'));  // '⌘⇧F'
    keys.forEach((k, i) => { k.textContent = i ? '' : combo; if (i) k.remove(); });
  }
}

function bindUi() {
  const keys = {
    'ctrl+p': openFinder, 'ctrl+e': openFinder,
    'ctrl+shift+f': () => showView('search'), 'ctrl+shift+e': () => showView('files'),
    'ctrl+b': () => { S.sidebarOpen = !S.sidebarOpen; layout(); persist(); },
    'ctrl+`': () => toggleConsole(), 'ctrl+s': () => save(), 'ctrl+w': () => closeTab(S.active), 'ctrl+f4': () => closeTab(S.active),
    'ctrl+shift+t': () => { const p = S.closed.pop(); if (p) { openFile(p); persist(); } },
    'ctrl+tab': () => mruStep(1), 'ctrl+shift+tab': () => mruStep(-1),
    'ctrl+pagedown': () => cycleTab(1), 'ctrl+pageup': () => cycleTab(-1),
    'alt+shift+l': revealActiveInTree,
    'f5': runFile, 'shift+f10': runFile,  // F5 also no longer reloads the page
    'ctrl+alt+s': openSettings,  // PyCharm's
    'ctrl+alt+arrowleft': () => goPlace(-1), 'ctrl+alt+arrowright': () => goPlace(1),  // PyCharm's back and forward
    'alt+arrowleft': () => goPlace(-1), 'alt+arrowright': () => goPlace(1),  // VS Code's
    'ctrl+f12': fileStructure,  // PyCharm's file structure popup
  };
  if (S.mac) {  // Cmd stands in for Ctrl (comboOf), except where a Mac's VS Code and PyCharm keep the Ctrl key itself
    for (const k of ['ctrl+tab', 'ctrl+shift+tab', 'ctrl+`']) { keys['mac' + k] = keys[k]; delete keys[k]; }
    Object.assign(keys, {'ctrl+,': openSettings, 'macctrl+r': runFile, 'ctrl+alt+arrowright': () => cycleTab(1), 'ctrl+alt+arrowleft': () => cycleTab(-1)});
    Object.assign(keys, {'macctrl+-': () => goPlace(-1), 'macctrl+shift+-': () => goPlace(1), 'macctrl+shift+_': () => goPlace(1)});  // a Mac's VS Code; Option+arrows move by word there
    delete keys['ctrl+f4'];
    delete keys['alt+arrowleft'];
    delete keys['alt+arrowright'];
  }
  addEventListener('keydown', (e) => {
    if (!$('finder').classList.contains('hidden')) {
      if (e.key === 'Escape') { e.preventDefault(); closeFinder(); return; }
      if (e.key === 'ArrowDown') { e.preventDefault(); moveFinder(1); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); moveFinder(-1); return; }
      if (e.key === 'Enter') { e.preventDefault(); pickFinder(finderSel); return; }
    }
    const combo = comboOf(e), fn = keys[combo];
    if (fn) { e.preventDefault(); e.stopPropagation(); usage.keys[combo] = (usage.keys[combo] || 0) + 1; fn(); }
  }, true);
  addEventListener('keyup', (e) => {
    if (e.key === 'Control' && mruCycle !== null) {
      mruCycle = null;
      if (S.active) S.mru = [S.active.key, ...S.mru.filter((k) => k !== S.active.key)];
      notePlace();  // the tab Ctrl+Tab settled on; the ones passed on the way are no places
    }
  });
  // the mouse's back and forward buttons; on mouseup, where the browser would otherwise go back in its history
  addEventListener('mouseup', (e) => { if (e.button === 3 || e.button === 4) { e.preventDefault(); goPlace(e.button === 3 ? -1 : 1); } });
  editor.onDidChangeCursorPosition(notePlace);
  addEventListener('mousedown', (e) => { if (!$('menu').contains(e.target)) hideMenu(); });
  $('finder-input').addEventListener('input', renderFinder);
  $('finder').addEventListener('focusout', (e) => { if (!$('finder').contains(e.relatedTarget)) setTimeout(() => { if (!$('finder').contains(document.activeElement)) $('finder').classList.add('hidden'); }, 100); });
  $('tabs').addEventListener('wheel', (e) => {
    if (e.deltaX || $('tabs').scrollWidth <= $('tabs').clientWidth) return;
    e.preventDefault();
    $('tabs').scrollLeft += e.deltaY;
  }, {passive: false});
  let tabsWidth = 0;
  new ResizeObserver(() => { if ($('tabs').clientWidth !== tabsWidth) { tabsWidth = $('tabs').clientWidth; fitTabs(); } }).observe($('tabs'));
  $('add-root').onclick = addRoot;
  for (const b of document.querySelectorAll('#side-tabs button')) b.onclick = () => showView(b.dataset.view);
  const searchSoon = debounce(runSearch, 350);
  $('search-input').addEventListener('input', searchSoon);
  $('search-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') runSearch(); });
  for (const opt of ['case', 'regex', 'glycan']) {
    $('search-' + opt).onclick = (e) => { search[opt] = !search[opt]; e.target.classList.toggle('on', search[opt]); glycanMode(); runSearch(); };
  }
  $('st-settings').onclick = openSettings;
  $('st-lint').onclick = () => { if (S.active) { editor.focus(); editor.trigger('status', 'editor.action.marker.nextInFiles'); } };
  dragSash($('side-sash'), (e) => { S.sidebarWidth = Math.max(140, Math.min(e.clientX, innerWidth - 300)); layout(); });
  dragSash($('console-sash'), (e) => { S.consoleHeight = Math.max(80, Math.min(innerHeight - e.clientY - 22, innerHeight - 150)); layout(); });
  editor.addAction({id: 'slate.reveal', label: 'Reveal in folder tree', keybindings: [monaco.KeyMod.Alt | monaco.KeyMod.Shift | monaco.KeyCode.KeyL],
    contextMenuGroupId: 'navigation', run: revealActiveInTree});
}
