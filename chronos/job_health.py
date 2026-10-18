"""Durable aggregate job outcomes without payloads or exception messages."""
from datetime import datetime, timezone
from functools import wraps
import logging
from time import perf_counter
from starlette.concurrency import run_in_threadpool

KEY = 'runtime:jobs:v1'
logger = logging.getLogger(__name__)


def tracked_job(database):
    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            started = perf_counter()
            status = 'success'
            error_type = None
            try:
                return await function(*args, **kwargs)
            except Exception as error:
                status, error_type = 'failed', type(error).__name__
                raise
            finally:
                now = datetime.now(timezone.utc).isoformat()
                elapsed = round((perf_counter() - started) * 1000, 2)
                def record(old):
                    jobs = dict(old or {})
                    previous = jobs.get(function.__name__, {})
                    jobs[function.__name__] = {
                        **previous, 'last_finished_at': now, 'status': status,
                        'duration_ms': elapsed, 'error_type': error_type,
                        'runs': previous.get('runs', 0) + 1,
                        'consecutive_failures': previous.get('consecutive_failures', 0) + 1 if status == 'failed' else 0,
                    }
                    if status == 'success':
                        jobs[function.__name__]['last_success_at'] = now
                    return jobs
                try:
                    await run_in_threadpool(database().mutate_task_action_state, KEY, record)
                except Exception:
                    logger.warning('Job outcome recording failed: %s', function.__name__)
        return wrapped
    return decorate
