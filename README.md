# slate

A small Python editor for Windows, written as a light replacement for PyCharm. It is Monaco (the editor component of VS Code) in a WebView2 window, driven by Python. With eight files open and the console running it uses about 430 MB of RAM (Task Manager), and about 350 MB while another app is in front, where PyCharm used 4.5 GB on the same machine.

## Requirements

* Windows 10 or 11. The WebView2 runtime and the .NET Framework 4 compiler that slate uses come with Windows.
* Python 3.10 or newer, from python.org (tested with 3.12). slate runs on this Python; the console can use a different one (see Settings).

## Install

1. Get the `slate` folder (clone or copy it) and open a terminal in it.
2. Install the package and its dependencies (pywebview, jedi, pycodestyle):
   ```
   pip install -e .
   ```
3. Install slate as a Windows app, for your user only (no admin rights needed):
   ```
   python -m slate --install
   ```
   This adds slate to the Start menu and to Settings → Apps → Installed apps, and registers it for .py files.
4. Make slate the default for .py files. Windows does not let programs set this themselves, so it is one click: on the Settings page that step 3 opened (Default apps → slate), click **Set default**. If you closed it, double-click any .py file instead and answer Windows' "How do you want to open this file?" with **slate** and **Always**.

   If slate is not in that list: click **Choose an app on your PC** at the bottom. Paste this into the **File name** box and press Enter, then click **Always**:
   ```
   %LOCALAPPDATA%\Programs\slate\slate.exe
   ```
   That is `C:\Users\<your user name>\AppData\Local\Programs\slate\slate.exe`. Paste the path rather than browsing, because File Explorer hides the AppData folder.

Start slate from the Start menu, by double-clicking a .py file, or with `slate` in a terminal. Double-clicking more files while slate is open adds them as tabs to the open window instead of starting another one.

Run `python -m slate --install` again after upgrading or moving Python. `python -m slate --uninstall`, or uninstalling slate under Installed apps, removes everything step 3 added.

## What it does

* **Tabs from any number of projects.** Tabs wrap onto several rows. Each tab is underlined in its folder's color and labelled with its project. Open tabs, cursor positions and folders come back on restart.
* **PyCharm-style colors and font.** Darcula colors and a Python grammar close to PyCharm's, with `self`, builtins, `def` names, decorators, keyword arguments, docstrings and f-string fields. The font is the JetBrains Mono that ships with PyCharm (or any JetBrains IDE) when one is installed, at PyCharm's default size; otherwise an installed JetBrains Mono, Cascadia Mono or Consolas.
* **Errors before you run.** Every .py file is compiled as you type, so syntax and indentation errors, `return` outside a function and similar mistakes are underlined in red, the tab name turns red, and the status bar names the error and line. SyntaxWarnings such as an invalid `"\d"` escape are underlined too.
* **Sharp on every monitor.** Text renders at each monitor's own scaling, and the window keeps its size when moved between a laptop screen and an external monitor. It reopens where it was closed, maximized or not.
* **Navigation.** Add project folders with **+** in the folder tree; several sit side by side. Ctrl+P finds a file by name across all of them (`name:120` jumps to line 120). Ctrl+Shift+F searches their contents. Ctrl+click or F12 goes to a definition (jedi, which looks names up on demand instead of indexing).
* **Console.** Ctrl+\` (Ctrl+Ö or the § key on Nordic keyboards) opens a Python console whose variables persist between runs. Enter runs the input, or adds a line while a block or bracket is still open. Shift+Enter always adds a line, and Ctrl+Enter always runs. Up and Down browse the history. **Interrupt** stops a run. **Restart** forgets all variables.
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
| Shift+Alt+F | reformat to the coding standard |
| F12 / Ctrl+click | go to definition |
| F8 | next style finding |

Everything else follows VS Code's keys.

## Settings

Close slate, then edit `%APPDATA%\slate\state.json` (it holds your session too, so change only these keys):

* `"console_python"`: the python.exe the console runs, e.g. `"C:\\Users\\you\\miniconda3\\envs\\glyco\\python.exe"` or a project's `.venv\\Scripts\\python.exe`. Without it, the console uses the Python slate runs on.
* `"style_script"`: the `check_style.py` used for files outside any project with its own (default: `Documents\glycowork\.github\scripts\check_style.py`).
* `"autosave": false`: turns off saving when the window loses focus.
* `"usage_log": false`: turns off the local usage log (see Development).
* `"gpu": false`: software rendering, saving about another 100 MB. On high-resolution screens it is noticeably slower: at 250% scaling a keystroke took 52 ms to appear instead of 22 ms.

## Development

`python -m slate --debug` opens slate with WebView2's developer tools. `python tools/bench.py LABEL [--flags "<chromium flags>"]` measures startup, scroll and typing latency and RAM per process in a real window, with an isolated session; run it before and after any change that could affect speed or memory. `python -m slate.usage [days]` summarises `%APPDATA%\slate\usage.jsonl`, a local log of which calls ran and how long they took, slate's keys, errors with their stack traces, and window and monitor changes. It holds no file contents and is never sent anywhere. `python -m slate.devserver` serves the page at http://localhost:8765 to an ordinary browser, with the same backend over HTTP and a separate session.
