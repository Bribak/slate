// slate's page: tabs, folder tree, finder, find-in-files and the console around one Monaco editor.
// Python does the disk, style, jedi and interpreter work behind window.pywebview.api (slate/app.py).
'use strict';

const $ = (id) => document.getElementById(id);
const ROOT_COLORS = ['#4A88C7', '#CC7832', '#6A8759', '#9876AA', '#BBB529', '#E06C75', '#56B6C2', '#D19A66'];
const FONT = "'slate mono', 'JetBrains Mono', 'Cascadia Mono', Consolas, monospace";  // 'slate mono' = PyCharm's own JetBrains Mono files, see loadFonts
const S = {
  roots: [], tabs: [], active: null, mru: [], expanded: new Set(), closed: [], history: [],
  view: 'files', sidebarWidth: 260, sidebarOpen: true, consoleHeight: 240, consoleOpen: false, autosave: true,
  ready: false,  // the saved session is restored
  pathOf: new Map(),  // model uri -> real path, since Monaco's uris lower-case the drive letter
};
let api, monaco, editor, consoleInput;

const keyOf = (p) => p.replace(/\//g, '\\').replace(/\\+$/, '').toLowerCase();
const basename = (p) => p.replace(/[\\/]+$/, '').split(/[\\/]/).pop();
const dirname = (p) => p.replace(/[\\/][^\\/]*$/, '');
const join = (a, b) => a.replace(/[\\/]+$/, '') + '\\' + b;
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

function boot() {
  if (boot.done) return;
  boot.done = true;
  api = track(window.pywebview.api);
  api.get_state().then((info) => {
    S.python = info.python;
    S.home = info.home;
    window.MonacoEnvironment = {getWorkerUrl() {
      const base = new URL('vendor/', location.href).href;
      return 'data:text/javascript;charset=utf-8,' + encodeURIComponent(
        `self.MonacoEnvironment = {baseUrl: '${base}'}; importScripts('${base}vs/base/worker/workerMain.js');`);
    }};
    require.config({paths: {vs: 'vendor/vs'}});
    require(['vs/editor/editor.main'], () => { monaco = window.monaco; init(info.state || {}); });
  });
}

if (window.pywebview && window.pywebview.api && window.pywebview.api.get_state) boot();
else window.addEventListener('pywebviewready', boot);

// Load the JetBrains Mono that PyCharm ships (Python finds it) under the name 'slate mono', before Monaco measures the font.
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
  await loadFonts();
  editor = monaco.editor.create($('editor'), {
    model: null, theme: 'darcula', fontFamily: FONT, fontSize: 13, lineHeight: 20,  // PyCharm's defaults: size 13, line spacing 1.2
    automaticLayout: true, minimap: {enabled: false}, scrollBeyondLastLine: false, bracketPairColorization: {enabled: false},
    guides: {indentation: true, bracketPairs: false}, stickyScroll: {enabled: true}, mouseWheelZoom: true,
    renderWhitespace: 'selection', fixedOverflowWidgets: true, unicodeHighlight: {ambiguousCharacters: false},
    wordBasedSuggestions: 'allDocuments', definitionLinkOpensInPeek: false, 'semanticHighlighting.enabled': false,
  });
  registerProviders();
  editor.onDidChangeCursorSelection(updateStatusPos);
  editor.onDidChangeModelContent(() => persist());
  S.roots = saved.roots || [];
  if (!saved.roots) {  // first run: the folders slate was built around, when they exist
    for (const name of ['glycowork', 'candycrunch']) {
      const p = join(join(S.home, 'Documents'), name);
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
  setupConsole();
  layout();
  await loadExtensions();  // before showView, which may restore a view an extension adds
  showView(S.view);
  renderTree();
  for (const t of saved.tabs || []) await openFile(t.path, {background: true, viewState: t.viewState, quiet: true});
  S.mru = (saved.mru || []).filter((k) => S.tabs.some((t) => t.key === k));
  const active = saved.active && S.tabs.find((t) => t.key === keyOf(saved.active));
  if (active || S.tabs.length) activate(active || S.tabs[0]);
  else renderTabs();
  if (saved.consoleOpen) toggleConsole(true);
  updateStatus();
  S.ready = true;
  drainLints();  // the front tab's check, started by activate, goes first
  await openPaths(await api.page_ready());  // files double-clicked in Explorer, before or while slate was starting
  setInterval(checkDisk, 2000);
  setInterval(flushUsage, 60000);
  usage.events.push({kind: 'ready', ms: Math.round(performance.now()), tabs: S.tabs.length, roots: S.roots.length, dpr: devicePixelRatio});
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
      file = await api.read_file(path);
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
      if (model.getLanguageId() === 'py') lintLater(tab);
    }
  }
  if (opts.background) return tab;
  activate(tab);
  if (opts.line) revealAt(opts.line, opts.col || 1, opts.endCol);
  return tab;
}

// Files from Explorer open as tabs (the last one in front); folders become roots in the tree.
async function openPaths(items) {
  let last = null;
  for (const it of items) {
    if (!it.dir) { last = (await openFile(it.path, {background: true})) || last; continue; }
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
  if (S.active && S.active !== tab && !S.active.model.isDisposed()) S.active.viewState = editor.saveViewState();
  S.active = tab;
  if (mru) S.mru = [tab.key, ...S.mru.filter((k) => k !== tab.key)];
  editor.setModel(tab.model);
  if (!tab.linted && tab.model.getLanguageId() === 'py') lint(tab);  // ahead of the background tabs still queued
  if (tab.viewState) editor.restoreViewState(tab.viewState);
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
  if (tab.model.getLanguageId() === 'py') lintSoon(tab);
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

function closeTab(tab) {
  if (!tab) return;
  if (isDirty(tab) && !confirm(`${basename(tab.path)} has unsaved changes. Close it anyway?`)) return;
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
  const bar = $('tabs');
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
  if (S.active && S.active.el) S.active.el.scrollIntoView({block: 'nearest'});
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
    result = await api.lint(tab.path, tab.model.getValue(monaco.editor.EndOfLinePreference.LF));
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
  if (tab === S.active) updateStatus();
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

function registerProviders() {
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
      openFile(path).then((tab) => {
        if (!tab || !where) return;
        if (where.startLineNumber) revealAt(where.startLineNumber, where.startColumn, where.endLineNumber === where.startLineNumber ? where.endColumn : undefined);
        else revealAt(where.lineNumber, where.column);
      });
      return true;
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
    ['Reveal in Explorer', () => api.reveal(entry.path)],
  ];
}

function dirMenu(entry, isRoot, rootIdx) {
  const items = [
    ['New file…', () => createIn(entry, false)],
    ['New folder…', () => createIn(entry, true)],
    ['Copy path', () => navigator.clipboard.writeText(entry.path)],
    ['Reveal in Explorer', () => api.reveal(join(entry.path, '.'))],
  ];
  if (isRoot) items.push(['Remove from slate', () => removeRoot(rootIdx)]);
  return items;
}

async function createIn(entry, isDir) {
  const name = prompt(isDir ? 'New folder name' : 'New file name (may include subfolders, e.g. scripts/run.py)');
  if (!name) return;
  try {
    const path = await api.create(join(entry.path, name.replace(/\//g, '\\')), isDir);
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
    const rootName = it.i >= 0 ? basename(S.roots[it.i]) + '\\' : '';
    const rel = it.rel.replace(/\//g, '\\');
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
const search = {case: false, regex: false};

async function runSearch() {
  const q = $('search-input').value;
  const seq = ++searchSeq;
  const results = $('search-results');
  if (!q) { results.innerHTML = ''; $('search-status').textContent = ''; return; }
  $('search-status').textContent = 'Searching…';
  let r;
  try {
    r = await api.search(S.roots, q, search.regex, search.case);
  } catch (e) {
    if (seq === searchSeq) $('search-status').textContent = errMsg(e);
    return;
  }
  if (seq !== searchSeq) return;
  results.innerHTML = '';
  $('search-status').textContent = `${r.matches.length} matching line${r.matches.length === 1 ? '' : 's'} in ${r.files} file${r.files === 1 ? '' : 's'}${r.truncated ? ' (stopped at 2000)' : ''}`;
  let current = null;
  for (const m of r.matches) {
    if (m.path !== current) {
      current = m.path;
      const head = el('div', 'sr-file');
      const dot = el('span', 'dot');
      dot.style.cssText = `width:8px;height:8px;border-radius:50%;flex:none;background:${rootColor(m.root)}`;
      const dir = el('span', 'dir', basename(S.roots[m.root]) + '\\' + dirname('\\' + m.rel).replace(/^\\/, ''));
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
    if (part === '\n' || part === '\r\n') { outLine.textContent += '\n'; outLine = null; }
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
  const images = out.querySelectorAll('img.plot');
  for (let i = 0; i < images.length - MAX_IMAGES; i++) images[i].replaceWith(el('span', 'sys', '(older figure removed)\n'));
  if (atBottom) { out.scrollTop = out.scrollHeight; img.addEventListener('load', () => { out.scrollTop = out.scrollHeight; }, {once: true}); }
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
      else if (kind === 'ready') appendOut(`Python ${payload} · variables persist until Restart · Enter runs, Shift+Enter adds a line\n`, 'sys');
      else if (kind === 'done') consoleBusy = Math.max(0, consoleBusy - 1);
      // exit() sends 'exit' and then 'stopped' with code 0, which needs no second line
      else if (kind === 'exit') appendOut('exit() called; the next command starts a fresh interpreter\n', 'sys');
      else if (kind === 'stopped') { consoleBusy = 0; if (payload !== 0) appendOut(`interpreter stopped${payload != null ? ` (exit code ${payload})` : ''}\n`, 'sys'); }
    }
    $('console-busy').classList.toggle('hidden', consoleBusy === 0);
  },
};

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

function toggleConsole(show = !S.consoleOpen) {
  S.consoleOpen = show;
  layout();
  if (show) {
    if (!consoleStarted) { consoleStarted = true; api.console_restart(consoleCwd(), S.roots); }
    setTimeout(() => consoleInput.focus(), 0);
  } else if (S.active) {
    editor.focus();
  }
  persist();
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
  if (!api) return;
  api.save_state({
    roots: S.roots,
    tabs: S.tabs.map((t) => ({path: t.path, viewState: t === S.active ? editor.saveViewState() : t.viewState})),
    active: S.active ? S.active.path : null,
    mru: S.mru, expanded: [...S.expanded], closed: S.closed, history: S.history, view: S.view,
    sidebarWidth: S.sidebarWidth, sidebarOpen: S.sidebarOpen, consoleHeight: S.consoleHeight, consoleOpen: S.consoleOpen,
    autosave: S.autosave,
  });
}, 500);

function comboOf(e) {
  let key = e.key.toLowerCase();
  if (e.code === 'Backquote' || (e.ctrlKey && (e.key === 'ö' || e.key === 'Ö'))) key = '`';  // Nordic layouts: the § key or Ö, as in VS Code
  if (e.code === 'Tab') key = 'tab';
  return (e.ctrlKey ? 'ctrl+' : '') + (e.shiftKey ? 'shift+' : '') + (e.altKey ? 'alt+' : '') + key;
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
  };
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
    }
  });
  addEventListener('mousedown', (e) => { if (!$('menu').contains(e.target)) hideMenu(); });
  $('finder-input').addEventListener('input', renderFinder);
  $('finder').addEventListener('focusout', (e) => { if (!$('finder').contains(e.relatedTarget)) setTimeout(() => { if (!$('finder').contains(document.activeElement)) $('finder').classList.add('hidden'); }, 100); });
  $('add-root').onclick = addRoot;
  for (const b of document.querySelectorAll('#side-tabs button')) b.onclick = () => showView(b.dataset.view);
  const searchSoon = debounce(runSearch, 350);
  $('search-input').addEventListener('input', searchSoon);
  $('search-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') runSearch(); });
  for (const opt of ['case', 'regex']) {
    $('search-' + opt).onclick = (e) => { search[opt] = !search[opt]; e.target.classList.toggle('on', search[opt]); runSearch(); };
  }
  $('st-lint').onclick = () => { if (S.active) { editor.focus(); editor.trigger('status', 'editor.action.marker.nextInFiles'); } };
  dragSash($('side-sash'), (e) => { S.sidebarWidth = Math.max(140, Math.min(e.clientX, innerWidth - 300)); layout(); });
  dragSash($('console-sash'), (e) => { S.consoleHeight = Math.max(80, Math.min(innerHeight - e.clientY - 22, innerHeight - 150)); layout(); });
  editor.addAction({id: 'slate.reveal', label: 'Reveal in folder tree', keybindings: [monaco.KeyMod.Alt | monaco.KeyMod.Shift | monaco.KeyCode.KeyL],
    contextMenuGroupId: 'navigation', run: revealActiveInTree});
}
