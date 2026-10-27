"""The Linux side of slate's window (app.py; slate/win.py and slate/mac.py are the others): pywebview's GTK backend, which
draws the page with WebKitGTK, the same engine as macOS's WKWebView. Imported on Linux only, where pywebview needs PyGObject
and WebKitGTK (README: Requirements).

* `prepare` names the program io.github.bribak.slate before GTK starts, so the window's Wayland app ID and X11 WM_CLASS
  match the desktop entry slate/linuxapp.py installs: the dock and Alt+Tab then show slate's name and icon, not Python's.
* Placement: GTK has the window's size and whether it is maximized, and on X11 its position too; Wayland lets no program
  place its own window. The size is the one before maximizing, followed through configure events.
* A second launch hands over the activation token its launcher gave it (XDG_ACTIVATION_TOKEN on Wayland, DESKTOP_STARTUP_ID
  on X11; instance.py), which is what lets the running window come forward rather than only ask for attention.
"""
import os
import subprocess

APP_ID = 'io.github.bribak.slate'  # also the name of the desktop entry (slate/linuxapp.py)
_normal = {}  # the window's size and position while it is neither maximized nor full screen, for get_placement


def allow_foreground(pid):
    pass  # the activation token goes along with the files instead (instance.hand_off)


def activation():
    """The token this process was started with, for the running slate to come forward with; None from a terminal."""
    return os.environ.get('XDG_ACTIVATION_TOKEN') or os.environ.get('DESKTOP_STARTUP_ID')


def bring_to_front(title, token = None):
    from gi.repository import GLib, Gtk

    def present():
        for window in Gtk.Window.list_toplevels():
            if window.get_title() == title and window.get_visible():
                if token:
                    window.set_startup_id(token)
                window.deiconify()
                window.present()
        return False
    GLib.idle_add(present)


def prepare():
    """Before pywebview imports GTK: importing PyGObject's Gdk initialises it, and the program's name is read then."""
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        try:
            gi.require_version('WebKit2', '4.1')
        except ValueError:
            gi.require_version('WebKit2', '4.0')  # pywebview takes either
    except (ImportError, ValueError) as e:
        raise SystemExit(f'slate needs GTK 3 and WebKitGTK for Python ({e}). On Ubuntu or Debian: sudo apt install python3-gi gir1.2-webkit2-4.1, '
                         'then run slate from a venv made with --system-site-packages (README.md, Install).') from None
    from gi.repository import GLib
    GLib.set_prgname(APP_ID)
    GLib.set_application_name('slate')
    gi.require_version('Gdk', '3.0')
    from gi.repository import Gdk
    Gdk.set_program_class(APP_ID)  # X11's WM_CLASS; by default the name with its first letter in upper case


def _x11(window):
    return not window.get_display().get_name().startswith('wayland')  # ':0' on X11 (XWayland too), 'wayland-0' on Wayland


def set_placement(window, saved):
    """Size the not-yet-shown window as it was left, and on X11 put it back where it was if a monitor still shows that place.
    Runs on the GTK thread (pywebview's before_show), as does get_placement (closing)."""
    if saved and 'size' in saved:  # a placement from another system has none
        window.resize(*saved['size'])
        x, y = saved.get('position') or (None, None)
        display = window.get_display()
        if x is not None and _x11(window) and any(_inside(x, y, display.get_monitor(i).get_geometry()) for i in range(display.get_n_monitors())):
            window.move(x, y)
    window.connect('configure-event', _follow)


def _inside(x, y, area):
    return area.x <= x + 50 < area.x + area.width and area.y <= y + 20 < area.y + area.height


def _follow(window, event):
    state = window.get_window().get_state() if window.get_window() else 0
    from gi.repository import Gdk
    if not state & (Gdk.WindowState.MAXIMIZED | Gdk.WindowState.FULLSCREEN | Gdk.WindowState.TILED):
        _normal['size'] = list(window.get_size())
        _normal['position'] = list(window.get_position()) if _x11(window) else None
    return False


def get_placement(window):
    size = _normal.get('size') or list(window.get_size())
    return {'size': size, 'position': _normal.get('position'), 'maximized': bool(window.is_maximized())}


def font_dirs():
    """JetBrains IDEs' own font folders (Toolbox, snaps and unpacked archives in /opt or the home folder), then the user's fonts."""
    import glob
    home = os.path.expanduser('~')
    toolbox = os.path.join(home, '.local', 'share', 'JetBrains', 'Toolbox', 'apps')
    # Toolbox 2 keeps an app in apps/<name>, older ones in apps/<name>/ch-0/<build>
    places = (f'{toolbox}/*', f'{toolbox}/*/*/*', '/opt/*', '/opt/*/*', f'{home}/pycharm*', f'{home}/idea*', '/snap/pycharm*/current', '/snap/intellij*/current')
    fonts = [d for p in places for d in glob.glob(os.path.join(p, 'jbr', 'lib', 'fonts'))]
    fonts.sort(key = os.path.getmtime, reverse = True)  # the most recently installed IDE first
    return fonts + [os.path.join(home, '.local', 'share', 'fonts'), '/usr/share/fonts/truetype/jetbrains-mono']


def trash(path):
    """Move a file or folder to the Trash (freedesktop.org's, which GNOME Files, Dolphin and the others show), through GIO."""
    from gi.repository import Gio, GLib
    try:
        Gio.File.new_for_path(path).trash(None)
    except GLib.Error as e:
        raise OSError(f'{os.path.basename(path)} was not moved to the Trash: {e.message}') from None


def reveal(path):
    """Select a file in the file manager (freedesktop.org's FileManager1, which GNOME Files, Dolphin, Nemo and Thunar answer),
    else open its folder; a folder's path ending in '.' opens the folder itself."""
    if not path.endswith(os.sep + '.'):
        from gi.repository import Gio, GLib
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call_sync('org.freedesktop.FileManager1', '/org/freedesktop/FileManager1', 'org.freedesktop.FileManager1', 'ShowItems',
                          GLib.Variant('(ass)', ([Gio.File.new_for_path(path).get_uri()], '')), None, Gio.DBusCallFlags.NONE, 3000, None)
            return
        except GLib.Error:
            pass
    subprocess.Popen(['xdg-open', path[:-2] if path.endswith(os.sep + '.') else os.path.dirname(path)])
