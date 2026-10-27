"""What differs between Windows, macOS and Linux outside the window itself, which slate/win.py, slate/mac.py and
slate/linux.py handle (as `native`)."""
import os
import subprocess
import sys
from pathlib import Path

MAC = sys.platform == 'darwin'
LINUX = sys.platform.startswith('linux')
WINDOWS = not (MAC or LINUX)
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)  # child processes get no console window on Windows

if MAC:
    from slate import mac as native
elif LINUX:
    from slate import linux as native
else:
    from slate import win as native


def data_dir():
    """Where slate keeps its state, logs and caches: %APPDATA%\\slate, ~/Library/Application Support/slate, or
    $XDG_CONFIG_HOME/slate (~/.config/slate)."""
    if MAC:
        return Path.home() / 'Library' / 'Application Support' / 'slate'
    if LINUX:
        return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'slate'
    return Path(os.environ.get('APPDATA', Path.home())) / 'slate'


def console_python(python):
    """The interpreter to run with real stdio pipes: python.exe rather than pythonw.exe, which slate.exe starts slate with."""
    python = Path(python)
    return python.with_name('python.exe') if python.name.lower() == 'pythonw.exe' else python


def reveal(path):
    """Show a file in Explorer, Finder or the file manager; a folder's path ending in '.' opens the folder itself."""
    if MAC:
        folder = path.endswith(os.sep + '.')
        subprocess.Popen(['open', path[:-2]] if folder else ['open', '-R', path])
    elif LINUX:
        native.reveal(path)
    else:
        subprocess.Popen(['explorer', '/select,', os.path.normpath(path)])


def installer():
    """The module behind --install and --uninstall: a Windows app (slate.exe, registry), a macOS one (slate.app) or a Linux
    desktop entry."""
    if MAC:
        from slate import macapp
        return macapp
    if LINUX:
        from slate import linuxapp
        return linuxapp
    from slate import install
    return install
