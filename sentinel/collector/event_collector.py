"""
The Event Collector.

Two responsibilities, kept deliberately separate:

  1. `trace_stage(name)` -- a decorator that wraps any function and
     emits a StageEvent every time it's called, without changing its
     return value, arguments, or exceptions in any way.

  2. `EventCollector` -- an in-memory queue drained by a background
     writer thread. Emitting an event is just a `queue.put()`, which is
     effectively free. Disk I/O (writing to sentinel_events.jsonl)
     happens on a separate thread so the chatbot's response path is
     never slowed down by observability. This is the same reason real
     APM agents batch and ship telemetry asynchronously.
"""

import atexit
import json
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from functools import wraps

from .schema import StageEvent
from ..context import trace_id_var, query_id_var
from ..config import EVENTS_PATH

_PRIMITIVE_TYPES = (str, int, float, bool, list, tuple, dict, type(None))


def _summarize(value, max_preview=200) -> dict:
    """
    Cheap, best-effort summary of a value for tracing purposes.
    Deliberately never stores full payloads: that's a cost problem
    (every embedding vector logged in full) and a privacy problem
    (raw user queries/answers sitting in a trace store forever).
    """
    try:
        if value is None:
            return {"type": "NoneType"}
        if isinstance(value, str):
            return {"type": "str", "length": len(value), "preview": value[:max_preview]}
        if isinstance(value, (list, tuple)):
            return {"type": type(value).__name__, "length": len(value)}
        if isinstance(value, dict):
            return {"type": "dict", "keys": list(value.keys())[:10]}
        if isinstance(value, (int, float, bool)):
            return {"type": type(value).__name__, "value": value}
        return {"type": type(value).__name__}
    except Exception:
        return {"type": "unknown"}


def _relevant_args(args, kwargs) -> list:
    """
    Filters out `self` / non-primitive objects (e.g. a Retriever
    instance) so we only summarize the data that actually matters for
    debugging -- not the object a method happened to be called on.
    """
    filtered = [a for a in args if isinstance(a, _PRIMITIVE_TYPES)]
    filtered.extend(kwargs.values())
    return filtered


class EventCollector:
    def __init__(self, output_path: str = EVENTS_PATH, flush_interval: float = 0.5):
        self._queue: "queue.Queue[StageEvent]" = queue.Queue()
        self._output_path = output_path
        self._flush_interval = flush_interval
        self._stop_event = threading.Event()
        self._buffer: list[StageEvent] = []
        self._lock = threading.Lock()
        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._writer_thread.start()

    def emit(self, event: StageEvent) -> None:
        self._queue.put(event)
        with self._lock:
            self._buffer.append(event)

    def flush(self) -> None:
        """
        Synchronously block until all pending events in the queue are written to the file.
        """
        self._queue.join()

    def _writer_loop(self) -> None:
        with open(self._output_path, "a", encoding="utf-8") as f:
            while not self._stop_event.is_set():
                try:
                    event = self._queue.get(timeout=self._flush_interval)
                except queue.Empty:
                    continue

                try:
                    f.write(event.model_dump_json() + "\n")
                    f.flush()
                finally:
                    self._queue.task_done()

    def get_events(self, trace_id: str | None = None) -> list[StageEvent]:
        with self._lock:
            events = list(self._buffer)
        if trace_id:
            events = [e for e in events if e.trace_id == trace_id]
        return sorted(events, key=lambda e: e.timestamp_start)

    def stop(self) -> None:
        """
        Stop the background writer and synchronously flush anything still
        sitting in the queue. Without this, a daemon thread can be killed
        mid-drain when the process exits, silently dropping the last
        batch of events -- exactly the kind of gap an observability
        system can't afford to have.
        """
        self._stop_event.set()
        self._writer_thread.join(timeout=2)

        remaining = []
        try:
            while True:
                remaining.append(self._queue.get_nowait())
        except queue.Empty:
            pass

        if remaining:
            with open(self._output_path, "a", encoding="utf-8") as f:
                for event in remaining:
                    f.write(event.model_dump_json() + "\n")


_collector = EventCollector()
atexit.register(_collector.stop)


def get_collector() -> EventCollector:
    return _collector


def trace_stage(stage_name: str):
    """
    Decorator factory: `@trace_stage("embedding_generation")`.

    Wraps a function so every call emits a StageEvent tagged with the
    current trace_id/query_id (read from contextvars, set once per
    incoming query -- see instrument.py). The wrapped function's
    behavior, return value, and exceptions are completely unchanged.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            trace_id = trace_id_var.get() or "untraced"
            query_id = query_id_var.get()

            start_wall = datetime.now(timezone.utc)
            start_perf = time.perf_counter()
            status: str = "success"
            error_msg = None
            result = None

            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                status = "error"
                error_msg = f"{type(e).__name__}: {e}"
                raise
            finally:
                end_perf = time.perf_counter()
                end_wall = datetime.now(timezone.utc)

                relevant_inputs = _relevant_args(args, kwargs)

                event = StageEvent(
                    event_id=str(uuid.uuid4()),
                    trace_id=trace_id,
                    query_id=query_id,
                    stage_name=stage_name,
                    timestamp_start=start_wall,
                    timestamp_end=end_wall,
                    latency_ms=round((end_perf - start_perf) * 1000, 3),
                    status=status,
                    input_summary={"args": [_summarize(a) for a in relevant_inputs]},
                    output_summary=_summarize(result) if status == "success" else {},
                    metadata={"function": func.__qualname__},
                    error=error_msg,
                )
                get_collector().emit(event)

        return wrapper
    return decorator
