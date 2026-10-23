"""What differs between Windows and macOS outside the window itself, which slate/win.py and slate/mac.py handle (as `native`)."""
import os
import subprocess
import sys
from pathlib import Path

MAC = sys.platform == 'darwin'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)  # child processes get no console window on Windows

if MAC:
    from slate import mac as native
else:
    from slate import win as native


def data_dir():
    """Where slate keeps its state, logs and caches: %APPDATA%\\slate, or ~/Library/Application Support/slate."""
    if MAC:
        return Path.home() / 'Library' / 'Application Support' / 'slate'
    return Path(os.environ.get('APPDATA', Path.home())) / 'slate'


def console_python(python):
    """The interpreter to run with real stdio pipes: python.exe rather than pythonw.exe, which slate.exe starts slate with."""
    python = Path(python)
    return python.with_name('python.exe') if python.name.lower() == 'pythonw.exe' else python


def reveal(path):
    """Show a file in Explorer or Finder; a folder's path ending in '.' opens the folder itself."""
    if MAC:
        folder = path.endswith(os.sep + '.')
        subprocess.Popen(['open', path[:-2]] if folder else ['open', '-R', path])
    else:
        subprocess.Popen(['explorer', '/select,', os.path.normpath(path)])


def installer():
    """The module behind --install and --uninstall: a Windows app (slate.exe, registry) or a macOS one (slate.app)."""
    if MAC:
        from slate import macapp
        return macapp
    from slate import install
    return install
