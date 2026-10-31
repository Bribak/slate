"""A local usage log, %APPDATA%/slate/usage.jsonl, for improving slate from how it is actually used.

Nothing leaves the machine and no file contents are written: calls and their timings, keys, errors with their stack traces,
window and monitor changes. "usage_log": false in state.json turns it off.

python -m slate.usage [days]   summarises the last 30 (or given) days
"""
import json
import os
import sys
import threading
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import median

from slate import state

MAX_BYTES = 2_000_000  # then usage.jsonl becomes usage.1.jsonl, so the log never holds more than about 4 MB
enabled = True
_lock = threading.Lock()


def log_file():
    return state.STATE_FILE.parent / 'usage.jsonl'  # looked up on each call, since the dev server and bench move STATE_FILE


def record(kind, **fields):
    if not enabled:
        return
    line = json.dumps({'t': datetime.now().isoformat(timespec = 'seconds'), 'pid': os.getpid(), 'kind': kind, **fields}, default = str)
    with _lock:
        try:
            path = log_file()
            path.parent.mkdir(parents = True, exist_ok = True)
            if path.is_file() and path.stat().st_size > MAX_BYTES:
                path.replace(path.with_name('usage.1.jsonl'))
            with path.open('a', encoding = 'utf-8') as f:
                f.write(line + '\n')
        except OSError:
            pass  # the log must never get in the way of the editor


def read(days = 30):
    since = (datetime.now() - timedelta(days = days)).isoformat()
    events = []
    for path in (log_file().with_name('usage.1.jsonl'), log_file()):
        try:
            lines = path.read_text(encoding = 'utf-8').splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue  # a line cut short by a crash
            if event.get('t', '') >= since:
                events.append(event)
    return events


def summary(events):
    """Plain-text digest of the log: where time goes, what is used, what failed."""
    by_kind = defaultdict(list)
    for e in events:
        by_kind[e['kind']].append(e)
    hours = sum(e.get('seconds', 0) for e in by_kind['end']) / 3600
    out = [f"{len(by_kind['start'])} sessions, {hours:.1f} h ({events[0]['t'][:10]} to {events[-1]['t'][:10]})" if events else 'no usage logged yet']
    if by_kind['ready']:
        out.append(f"startup to restored session: median {median(e['ms'] for e in by_kind['ready']):.0f} ms, max {max(e['ms'] for e in by_kind['ready']):.0f} ms")
        opened = [e['file_ms'] for e in by_kind['ready'] if e.get('file_ms')]
        if opened:
            out.append(f"startup to double-clicked file: median {median(opened):.0f} ms, max {max(opened):.0f} ms ({len(opened)} starts)")
    if by_kind['page']:
        started = [e['seconds'] for e in by_kind['page']]
        out.append(f"page asked for its state: median {median(started):.2f} s after launch, max {max(started):.2f} s"
                   f" ({len(started)} of {len(by_kind['shown'])} windows)")
    if by_kind['page_stalled']:
        out.append(f"page stalled {len(by_kind['page_stalled'])} times and was loaded again: {by_kind['page_stalled'][-1]}")
    calls, keys, long_tasks = defaultdict(lambda: [0, 0.0, 0.0, 0]), Counter(), [0, 0, 0.0]
    for e in by_kind['activity']:
        for name, (n, total, worst, errors) in e.get('calls', {}).items():
            c = calls[name]
            c[0] += n
            c[1] += total
            c[2] = max(c[2], worst)
            c[3] += errors
        keys.update(e.get('keys', {}))
        n, slow, worst = e.get('long', [0, 0, 0])
        long_tasks = [long_tasks[0] + n, long_tasks[1] + slow, max(long_tasks[2], worst)]
    if calls:
        out.append('\nAPI calls (count, mean ms, max ms, errors):')
        out += [f'  {name:16} {n:7} {total / n:8.1f} {worst:8.0f} {errors:5}' for name, (n, total, worst, errors) in sorted(calls.items(), key = lambda kv: -kv[1][0])]
    if long_tasks[0]:
        out.append(f'\npage tasks over 50 ms: {long_tasks[0]}, over 200 ms: {long_tasks[1]}, longest {long_tasks[2]:.0f} ms')
    if by_kind['slow']:
        slow = Counter(e['call'] for e in by_kind['slow'])
        out.append('slow calls (over 1 s): ' + ', '.join(f"{name} x{n} (max {max(e['ms'] for e in by_kind['slow'] if e['call'] == name):.0f} ms)" for name, n in slow.most_common()))
    if keys:
        out.append('\nslate keys: ' + ', '.join(f'{k} {n}' for k, n in keys.most_common()))
    window = Counter(e['kind'] for e in events if e['kind'] in ('maximized', 'restored', 'monitor'))
    dpis = sorted({e['dpi'] for e in events if 'dpi' in e})
    if window or dpis:
        out.append('window: ' + ', '.join([f'{k} {n}' for k, n in window.items()] + [f"monitor DPIs seen {' '.join(map(str, dpis))}"]))
    errors = defaultdict(list)
    for e in by_kind['api_error'] + by_kind['js_error'] + by_kind['console_exit']:
        errors[(e['kind'], e.get('call', ''), e.get('message', str(e.get('code'))))].append(e)
    if errors:
        out.append('\nerrors (first seen per session; count, last seen):')
        for (kind, call, message), seen in sorted(errors.items(), key = lambda kv: -len(kv[1])):
            out.append(f"  {len(seen):4}  {seen[-1]['t'][:16]}  {kind} {call} {message}")
            frames = [line.strip() for line in seen[-1].get('stack', '').splitlines() if line.strip().startswith(('File ', 'at '))]
            if frames:  # the innermost frame: last in a Python traceback, first in a JS stack
                out.append('        ' + (frames[-1] if frames[-1].startswith('File ') else frames[0])[:200])
    return '\n'.join(out)


if __name__ == '__main__':
    print(summary(read(days = int(sys.argv[1]) if len(sys.argv) > 1 else 30)))
