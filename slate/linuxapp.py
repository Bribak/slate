"""Install slate as a Linux desktop app for one user: `python -m slate --install` writes a desktop entry and its icon under
~/.local/share (XDG_DATA_HOME), which GNOME, KDE and the other desktops list in their app menus and in a .py file's
"Open With". `--uninstall` removes both. The Windows and macOS counterparts are slate/install.py and slate/macapp.py.

The entry starts `python -m slate` with the Python slate is installed in, so a venv's slate starts from its venv; files
opened with it go along as arguments, and a running slate takes them (instance.py). Its name is the window's app ID
(slate/linux.py), which is how the desktop ties the window to the entry's name and icon. The entry does not claim folders:
listed for inode/directory, some desktops made VS Code the program that opens every folder. The default app for .py files
stays the user's choice (a file's Properties > Open With, or xdg-mime).
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from slate import __version__
from slate.linux import APP_ID
from slate.system import console_python

HERE = Path(__file__).parent
DATA = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
ENTRY = DATA / 'applications' / f'{APP_ID}.desktop'
ICON = DATA / 'icons' / 'hicolor' / '256x256' / 'apps' / f'{APP_ID}.png'
TYPES = ('text/x-python', 'text/x-python3')


def quote(arg):
    """An Exec argument as the Desktop Entry Specification quotes it; % is doubled, since it starts a field code there."""
    arg = arg.replace('%', '%%')
    if not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return arg
    return '"' + ''.join('\\' + c if c in '"`$\\' else c for c in arg) + '"'


def entry(python):
    return '\n'.join([
        '[Desktop Entry]', 'Type=Application', 'Name=slate', 'GenericName=Python Editor',
        'Comment=A small Python editor, made for glycobiology', f'Exec={quote(str(python))} -m slate %F', f'Icon={APP_ID}',
        'Terminal=false', 'Categories=Development;IDE;', f"MimeType={';'.join(TYPES)};", 'Keywords=python;glycan;glycowork;editor;',
        'StartupNotify=true', f'StartupWMClass={APP_ID}', f'X-slate-Version={__version__}', ''])


def installed():
    return ENTRY.is_file()


def refresh():
    """Update the folder's cache of which entries open which types (Open With), where the tool for it is installed. No icon
    cache: one written in ~/.local/share/icons would also have to be kept current for other apps' icons there."""
    if shutil.which('update-desktop-database'):
        subprocess.run(['update-desktop-database', '-q', str(ENTRY.parent)], capture_output = True)


def install(open_settings = True):
    python = console_python(sys.executable)
    ICON.parent.mkdir(parents = True, exist_ok = True)
    shutil.copyfile(HERE / 'web' / 'slate.png', ICON)  # 256 px, which the desktop scales for its menus and docks
    ENTRY.parent.mkdir(parents = True, exist_ok = True)
    ENTRY.write_text(entry(python), encoding = 'utf-8')
    refresh()
    print(f'slate installed as {ENTRY}; it starts {python} -m slate.')
    if open_settings:
        print(f'To open .py files with slate by default: xdg-mime default {ENTRY.name} {" ".join(TYPES)}')


def uninstall():
    for path in (ENTRY, ICON):
        if path.exists():
            path.unlink()
    refresh()
    print('slate is uninstalled. Files that opened with it open with their next app.')
