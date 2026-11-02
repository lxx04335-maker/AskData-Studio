"""Request-local streaming; only public text and progress leave the workflow."""
from __future__ import annotations

import json
from contextvars import ContextVar
from queue import Empty, Full, Queue
from threading import Event, Thread
from typing import Any, Callable

event_sink: ContextVar[Callable[[dict], None] | None] = ContextVar("event_sink", default=None)


def emit(event: str, **data: Any) -> None:
    sink = event_sink.get()
    if sink:
        sink({"event": event, **data})


def partial_strings(raw: str) -> dict[str, str]:
    """Read top-level JSON strings, including an unfinished string, without JSON syntax."""
    decoder = json.JSONDecoder()
    start = raw.find("{")
    if start < 0:
        return {}
    pos = start + 1
    result: dict[str, str] = {}
    while pos < len(raw):
        while pos < len(raw) and raw[pos] in " \r\n\t,":
            pos += 1
        try:
            key, end = decoder.raw_decode(raw, pos)
        except ValueError:
            break
        if not isinstance(key, str):
            break
        pos = end
        while pos < len(raw) and raw[pos] in " \r\n\t:":
            pos += 1
        if pos >= len(raw):
            break
        if raw[pos] != '"':
            try:
                _, pos = decoder.raw_decode(raw, pos)
            except ValueError:
                break
            continue
        try:
            value, pos = decoder.raw_decode(raw, pos)
            result[key] = value
        except ValueError:
            # Decode only complete escape sequences; hold split unicode escapes.
            tail = raw[pos + 1:]
            chars: list[str] = []
            i = 0
            while i < len(tail):
                if tail[i] == '"':
                    break
                if tail[i] != "\\":
                    chars.append(tail[i]); i += 1
                    continue
                width = 6 if tail[i:i+2] == "\\u" else 2
                if i + width > len(tail):
                    break
                try:
                    chars.append(json.loads('"' + tail[i:i+width] + '"'))
                except ValueError:
                    break
                i += width
            result[key] = "".join(chars)
            break
    return result


def stream_response(operation: Callable):
    """Run blocking model I/O off the response iterator and send heartbeats while idle."""
    queue: Queue = Queue(maxsize=256)
    closed = Event()

    def send(item):
        while not closed.is_set():
            try:
                queue.put(item, timeout=.2)
                return
            except Full:
                pass

    def run():
        token = event_sink.set(send)
        try:
            emit("progress", stage="start", message="正在理解问题…")
            result = operation()
            emit("result", result=result.model_dump(mode="json"))
        except Exception as exc:
            emit("error", message=str(exc))
        finally:
            event_sink.reset(token)
            send(None)

    Thread(target=run, daemon=True).start()
    try:
        while True:
            try:
                item = queue.get(timeout=10)
            except Empty:
                yield ": heartbeat\n\n"
                continue
            if item is None:
                break
            yield "data: " + json.dumps(item, ensure_ascii=True) + "\n\n"
    finally:
        closed.set()
