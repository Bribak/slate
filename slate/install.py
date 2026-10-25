"""Install slate as a per-user Windows app, like VS Code's user installer: nothing machine-wide, no admin rights.

`python -m slate --install` puts a real slate.exe (icon, version info) in %LOCALAPPDATA%\\Programs\\slate, adds a Start
menu entry, registers slate for .py files and in Settings > Default apps, and lists it under Installed apps.
Windows keeps the actual default app in a hash-protected key that no program can set, so the last step is the
"Set default" button on slate's page in Settings, which --install opens. `python -m slate --uninstall` undoes it all.
"""
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
import winreg
from pathlib import Path

from slate import __version__
from slate.win import APP_ID

HERE = Path(__file__).parent
ICON = HERE / 'web' / 'slate.ico'
FILE_ICON = HERE / 'web' / 'slate-file.ico'  # .py files in Explorer: a page in slate's colours (tools/icons.py)
known = lambda name: Path(os.environ[name].removeprefix('\\\\?\\'))  # some shells hand these out with a \\?\ prefix, which csc rejects
HOME = known('LOCALAPPDATA') / 'Programs' / 'slate'
EXE = HOME / 'slate.exe'
SHORTCUT = known('APPDATA') / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / 'slate.lnk'
PINNED = known('APPDATA') / 'Microsoft' / 'Internet Explorer' / 'Quick Launch' / 'User Pinned' / 'TaskBar' / 'slate.lnk'
CSC = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET' / 'Framework64' / 'v4.0.30319' / 'csc.exe'
PROGID = 'slate.py'
KEYS = [r'Software\Classes\slate.py', r'Software\Classes\Applications\slate.exe', r'Software\slate',
        r'Software\Microsoft\Windows\CurrentVersion\App Paths\slate.exe', r'Software\Microsoft\Windows\CurrentVersion\Uninstall\slate']


def pythonw():
    path = Path(sys.executable).with_name('pythonw.exe')
    return path if path.is_file() else Path(sys.executable)


def build_exe():
    source = (HERE / 'launcher.cs').read_text(encoding = 'utf-8').replace('{PYTHONW}', str(pythonw())).replace('{VERSION}', __version__ + '.0')
    HOME.mkdir(parents = True, exist_ok = True)
    with tempfile.TemporaryDirectory() as tmp:
        cs = Path(tmp) / 'launcher.cs'
        cs.write_text(source, encoding = 'utf-8')
        out = subprocess.run([str(CSC), '/nologo', '/target:winexe', '/optimize', f'/win32icon:{ICON}', '/reference:System.Windows.Forms.dll',
                              f'/out:{EXE}', str(cs)], capture_output = True, text = True)
    if out.returncode:
        raise SystemExit(f'compiling the launcher failed:\n{out.stdout}{out.stderr}')
    shutil.copy2(ICON, HOME / 'slate.ico')
    shutil.copy2(FILE_ICON, HOME / 'slate-file.ico')


def set_value(key, name, value):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ, value)


def register():
    command = f'"{EXE}" "%1"'
    app_icon = f'"{EXE}",0'
    file_icon = str(HOME / 'slate-file.ico')
    set_value(r'Software\Classes\slate.py', '', 'Python File')
    set_value(r'Software\Classes\slate.py\DefaultIcon', '', file_icon)
    set_value(r'Software\Classes\slate.py\shell\open\command', '', command)
    set_value(r'Software\Classes\.py\OpenWithProgids', PROGID, '')
    set_value(r'Software\Classes\Applications\slate.exe', 'FriendlyAppName', 'slate')
    set_value(r'Software\Classes\Applications\slate.exe\DefaultIcon', '', file_icon)  # the files' icon when "Open with" made slate their app
    set_value(r'Software\Classes\Applications\slate.exe\shell\open\command', '', command)
    set_value(r'Software\Classes\Applications\slate.exe\SupportedTypes', '.py', '')
    set_value(r'Software\slate\Capabilities', 'ApplicationName', 'slate')
    set_value(r'Software\slate\Capabilities', 'ApplicationDescription', 'A small Python editor')
    set_value(r'Software\slate\Capabilities', 'ApplicationIcon', app_icon)
    set_value(r'Software\slate\Capabilities\FileAssociations', '.py', PROGID)
    set_value(r'Software\RegisteredApplications', 'slate', r'Software\slate\Capabilities')
    set_value(r'Software\Microsoft\Windows\CurrentVersion\App Paths\slate.exe', '', str(EXE))  # Win+R "slate"
    uninstall = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\slate'
    for name, value in [('DisplayName', 'slate'), ('DisplayVersion', __version__), ('DisplayIcon', app_icon), ('InstallLocation', str(HOME)),
                        ('UninstallString', f'"{sys.executable}" -m slate --uninstall'), ('NoModify', 1), ('NoRepair', 1)]:
        set_value(uninstall, name, value)


def shortcut():
    quote = lambda path: str(path).replace("'", "''")  # a user name like O'Brien would end the PowerShell string
    script = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{quote(SHORTCUT)}'); $s.TargetPath = '{quote(EXE)}'; "
              f"$s.IconLocation = '{quote(EXE)},0'; $s.Description = 'A small Python editor'; $s.Save()")
    subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script], check = True, capture_output = True)


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [('fmtid', ctypes.c_byte * 16), ('pid', ctypes.c_ulong)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [('vt', ctypes.c_ushort), ('reserved', ctypes.c_ushort * 3), ('text', ctypes.c_wchar_p), ('rest', ctypes.c_void_p)]


def set_link_app_id(path):
    """Give a shortcut slate's AppUserModelID (System.AppUserModel.ID), the one slate's process declares (win.set_app_id):
    Windows then shows the running window under this shortcut's name and icon, and a pin of it as the running slate."""
    guid = lambda text: (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(text).bytes_le)
    key = PROPERTYKEY(guid('9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3'), 5)
    store = ctypes.c_void_p()
    ctypes.windll.ole32.CoInitialize(None)
    ctypes.oledll.shell32.SHGetPropertyStoreFromParsingName(str(path), None, 2, ctypes.byref(guid('886d8eeb-8cf2-4446-8d02-cdba1dbdcf99')),
                                                            ctypes.byref(store))  # GPS_READWRITE, IID_IPropertyStore
    table = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    method = lambda index, *args: ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *args)(table[index])
    try:
        method(6, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))(store, key, PROPVARIANT(31, (0, 0, 0), APP_ID))  # SetValue, VT_LPWSTR
        method(7)(store)  # Commit
    finally:
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[2])(store)  # Release


def changed():
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x1000, None, None)  # SHCNE_ASSOCCHANGED, so Explorer rereads the associations; SHCNF_FLUSH: once it has
    refresh = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'System32' / 'ie4uinit.exe'
    if refresh.is_file():
        subprocess.run([str(refresh), '-show'], capture_output = True)  # and redraws icons it has cached for .py files


def installed():
    return EXE.is_file()


def install(open_settings = True):
    if not CSC.is_file():
        raise SystemExit(f'{CSC} not found: slate.exe is compiled with the .NET Framework 4 compiler that ships with Windows')
    build_exe()
    register()
    shortcut()
    for link in (SHORTCUT, PINNED):  # PINNED: a slate pinned to the taskbar before slate had an AppUserModelID
        if link.is_file():
            set_link_app_id(link)
    changed()
    print(f'slate installed as {EXE}, with a Start menu entry and a .py registration.')
    print('If Windows does not offer slate for .py files, use "Choose an app on your PC" and paste that path into the File name box.')
    if open_settings:
        print('To make it the default for .py files: on the Settings page that opens now, click "Set default" (or pick slate next to .py).')
        os.startfile('ms-settings:defaultapps?registeredAppUser=slate')


def delete_tree(key):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            while True:
                try:
                    child = winreg.EnumKey(k, 0)
                except OSError:
                    break
                delete_tree(rf'{key}\{child}')
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
    except FileNotFoundError:
        pass


def delete_value(key, name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
    except FileNotFoundError:
        pass


def uninstall():
    for key in KEYS:
        delete_tree(key)
    delete_value(r'Software\Classes\.py\OpenWithProgids', PROGID)
    delete_value(r'Software\RegisteredApplications', 'slate')
    SHORTCUT.unlink(missing_ok = True)
    for name in ('slate.exe', 'slate.ico', 'slate-file.ico'):
        (HOME / name).unlink(missing_ok = True)
    if HOME.is_dir() and not any(HOME.iterdir()):
        HOME.rmdir()
    changed()
    print('slate is uninstalled. If .py files were set to open in slate, Windows asks which app to use the next time.')
