# server/app/events.py
"""Tiny in-process pub/sub used for Server-Sent Events.

Single-process only: the app is served by one waitress process, so a dict of
subscriber queues is enough. Each key (for example a pairing token) has its own
set of subscriber queues.
"""
import queue
import threading

_lock = threading.Lock()
_subscribers: dict[str, set] = {}


def subscribe(key: str) -> "queue.Queue":
    channel = queue.Queue()
    with _lock:
        _subscribers.setdefault(key, set()).add(channel)
    return channel


def unsubscribe(key: str, channel) -> None:
    with _lock:
        subs = _subscribers.get(key)
        if subs is not None:
            subs.discard(channel)
            if not subs:
                _subscribers.pop(key, None)


def publish(key: str, event: str, data=None) -> None:
    """Deliver an event to every subscriber of `key` (never blocks)."""
    with _lock:
        channels = list(_subscribers.get(key, ()))
    message = {"event": event, "data": data or {}}
    for channel in channels:
        channel.put(message)
