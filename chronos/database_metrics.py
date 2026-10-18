"""Bounded process-local database timing, without arguments or result data."""
from collections import defaultdict
from functools import wraps
from threading import Lock
from time import perf_counter

_lock = Lock()
_metrics = defaultdict(lambda: {'calls': 0, 'failures': 0, 'total_ms': 0.0, 'max_ms': 0.0})


def measured(function):
    @wraps(function)
    def run(*args, **kwargs):
        start = perf_counter()
        failed = False
        try:
            return function(*args, **kwargs)
        except Exception:
            failed = True
            raise
        finally:
            elapsed = (perf_counter() - start) * 1000
            with _lock:
                entry = _metrics[function.__qualname__]
                entry['calls'] += 1
                entry['failures'] += int(failed)
                entry['total_ms'] += elapsed
                entry['max_ms'] = max(entry['max_ms'], elapsed)
    return run


def snapshot():
    with _lock:
        return {name: {**entry, 'mean_ms': round(entry['total_ms'] / entry['calls'], 3)}
                for name, entry in _metrics.items()}
