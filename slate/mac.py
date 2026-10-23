"""The macOS side of slate's window (app.py; slate/win.py is the Windows side). Imported on macOS only, where pywebview
brings PyObjC.

pywebview's Cocoa backend needs three changes, made by `prepare` before the window exists:
* Its WKWebView subclass answers Cmd+Z, Cmd+A, Cmd+W and Cmd+Q itself, before the page sees them, so Monaco's undo and
  select-all never ran, Cmd+W closed the whole window instead of a tab, and Cmd+Q quit without asking about unsaved
  files. A local event monitor now hands those keys to WKWebView's own keyDown: (the page first, as in Safari) before
  pywebview's sees them; Cmd+Q closes the window the way its close button does. That class cannot be subclassed: its
  methods call super(BrowserView.WebKitHost, self), which looks the class up by name, so a subclass put in its place made
  every such call recurse into itself.
* Its app delegate has no application:openFiles:, which Finder and the Dock use to hand an app files. Its methods use no
  such super, so a subclass takes its place. NSTreatUnknownArgumentsAsOpen = NO keeps AppKit from passing it the
  process's own command line as files too.
* The menu bar would say "Python", the name of the process's bundle.
"""
import os

_monitor = None  # the key monitor, kept alive for the life of the app


def allow_foreground(pid):
    pass  # on macOS the running app brings itself forward (bring_to_front)


def bring_to_front(title):
    import AppKit
    from PyObjCTools import AppHelper
    AppHelper.callAfter(lambda: AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True))


def prepare(open_paths, quitting):
    """open_paths(paths) for files dropped on the Dock icon or opened from Finder; quitting() when the app quits from its menu,
    which ends the process without closing the window first."""
    import Foundation
    bundle = Foundation.NSBundle.mainBundle()
    for info in (bundle.localizedInfoDictionary(), bundle.infoDictionary()):
        if info is not None:
            info['CFBundleName'] = 'slate'  # read when the menu bar is built, so before pywebview's Cocoa module creates the app
    Foundation.NSProcessInfo.processInfo().setProcessName_('slate')
    # Once the delegate below takes files, AppKit also "opens" the process's own command line: `python tools/smoke.py` opened
    # smoke.py, and `slate file.py` would open file.py twice. Arguments reach slate through sys.argv. Registered, so not saved
    # in Python's own defaults
    Foundation.NSUserDefaults.standardUserDefaults().registerDefaults_({'NSTreatUnknownArgumentsAsOpen': 'NO'})
    import AppKit
    import objc
    from webview.platforms import cocoa
    global _monitor
    host = cocoa.BrowserView.WebKitHost
    forwarded = set()  # (timestamp, key code) of keys handed to the page, which WebKit sends again if the page leaves them

    def key_down(event):
        """None to swallow the event, which went to the page or closed the window; the event to let AppKit route it."""
        if not event.modifierFlags() & AppKit.NSEventModifierFlagCommand:
            return event
        key = (event.charactersIgnoringModifiers() or '').lower()
        view = next((i.webview for i in list(cocoa.BrowserView.instances.values()) if i.window == event.window()), None)
        if view is None or key not in ('z', 'a', 'w', 'q'):
            return event
        if key == 'q':
            view.window().performClose_(None)  # asks about unsaved files, and closing the only window ends slate
            return None
        stamp = (event.timestamp(), event.keyCode())
        if stamp in forwarded:  # the page did not take it: now the menus (Select All outside the editor) may
            forwarded.discard(stamp)
            return event
        if len(forwarded) > 100:
            forwarded.clear()
        forwarded.add(stamp)
        objc.super(host, view).keyDown_(event)  # WKWebView's own keyDown:, past pywebview's
        return None
    _monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(AppKit.NSEventMaskKeyDown, key_down)
    delegate = cocoa.BrowserView.AppDelegate

    class SlateAppDelegate(delegate):
        def application_openFiles_(self, app, names):
            open_paths([str(n) for n in names])
            app.replyToOpenOrPrint_(0)  # NSApplicationDelegateReplySuccess

        def applicationWillTerminate_(self, notification):
            quitting()
    cocoa.BrowserView.AppDelegate = SlateAppDelegate


def get_placement(window):
    """The window's frame in Cocoa's screen points (origin bottom left), and whether it is zoomed. Read from the NSWindow
    directly: pywebview's window.x waits for the main thread, which is the thread running the closing event."""
    f = window.frame()
    return {'frame': [f.origin.x, f.origin.y, f.size.width, f.size.height], 'maximized': bool(window.isZoomed())}


def set_placement(window, saved):
    """Put the window back where it was, unless no screen shows that place any more."""
    import AppKit
    if 'frame' not in saved:
        return  # a placement from Windows
    x, y, w, h = saved['frame']
    frame = AppKit.NSMakeRect(x, y, w, h)
    if any(AppKit.NSIntersectsRect(frame, s.visibleFrame()) for s in AppKit.NSScreen.screens()):
        window.setFrame_display_(frame, True)


def font_dirs():
    """JetBrains IDEs' own font folders (app bundles and Toolbox installs), then the installed fonts."""
    home = os.path.expanduser('~')
    apps = []
    for base in ('/Applications', os.path.join(home, 'Applications'), os.path.join(home, 'Library/Application Support/JetBrains/Toolbox/apps')):
        if os.path.isdir(base):
            for root, dirs, _ in os.walk(base):
                if root.endswith('.app') or root.count(os.sep) - base.count(os.sep) >= 4:
                    dirs[:] = []  # never into a bundle; Toolbox nests its apps 4 folders down (apps/PyCharm-P/ch-0/<build>)
                    if root.endswith('.app') and os.path.isdir(os.path.join(root, 'Contents', 'jbr')):
                        apps.append(root)
    apps.sort(key = os.path.getmtime, reverse = True)  # the most recently installed IDE first
    return [os.path.join(a, 'Contents', 'jbr', 'Contents', 'Home', 'lib', 'fonts') for a in apps] + [os.path.join(home, 'Library', 'Fonts'), '/Library/Fonts']


def trash(path):
    """Move a file or folder to the Trash, as Finder's Move to Trash does."""
    from Foundation import NSURL, NSFileManager
    ok, _, error = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(NSURL.fileURLWithPath_(path), None, None)
    if not ok:
        raise OSError(f'{os.path.basename(path)} was not moved to the Trash: {error.localizedDescription() if error else "unknown error"}')
