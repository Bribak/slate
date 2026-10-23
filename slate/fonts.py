"""The editor font: the JetBrains Mono files a JetBrains IDE ships with, so the code looks the way it did in PyCharm.

Repackaged copies installed into Windows can differ in hinting and metrics; the IDE's own files are the reference.
"""
import base64
import os
from pathlib import Path

STYLES = {'Regular': ('normal', 'normal'), 'Italic': ('italic', 'normal')}  # slate draws nothing in bold


def font_dirs():
    local = Path(os.environ.get('LOCALAPPDATA', '').removeprefix('\\\\?\\'))
    ides = list(Path(os.environ.get('ProgramFiles', r'C:\Program Files')).glob('JetBrains/*')) + list((local / 'Programs').glob('*'))
    ides.sort(key = lambda p: p.stat().st_mtime, reverse = True)  # the most recently installed IDE first
    return [d / 'jbr' / 'lib' / 'fonts' for d in ides] + [Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts', local / 'Microsoft' / 'Windows' / 'Fonts']


def jetbrains_mono():
    """[{style, weight, data (base64 TTF)}] for the first folder holding JetBrains Mono, or [] to fall back to installed fonts."""
    for folder in font_dirs():
        files = {name: folder / f'JetBrainsMono-{name}.ttf' for name in STYLES}
        if files['Regular'].is_file():
            return [{'style': STYLES[name][0], 'weight': STYLES[name][1], 'data': base64.b64encode(path.read_bytes()).decode('ascii')}
                    for name, path in files.items() if path.is_file()]
    return []
