# slate

A small Python editor for Windows, written as a light replacement for PyCharm. It is Monaco (the editor component of VS Code) in a WebView2 window, driven by Python. With eight files open and the console running it uses about 430 MB of RAM (Task Manager), and about 350 MB while another app is in front, where PyCharm used 4.5 GB on the same machine.

## Requirements

* Windows 10 or 11. The WebView2 runtime and the .NET Framework 4 compiler that slate uses come with Windows.
* Python 3.10 or newer, from python.org (tested with 3.12). slate runs on this Python; the console can use a different one (see Settings).

## Install

1. Install slate and its dependencies (pywebview, jedi, pycodestyle) straight from GitHub:
   ```
   pip install git+https://github.com/Bribak/slate
   ```
   That needs git. Without git, install the same code as a zip:
   ```
   pip install https://github.com/Bribak/slate/archive/refs/heads/main.zip
   ```
2. Install slate as a Windows app, for your user only (no admin rights needed):
   ```
   python -m slate --install
   ```
   This adds slate to the Start menu and to Settings → Apps → Installed apps, and registers it for .py files.
3. Make slate the default for .py files. Windows does not let programs set this themselves, so it is one click: on the Settings page that step 2 opened (Default apps → slate), click **Set default**. If you closed it, double-click any .py file instead and answer Windows' "How do you want to open this file?" with **slate** and **Always**.

   If slate is not in that list: click **Choose an app on your PC** at the bottom. Paste this into the **File name** box and press Enter, then click **Always**:
   ```
   %LOCALAPPDATA%\Programs\slate\slate.exe
   ```
   That is `C:\Users\<your user name>\AppData\Local\Programs\slate\slate.exe`. Paste the path rather than browsing, because File Explorer hides the AppData folder.

Start slate from the Start menu, by double-clicking a .py file, or with `slate` in a terminal. Double-clicking more files while slate is open adds them as tabs to the open window instead of starting another one.

Run `python -m slate --install` again after upgrading or moving Python. `python -m slate --uninstall`, or uninstalling slate under Installed apps, removes everything step 2 added.

**Updates.** Once a day slate asks GitHub for its newest version number. When there is a newer one, the status bar says "slate 0.2.0 is available"; click it to install it with pip (about 40 s), then restart slate. Turn the check off in Settings. To work on slate itself, clone it and install with `pip install -e .` instead; such a checkout gets no update notices.

## What it does

* **Tabs from any number of projects.** Tabs wrap onto several rows. Each tab is underlined in its folder's color and labelled with its project. Open tabs, cursor positions and folders come back on restart.
* **PyCharm-style colors and font.** Darcula colors and a Python grammar close to PyCharm's, with `self`, builtins, `def` names, decorators, keyword arguments, docstrings and f-string fields. The font is the JetBrains Mono that ships with PyCharm (or any JetBrains IDE) when one is installed, at PyCharm's default size; otherwise an installed JetBrains Mono, Cascadia Mono or Consolas.
* **Errors before you run.** Every .py file is compiled as you type, so syntax and indentation errors, `return` outside a function and similar mistakes are underlined in red, the tab name turns red, and the status bar names the error and line. SyntaxWarnings such as an invalid `"\d"` escape are underlined too.
* **Sharp on every monitor.** Text renders at each monitor's own scaling, and the window keeps its size when moved between a laptop screen and an external monitor. It reopens where it was closed, maximized or not.
* **Navigation.** Add project folders with **+** in the folder tree; several sit side by side. Ctrl+P finds a file by name across all of them (`name:120` jumps to line 120). Ctrl+Shift+F searches their contents. Ctrl+click or F12 goes to a definition: the open folders' own names (variables, functions, classes, methods, keyword arguments, imports) straight from the syntax tree, library code and attributes of objects through jedi.
* **Function hints.** Typing a call to one of your projects' own functions shows its parameters with their `# comments`, highlighting the one you are typing. Hovering over its name shows the docstring. This covers functions defined or imported in the file, and in the console those imported earlier. Library functions such as pandas' get no hints.
* **Console.** Ctrl+\` (Ctrl+Ö or the § key on Nordic keyboards) opens a Python console whose variables persist between runs. Enter runs the input, or adds a line while a block or bracket is still open. Shift+Enter always adds a line, and Ctrl+Enter always runs. Up and Down browse the history. **Interrupt** stops a run. **Restart** forgets all variables.
* **Running code from the editor.** Ctrl+Enter runs the selection in the console. Without a selection, it runs the cell at the cursor in a file with `# %%` cells, else the statement at the cursor. Shift+Enter does the same and moves on to the next cell or statement, as in Jupyter. F5 (or Shift+F10) runs the whole file as a script. The script starts with a fresh namespace, as `python file.py` would, and its variables stay in the console afterwards, so you can look at them. Each `# %%` line starts a cell, as in VS Code, Spyder and PyCharm. It gets a rule above it and a ▶ in the gutter that runs it. The console's working directory becomes the file's folder, so relative paths and sibling imports work. Tracebacks give the file's own line numbers, and clicking a `File "...", line N` line opens it there.
* **Figures in the console.** matplotlib and seaborn plots appear below the code that made them, as in Jupyter: every figure still open at the end of a run, and each `plt.show()`. So do values that can draw themselves for Jupyter, such as `GlycoDraw(..., suppress = True)`. Right-click a figure to copy it. The newest 40 stay; older ones make room. Set `MPLBACKEND` yourself to use another matplotlib backend.
* **Glycan drawings.** A string that reads as an IUPAC-condensed glycan, such as `'Fuc(a1-2)Gal(b1-3)GalNAc'`, gets a small SNFG drawing right after it, and hovering over it shows the full drawing with linkages. glycowork's GlycoDraw draws them in a process of its own, with the console's Python and with a glycowork checkout among your folders if there is one. That process takes about 2 s to start, uses about 70 MB while it runs, and stops after 10 minutes without anything new to draw. Only the lines on screen are drawn, so files with thousands of glycans stay quick. Without glycowork, slate says so once and draws nothing.
* **Lab coding standard.** In a project with a `.github/scripts/check_style.py` (glycowork, CandyCrunch), violations of the lab's coding standard are underlined as you type, using that project's own checker, so the editor agrees with CI. Shift+Alt+F reformats the file to the standard (GW1, GW2 and the pycodestyle rules; GW3 stays underlined). Reformatting needs a version of `check_style.py` with `--fix`; with an older one, slate says so instead. Files outside such a project are checked with glycowork's checker if a glycowork checkout sits in `Documents\glycowork` (see `style_script` below), and are not checked otherwise.
* **Files changed outside slate.** Edits by git, Claude or any other tool are picked up within two seconds without moving your cursor. If you have unsaved edits in that file, a banner asks which version to keep.
* **Autosave.** As in PyCharm, changed files are saved when the slate window loses focus.

## Keys

| Key | Action |
| --- | --- |
| Ctrl+P / Ctrl+E | open file by name |
| Ctrl+Shift+F | find in files |
| Ctrl+Shift+E | folder tree |
| Alt+Shift+L | show the current file in the tree |
| Ctrl+B | show/hide the sidebar |
| Ctrl+\` | show/hide the console |
| Ctrl+S | save |
| Ctrl+W | close tab |
| Ctrl+Shift+T | reopen closed tab |
| Ctrl+Tab | previous tab (hold Ctrl and press Tab again to go further back) |
| Ctrl+PageUp/PageDown | tab to the left/right |
| Ctrl+Enter / Alt+Shift+E | run the selection, cell or statement in the console |
| Shift+Enter | the same, then go on to the next cell or statement |
| F5 / Shift+F10 | run the file in the console |
| Ctrl+Alt+S | settings |
| Shift+Alt+F | reformat to the coding standard |
| F12 / Ctrl+click | go to definition |
| F8 | next style finding |

Everything else follows VS Code's keys.

## Settings

Ctrl+Alt+S, or the ⚙ at the right end of the status bar, opens the settings. Changes apply at once, except where they say otherwise.

* **Console Python.** You can set a default and one per open folder. The list offers each folder's virtual environment (`.venv`, `venv`), your conda environments and the Pythons installed from python.org; **Browse…** picks any other python.exe. Code run from a file (Ctrl+Enter, F5) uses its project's Python. If the console is running another Python, it restarts first, which clears its variables. The console also uses the project's Python when it starts or restarts while one of that project's files is in front. Any Python 3.8 or newer works, and the console imports packages from it. Without a setting, the console uses the Python slate runs on.

The settings live in `%APPDATA%\slate\state.json` with your session. You can also edit it by hand with slate closed:

* `"console_python"`: the default python.exe for the console, e.g. `"C:\\Users\\you\\miniconda3\\envs\\glyco\\python.exe"`. `"console_pythons"` maps a folder (lower case, as `"c:\\users\\you\\documents\\glycowork"`) to its own.
* `"style_script"`: the `check_style.py` used for files outside any project with its own (default: `Documents\glycowork\.github\scripts\check_style.py`).
* `"autosave": false`: turns off saving when the window loses focus.
* `"usage_log": false`: turns off the local usage log (see Development).
* `"glycan_drawings": false`: no SNFG drawings next to glycans in the code.
* `"gpu": false`: software rendering, saving about another 100 MB. On high-resolution screens it is noticeably slower: at 250% scaling a keystroke took 52 ms to appear instead of 22 ms.
* `"update_check": false`: no daily check for a new version.

## Development

`python -m slate --debug` opens slate with WebView2's developer tools. `python tools/bench.py LABEL [--flags "<chromium flags>"] [--state '<state keys as JSON>'] [--line N]` measures startup, scroll and typing latency and RAM per process in a real window, with an isolated session; run it before and after any change that could affect speed or memory. `python tools/startup.py LABEL FILE` times a start that opens FILE, as a double-click does, in a copy of your session. `python -m slate.usage [days]` summarises `%APPDATA%\slate\usage.jsonl`, a local log of which calls ran and how long they took, slate's keys, errors with their stack traces, and window and monitor changes. It holds no file contents and is never sent anywhere. `python -m slate.devserver` serves the page at http://localhost:8765 to an ordinary browser, with the same backend over HTTP and a separate session.

**Personal extensions.** Every `.js` file in `%APPDATA%\slate\extensions` runs once when slate starts, with a `host` object that can add a sidebar view, open files, show a diff and react to focus and saves (see `host` in `slate/web/app.js`). The folder belongs to you, not to slate, so extensions stay on your machine. They run with slate's full rights: only put your own files there.
