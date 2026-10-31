# What's new in slate

slate shows the sections below when an update is available and once after it is installed (slate/update.py). Each version
gets a `## X.Y.Z (date)` section, newest first, written for the people who use slate. Changes that are not released yet go
under `## Unreleased`; the version bump renames it.

## Unreleased

## 0.2.2 (2026-10-31)

- A GlycoDraw drawing shown in the console is a die-cut sticker on the dark background, instead of sitting on a white box.
- Markdown in the console draws the glycans written in its text as stickers, so `Markdown(f'{glycan} binds')` is enough; a drawing from `as_markdown()` becomes a sticker too, and glycans in `code` stay text.
- Plots in the console are cut out as stickers as well: rounded corners and a soft shadow.
- Point at a motif's name in a glycan's hover card, or in the table view's card, and the motif lights up in the drawing.
- Right-click any glycan drawing (in a hover, the table view, or next to a glycan in your code) to copy it as a sharp image for a slide.
- ▶ Analyze in the table view: glycowork's differential expression, its volcano plot and the top results in the console, in one click, as code you can change and run again.
- A pandas DataFrame in the console shows as a table, with its glycans drawn.
- glycowork's volcano plots and networks with SNFG drawings show sharp in the console, where they came out blank.
- slate starts its page without asking DNS for the computer's own name, which on some Macs kept the window blank; a page that does not load is now tried again up to three times.

## 0.2.1 (2026-10-31)

- Many open tabs no longer slow slate down: the tabs it restores are read the first time you look at them, so 29 tabs start as fast as one, with about 70 MB less memory and no 6 s of checking every file in the background.
- Ctrl+S saves every changed tab, as in PyCharm, not only the one in front.
- slate no longer opens to a blank window when it is reopened right after closing, while the old window is still shutting down (or anything else holds its port); and if the page ever fails to start, slate loads it again by itself.
- The full glycan drawing with linkages, in the hover and in the table view's row panel, is a die-cut sticker like the small one, instead of sitting on a white box.
- Ctrl+P's recent files no longer show their folder's name twice (glycowork\glycowork\glycowork\motif).
- The status bar names a tab's language as CSV, TSV, JSON or Markdown, rather than "plaintext".
- Escape closes a right-click menu, and the tab or file it belongs to is outlined while it is open.

## 0.2.0 (2026-10-27)

- slate now runs on Linux too, in a window made of GTK and WebKitGTK; README.md says what to install first. `python -m slate --install` adds it to the desktop's app menu and to a .py file's Open With.

## 0.1.6 (2026-10-26)

- slate no longer turns blank for good after a while with big files open (it happened most with change files being written while the Changes view was open): every answer from slate's Python side stayed in the window's memory until it ran out. Should the window's page ever crash again, it reloads by itself with your tabs.
- Moving the mouse into the editor no longer blanks it for a moment in a large window on a high-DPI screen: slate now gives the graphics card enough memory for the whole editor, and makes up for it elsewhere, so it uses slightly less memory than before.
- Settings (Ctrl+Alt+S) now lists all of slate's keys, with a Mac's keys on a Mac; the empty editor points there.
- Shift+Alt+L (show the current file in the tree) now also works when the tree or the console has the focus, not only the editor.
- The fold arrows in the gutter and the divider between sidebar and editor show only when the mouse rests there (or drags the divider), not when it passes over them.

## 0.1.5 (2026-10-24)

- slate's window shows slate's icon on the Windows taskbar instead of Python's, and a slate pinned to the taskbar is the slate that is running.
- .py files that open in slate show slate's own file icon in Explorer.
- Local history, as in PyCharm: every save keeps the version it replaced, including edits by git or Claude that you then saved over. Right-click a tab and choose Local history… to compare any of them with the text now and restore it.
- Compare with saved, in a tab's menu: its unsaved changes against the file on disk, with Revert to saved.
- Ctrl+Shift+Backspace goes to where you last changed something, and further back on each press, as in PyCharm.
- `TODO` and `FIXME` comments stand out in PyCharm's yellow-green.
- Ctrl+P lists recently closed files after the open tabs before you type.
- Menus group their items, diffs use calmer colours, and buttons show where the keyboard focus is.
- Style checks of large files are up to a third faster (CandyCrunch's prediction.py: 0.66 s → 0.43 s), and squiggles start 0.3 s after you stop typing instead of 0.4 s, as in PyCharm.
- Find in files is 20-50% faster for plain text: a search of glycowork and CandyCrunch that finds nothing takes 0.16 s instead of 0.29 s.
- slate starts about 50 ms sooner, and hands a double-clicked file to an open slate about 50 ms sooner.
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
