"""Answer a page from memory when the same thing was asked a moment ago.

Every read of the database costs a network round trip, and a page is made of
many. Three small tools keep a page from paying for them again and again:

``cached``   wraps a read-only endpoint. Asked again within ``fresh`` seconds, it
             answers from memory. Asked again within ``keep`` seconds, it answers
             from memory at once and reads the database again in the background,
             so the next request has the new answer. Older than that, it reads.
             A page somebody opened in the last twenty minutes is kept warm: a
             background thread re-reads it before it goes stale, so opening it
             again never waits on the database.
``memo``     the same idea for one value inside an endpoint.
``gather``   runs several independent reads at the same time instead of one
             after another.

Nothing here may show a person something a change has already overtaken: every
write to the API, and every background job that finishes, calls ``clear``.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from typing import Any, Callable, Dict

from mangotree.core.logging import logger

_lock = threading.Lock()
_cache: Dict[tuple, tuple] = {}
_busy: set = set()
_gen = 0
_wanted: Dict[tuple, tuple] = {}            # key -> (fn, args, kwargs, fresh, last asked for)
_warmer_started = False
WARM_FOR = 20 * 60
_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="read")


def clear() -> None:
    """Something changed: forget every remembered answer."""
    global _gen
    with _lock:
        _cache.clear()
        _gen += 1


def _store(key: tuple, gen: int, value: Any) -> None:
    with _lock:
        if gen == _gen:                     # a change arrived while this was being read: do not keep it
            _cache[key] = (time.time(), value)


def _warm_loop() -> None:
    """Re-read, one at a time, whatever was asked for recently and is about to go stale."""
    # Stops with the process: once the main thread has ended there is nobody left to answer.
    while threading.main_thread().is_alive():
        time.sleep(4)
        if not threading.main_thread().is_alive():
            return
        try:
            now = time.time()
            with _lock:
                due = [(k, w) for k, w in _wanted.items() if now - w[4] < WARM_FOR and (k not in _cache or now - _cache[k][0] > w[3] * 0.8) and k not in _busy]
                for k in [k for k, w in _wanted.items() if now - w[4] >= WARM_FOR]:
                    _wanted.pop(k, None)
            for k, (fn, args, kwargs, _fresh, _asked) in due[:6]:
                with _lock:
                    gen = _gen
                    _busy.add(k)
                try:
                    _store(k, gen, fn(*args, **kwargs))
                except Exception:
                    logger.exception("keeping %s warm failed", k[0])
                finally:
                    with _lock:
                        _busy.discard(k)
        except Exception:
            logger.exception("warm loop")


def _ensure_warmer() -> None:
    global _warmer_started
    with _lock:
        if _warmer_started:
            return
        _warmer_started = True
    threading.Thread(target=_warm_loop, daemon=True, name="keep-warm").start()


def cached(name: str, *, fresh: int = 20, keep: int = 120, per_user: bool = True, warm: bool = True) -> Callable:
    def deco(fn: Callable) -> Callable:
        @wraps(fn)
        def inner(*args, **kwargs):
            user = kwargs.get("user") if per_user and isinstance(kwargs.get("user"), dict) else {}
            key = (name, user.get("user_id"), user.get("role"), args, tuple(sorted((k, repr(v)) for k, v in kwargs.items() if k != "user")))
            now = time.time()
            if warm:
                _ensure_warmer()
                with _lock:
                    _wanted[key] = (fn, args, kwargs, fresh, now)
            with _lock:
                hit, gen = _cache.get(key), _gen
            if hit and now - hit[0] < fresh:
                return hit[1]
            if hit and now - hit[0] < keep:
                with _lock:
                    start = key not in _busy
                    if start:
                        _busy.add(key)
                if start:
                    def work():
                        try:
                            _store(key, gen, fn(*args, **kwargs))
                        except Exception:
                            logger.exception("background refresh of %s failed", name)
                        finally:
                            with _lock:
                                _busy.discard(key)
                    threading.Thread(target=work, daemon=True, name=f"refresh-{name}").start()
                return hit[1]
            value = fn(*args, **kwargs)
            _store(key, gen, value)
            return value
        return inner
    return deco


def memo(key: Any, ttl: int, fn: Callable[[], Any]) -> Any:
    """One value, remembered for ``ttl`` seconds."""
    k = ("memo", key)
    now = time.time()
    with _lock:
        hit, gen = _cache.get(k), _gen
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = fn()
    _store(k, gen, value)
    return value


def gather(**reads: Callable[[], Any]) -> Dict[str, Any]:
    """Run independent reads side by side; the page waits for the slowest, not for the sum."""
    try:
        futures = {k: _pool.submit(f) for k, f in reads.items()}
    except RuntimeError:
        # The process is shutting down and the pool no longer takes work: read one after another.
        return {k: f() for k, f in reads.items()}
    return {k: f.result() for k, f in futures.items()}


class ClearOnWrite:
    """Every request that changes something empties the remembered answers. (Plain
    ASGI, so streamed responses pass through untouched.)"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") in ("GET", "HEAD", "OPTIONS"):
            return await self.app(scope, receive, send)

        async def relay(message):
            if message["type"] == "http.response.start" and message.get("status", 500) < 400:
                clear()
            await send(message)
        return await self.app(scope, receive, relay)
