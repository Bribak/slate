"""Python's cyclic garbage collector, paused while a big file is parsed, tokenized and checked.

ast.parse and tokenize make hundreds of thousands of objects that form no cycles, and every few hundred allocations the
collector walks the young ones, and now and then everything alive, the new tree included. Paused, a lint of CandyCrunch's
prediction.py (5,437 lines) took 428 ms instead of 663, draw.py 185 instead of 248, with the same markers on all 117 .py files
of the lab's repositories; glycowork's loader pauses it for df_glycan the same way. Garbage made meanwhile is collected once
it runs again, which leaves slate's process 3-5 MB bigger in the bench (84-86 MB against 79-83).
Pausing only while the tree and tokens are made, or also freezing them out of the collector's sight for the checks
(gc.freeze), was slower and cost more: 88-93 MB in the bench.
"""
import contextlib
import gc
import threading

_lock = threading.Lock()
_paused = 0  # how many callers are inside paused(): pywebview runs each call on its own thread, and they overlap
_was_enabled = True


@contextlib.contextmanager
def paused():
    global _paused, _was_enabled
    with _lock:
        if not _paused:
            _was_enabled = gc.isenabled()
            gc.disable()
        _paused += 1
    try:
        yield
    finally:
        with _lock:
            _paused -= 1
            if not _paused and _was_enabled:
                gc.enable()
