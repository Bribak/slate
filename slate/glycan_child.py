"""The process that draws glycans for the editor (slate/glycans.py). It imports glycowork once and then answers one JSON line
per request on stdin, a list of IUPAC-condensed glycans, with one JSON line on stdout: {glycan: [small, full] or null}.

small is drawn compact and without linkage labels, for next to the string; full is the usual GlycoDraw, for the hover.
Anything glycowork prints goes to stderr, which the parent discards, so stdout carries only the replies.
"""
import json
import os
import sys
import warnings

REAL_OUT = sys.stdout


def send(msg):
    REAL_OUT.write(json.dumps(msg) + '\n')
    REAL_OUT.flush()


def draw(glycan, GlycoDraw):
    try:
        return [GlycoDraw(glycan, compact = True, show_linkage = False, suppress = True).as_svg(), GlycoDraw(glycan, suppress = True).as_svg()]
    except Exception:  # not a glycan after all, or one GlycoDraw cannot draw: the editor then shows nothing for it
        return None


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or '.') != here]  # slate's own modules are not the user's
    sys.path[:0] = sys.argv[1:]  # the open folders, so a glycowork checkout among them is the one that draws
    sys.stdout = sys.stderr
    warnings.filterwarnings('ignore')
    try:
        import glycowork
        from glycowork.motif.draw import GlycoDraw
    except Exception as e:
        send({'error': f'{type(e).__name__}: {e}'})
        return
    send({'ready': getattr(glycowork, '__version__', '?'), 'file': glycowork.__file__})
    for line in sys.stdin:  # on the main thread: a read pending in a background thread makes DLL loading hang on Windows
        send({glycan: draw(glycan, GlycoDraw) for glycan in json.loads(line)})


if __name__ == '__main__':
    main()
