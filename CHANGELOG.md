# What's new in slate

slate shows the sections below when an update is available and once after it is installed (slate/update.py). Each version
gets a `## X.Y.Z (date)` section, newest first, written for the people who use slate. Changes that are not released yet go
under `## Unreleased`; the version bump renames it.

## Unreleased

- The session comes back faster at startup: the saved tabs are read while the window is still setting up (about 0.4 s sooner for eight tabs).
- Diff views (such as the Changes extension's) no longer have Python parse both files three times each in the background.
- The table view keeps the glycan column to about a third of the width, so the sample columns stay in view next to an open row.
- A minimised slate no longer checks its open files for outside changes every 2 seconds; it checks once when it is shown again.

## 0.1.4 (2026-10-24)

- Suggestions while you type a glycan in a string or a table cell: after `Gal(` the linkages Gal takes, after `Gal(b1-4)` the monosaccharides that follow it, and the monosaccharides that start a glycan or a branch, each with how often glycowork's glycans have it there, most common first.
- GlyTouCan IDs such as `G00031MO` get the drawing of their glycan, in scripts and tables, and a hover with the glycan and its card (glycowork 1.11.0 knows 189,228 IDs).
- ⊞ Table in the status bar shows a .csv, .tsv or .txt file as a table: aligned columns under a fixed header, glycans drawn, sorting by any column, a filter box, and a click on a row shows its glycan with the row's values as bars per sample group. Hovering a glycan in an abundance table shows its row's values as small bars too.
- Hovering a PMID or DOI shows the paper's title, authors, journal and year, with links to PubMed and doi.org; file names with a PMID (glycowork's datasets) show it in their tooltip. Only the identifier is sent, when you hover; it can be turned off in Settings.
- Ctrl+F (and Ctrl+H, replace) finds in the file in front wherever you are, e.g. in the folder tree or the console, where it used to do nothing or open a find box too small to see that then took your typing. It also no longer stops working for the rest of the session after an extension's diff view was closed.

## 0.1.3 (2026-10-24)

- Tabs wrap onto two rows at most; with more, both rows scroll sideways (the mouse wheel scrolls them too), as in PyCharm.
- The drawings next to glycans are GlycoDraw's die-cut stickers instead of drawings on a grey box (glycowork 1.10.1 or newer; older versions keep the box).
- Glycans on screen are drawn in about half the time: the larger drawing with linkages is made when you first hover over a glycan.
- The console shows Markdown as Jupyter does, glycans in the text included: `Markdown(f'binds {GlycoDraw(g).as_markdown()}')` from `IPython.display` (glycowork 1.11.1). `display()` shows several things from one cell, as in Jupyter.

## 0.1.2 (2026-10-24)

- Updates install a tested release: a new version is released only after slate passed its tests on Windows and macOS, so an update never brings work in progress.
- What changed: clicking "slate X is available" shows these notes before installing, and the first start after an update offers them once in the status bar.
- Glycans in tables: glycan cells in .csv, .tsv and .txt files get their SNFG drawing, coloured residues and hover card, as glycan strings in scripts do. Quoted cells may hold commas, and tables are never checked.
- glycowork, CandyCrunch, glycorender or glycontact installed with pip while slate is open now get hints and Ctrl+click within seconds, without restarting slate.

## 0.1.1 (2026-10-23)

- Back and forward: Ctrl+Alt+Left/Right (or Alt+Left/Right, or the mouse's side buttons) return to where you jumped from, as in PyCharm.
- File structure: Ctrl+F12 lists the file's classes and functions to jump to.
- Right-click a tab to close the others, the ones to its right or all unchanged ones, to copy its path, or to show it in the tree or Explorer.
- Right-click a file or folder in the tree to rename it (its open tabs follow) or to delete it to the Recycle Bin.
- Hints and Ctrl+click also reach pip-installed glycorender and glycontact, besides glycowork and CandyCrunch.
- Glycan drawings: prose in docstrings and f-strings are no longer drawn as glycans.
- The glycan hover card stays in view below the drawing.
- Faster: function hints after a pause (about 20 ms instead of up to half a second), less CPU while idle, and less memory after checking big files.
- The README has screenshots.

## 0.1.0 (2026-10-23)

The first version shared with the group.

- Glycans in the code: IUPAC-condensed strings get their SNFG drawing next to them, a hover card with mass, composition and motifs, warnings for glycans glycowork cannot read, and a motif search across the open folders.
- Signature hints and docstrings for the open projects and for glycowork and CandyCrunch; Ctrl+click goes to definitions in a few milliseconds.
- glycowork's style checks underlined as you type, with every file compiled for syntax errors.
- A Python console with figures, `# %%` cells, Ctrl+Enter and Shift+Enter to run code, and F5 to run the file.
- PyCharm's Darcula colours and JetBrains Mono, tabs from any number of projects, Ctrl+P and Ctrl+Shift+F across them.
- A settings panel (Ctrl+Alt+S) with a Python per project, and a daily check for updates.
- Installs as an app on Windows and macOS, opens .py files by double-click, and keeps to one window.
