"""Time go-to-definition on every STEP-th name of real files, and check the ast answers against jedi's.

python tools/goto.py STEP FILE... [--roots ROOT,ROOT] [--jedi]

Calls slate.navigate.goto on every STEP-th identifier of each FILE in one process (so the first lookup includes jedi's
start), with the folders of %APPDATA%\\slate\\state.json as roots unless --roots names them. Prints the median, 90th and
99th percentile, maximum and total per file. With --jedi it also asks jedi alone wherever the ast answered and lists every
position where they disagree. Expected differences: jedi counts `x[k] = ...` as a definition of x, sends names of
glycowork to a pip-installed copy rather than the open checkout, and finds nothing for some comprehension targets.
"""
import io
import json
import os
import sys
import time
import tokenize
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slate import navigate, state


def spread(times):
    times = sorted(times)
    n = len(times)
    return f'{n} names, median {times[n // 2]:.1f} ms, p90 {times[int(n * .9)]:.0f}, p99 {times[int(n * .99)]:.0f}, max {times[-1]:.0f}, total {sum(times) / 1000:.1f} s'


def where(found):
    return sorted((os.path.normcase(d['path']), d['line']) for d in found or [])


def sweep(path, step, roots, with_jedi = False):
    source = Path(path).read_text(encoding = 'utf-8')
    lines = source.split('\n')
    times, jedi_times, differ = [], [], []
    for t in [t for t in tokenize.generate_tokens(io.StringIO(source).readline) if t.type == tokenize.NAME][::step]:
        line, col = t.start
        navigate._done = (None, None)  # every position is new, as when the mouse moves on
        start = time.perf_counter()
        found = navigate.goto(path, source, line, col, roots)
        times.append((time.perf_counter() - start) * 1000)
        if not with_jedi or navigate._current is None or navigate.find(navigate._current, (line, len(lines[line - 1][:col].encode('utf-8'))), roots) is None:
            continue  # jedi gave this answer anyway
        start = time.perf_counter()
        try:
            alone = navigate.with_jedi(path, source, line, col, roots, navigate._newest[0])
        except navigate.Superseded:
            alone = None
        jedi_times.append((time.perf_counter() - start) * 1000)
        if alone is None or where(alone) != where(found):
            differ.append(f'{Path(path).name}:{line} {t.string!r}\n    jedi {where(alone) if alone is not None else "gave up"}\n    ast  {where(found)}')
    print(f'{path}: {spread(times)}')
    if with_jedi:
        print(f'  jedi alone where the ast answered: {spread(jedi_times)}; {len(differ)} of {len(jedi_times)} differ')
        print('\n'.join(differ))


def main(args):
    with_jedi = '--jedi' in args
    args = [a for a in args if a != '--jedi']
    roots = None
    if '--roots' in args:
        i = args.index('--roots')
        roots = args[i + 1].split(',')
        del args[i:i + 2]
    if roots is None:
        roots = json.loads(state.STATE_FILE.read_text(encoding = 'utf-8')).get('roots', [])
    for path in args[1:]:
        sweep(path, int(args[0]), roots, with_jedi = with_jedi)


if __name__ == '__main__':
    main(sys.argv[1:])
