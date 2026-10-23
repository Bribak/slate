"""Install slate as a macOS app for one user: `python -m slate --install` makes ~/Applications/slate.app, which Launchpad,
Spotlight, the Dock and Finder's "Open With" know. `--uninstall` removes it. The Windows counterpart is slate/install.py.

The app is an AppleScript applet, compiled with macOS's own osacompile, so installing needs neither Xcode nor its command
line tools. A double-click starts `python -m slate`, and files opened with it go along as arguments; when slate is already
running, that launch hands them to the open window (instance.py), as on Windows. Info.plist then gets slate's name,
icon and version, and declares that it opens Python scripts and folders. The default app for .py files stays the user's
choice: Get Info > Open with > slate > Change All.
"""
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from slate import __version__
from slate.system import console_python

HERE = Path(__file__).parent
APP = Path.home() / 'Applications' / 'slate.app'
BUNDLE_ID = 'io.github.bribak.slate'
LSREGISTER = '/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister'
ICON_SIZES = (16, 32, 128, 256, 512)


def applet(python):
    """The AppleScript: the launch command runs in the background, so the applet quits at once and slate keeps no Terminal."""
    launch = f'{shlex.quote(str(python))} -m slate'.replace('\\', '\\\\').replace('"', '\\"')
    return f'''property launcher : "{launch}"

on run
\tdo shell script launcher & " > /dev/null 2>&1 &"
end run

on open theItems
\tset args to ""
\trepeat with anItem in theItems
\t\tset args to args & " " & quoted form of POSIX path of anItem
\tend repeat
\tdo shell script launcher & args & " > /dev/null 2>&1 &"
end open
'''


def info(plist):
    """Info.plist as osacompile wrote it, with slate's identity and the documents it opens."""
    plist.update({
        'CFBundleName': 'slate', 'CFBundleDisplayName': 'slate', 'CFBundleIdentifier': BUNDLE_ID, 'CFBundleIconFile': 'slate',
        'CFBundleShortVersionString': __version__, 'CFBundleVersion': __version__, 'NSHighResolutionCapable': True,
        'CFBundleDocumentTypes': [
            {'CFBundleTypeName': 'Python script', 'CFBundleTypeRole': 'Editor', 'LSHandlerRank': 'Alternate',
             'LSItemContentTypes': ['public.python-script']},
            {'CFBundleTypeName': 'Folder', 'CFBundleTypeRole': 'Viewer', 'LSHandlerRank': 'None', 'LSItemContentTypes': ['public.folder']},
        ],
    })
    return plist


def icon(resources):
    """slate.icns from web/slate.png, through sips and iconutil (both part of macOS); the applet's own icon if that fails."""
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / 'slate.iconset'
        iconset.mkdir()
        for size in ICON_SIZES:
            for scale in (1, 2):
                px = size * scale
                if px <= 512:  # slate.png is 256 px; sips scales it up for the 2x of 256 and 512
                    subprocess.run(['sips', '-z', str(px), str(px), str(HERE / 'web' / 'slate.png'), '--out',
                                    str(iconset / f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png")], capture_output = True)
        out = subprocess.run(['iconutil', '-c', 'icns', str(iconset), '-o', str(resources / 'slate.icns')], capture_output = True)
    return out.returncode == 0


def installed():
    return APP.is_dir()


def install(open_settings = True):
    python = console_python(sys.executable)
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / 'slate.applescript'
        script.write_text(applet(python), encoding = 'utf-8')
        if APP.exists():
            shutil.rmtree(APP)
        APP.parent.mkdir(parents = True, exist_ok = True)
        out = subprocess.run(['osacompile', '-o', str(APP), str(script)], capture_output = True, text = True)
    if out.returncode:
        raise SystemExit(f'osacompile failed:\n{out.stdout}{out.stderr}')
    contents = APP / 'Contents'
    plist_file = contents / 'Info.plist'
    plist = info(plistlib.loads(plist_file.read_bytes()))
    if not icon(contents / 'Resources'):
        plist.pop('CFBundleIconFile')
    plist_file.write_bytes(plistlib.dumps(plist))
    subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(APP)], capture_output = True)  # Info.plist changed, so sign it again (ad hoc)
    subprocess.run([LSREGISTER, '-f', str(APP)], capture_output = True)  # Launchpad, Spotlight and "Open With" see it now
    print(f'slate installed as {APP}; it starts {python} -m slate.')
    if open_settings:
        print('To open .py files with slate by default: select one in Finder, File > Get Info, Open with: slate, then Change All.')
        subprocess.run(['open', '-R', str(APP)])


def uninstall():
    if APP.exists():
        subprocess.run([LSREGISTER, '-u', str(APP)], capture_output = True)
        shutil.rmtree(APP)
    print('slate is uninstalled. Files that opened with it open with their next app.')
