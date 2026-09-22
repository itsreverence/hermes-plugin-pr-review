"""Tool-less Responses call through the installed Hermes auth and stream parser.

Budgets bound decoded events BEFORE Hermes accumulates them, not the SDK's
individual SSE-frame decoding. The wall deadline also interrupts blocked reads
and heartbeat-only streams. Synchronous POSIX/main-thread use only; fail before
inference if the process already owns an ITIMER_REAL timer.
"""
import json
import math
import signal
import threading
from contextlib import contextmanager
from types import SimpleNamespace


MAX_STREAM_EVENTS = 4096
MAX_STREAM_BYTES = 1_000_000
MAX_EVENT_BYTES = 240_000
MAX_OUTPUT_ITEMS = 128  # Cumulative item occurrences, including added/done/terminal.
MAX_OUTPUT_CHARS = 120_000


class _DeadlineExpired(BaseException):
    """Escape SDK retry/error handlers and Hermes' best-effort callbacks."""


@contextmanager
def wall_deadline(timeout):
    """Own one POSIX wall timer; never replace another caller's active timer."""
    if (not hasattr(signal, "setitimer") or not hasattr(signal, "SIGALRM")
            or threading.current_thread() is not threading.main_thread()):
        raise ValueError("judgment deadline requires a POSIX main thread")
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("invalid judgment deadline")
    if any(signal.getitimer(signal.ITIMER_REAL)):
        raise ValueError("judgment deadline cannot replace an active timer")
    previous = signal.getsignal(signal.SIGALRM)

    def expire(signum, frame):
        raise _DeadlineExpired()

    signal.signal(signal.SIGALRM, expire)
    try:
        signal.setitimer(signal.ITIMER_REAL, timeout)
        try:
            yield
        except _DeadlineExpired:
            raise TimeoutError("judgment time budget exhausted") from None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _json_default(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, SimpleNamespace):
        return vars(value)
    raise ValueError("unexpected event structure")


def _event_size(event):
    # Incremental encoding avoids constructing a second entire event string.
    size = 0
    encoder = json.JSONEncoder(ensure_ascii=False, default=_json_default)
    for chunk in encoder.iterencode(event):
        size += len(chunk.encode("utf-8"))
        if size > MAX_EVENT_BYTES:
            raise ValueError("judgment event size budget exhausted")
    return size


def _check_item(item):
    if _get(item, "type") not in {"message", "reasoning"}:
        raise ValueError("tool output rejected")


def _guarded_events(stream, terminal):
    count = size = items = 0
    for event in stream:
        count += 1
        if count > MAX_STREAM_EVENTS:
            raise ValueError("judgment event budget exhausted")
        size += _event_size(event)
        if size > MAX_STREAM_BYTES:
            raise ValueError("judgment stream byte budget exhausted")
        kind = _get(event, "type", "")
        if not isinstance(kind, str):
            raise ValueError("unexpected event type")
        if "function_call" in kind:
            raise ValueError("tool output rejected")
        if kind in {"response.output_item.added", "response.output_item.done"}:
            items += 1
            _check_item(_get(event, "item"))
        if kind in {"response.completed", "response.incomplete", "response.failed"}:
            final = _get(event, "response")
            output = _get(final, "output")
            if output is not None:
                if not isinstance(output, list):
                    raise ValueError("unexpected terminal output")
                items += len(output)
                for item in output:
                    _check_item(item)
            terminal.append((kind, final))
        if items > MAX_OUTPUT_ITEMS:
            raise ValueError("judgment output item budget exhausted")
        # Guard outside on_event: Hermes swallows ordinary callback exceptions.
        yield event


def complete(client, *, model, messages, timeout, reasoning, consume=None):
    """Borrow a client for a bounded call; close the stream, not the client."""
    with wall_deadline(timeout):
        return _complete(client, model=model, messages=messages, timeout=timeout,
                         reasoning=reasoning, consume=consume)


def _complete(client, *, model, messages, timeout, reasoning, consume=None):
    """Internal call: caller MUST already hold wall_deadline through cleanup."""
    if consume is None:
        from agent.codex_runtime import _consume_codex_event_stream
        consume = _consume_codex_event_stream
    if hasattr(client, "max_retries"):
        client.max_retries = 0
    terminal = []
    # No hosted tools, functions, agent loop, histories, or implicit user config.
    stream = client.responses.create(
        model=model, instructions=messages[0]["content"],
        input=[{"role": "user", "content": messages[1]["content"]}],
        tools=[], store=False, stream=True, timeout=timeout,
        reasoning={"effort": reasoning},
    )
    try:
        response = consume(_guarded_events(stream, terminal), model=model)
    finally:
        close = getattr(stream, "close", None)
        if close:
            close()
    if len(terminal) != 1:
        raise ValueError("missing or ambiguous completion event")
    kind, final = terminal[0]
    if kind != "response.completed" or _get(final, "status") != "completed":
        raise ValueError("provider did not complete")
    reported_model = _get(final, "model")
    if reported_model != model:
        raise ValueError("provider model identity differs from requested route")
    answers = []
    for item in _get(response, "output", []) or []:
        kind = _get(item, "type")
        if kind == "reasoning":
            continue
        if kind != "message" or _get(item, "role") != "assistant":
            raise ValueError("unexpected output type; tools cannot run")
        phase = _get(item, "phase")
        if phase in {"commentary", "analysis"}:
            continue
        # Phase-less messages are the legacy Responses final-answer form.
        if phase not in {None, "final_answer"}:
            raise ValueError("unexpected answer phase")
        answers.append(item)
    if len(answers) != 1:
        raise ValueError("missing or ambiguous final answer")
    text = []
    size = 0
    for part in _get(answers[0], "content", []) or []:
        if _get(part, "type") != "output_text" or not isinstance(_get(part, "text"), str):
            raise ValueError("unexpected response content")
        size += len(_get(part, "text"))
        if size > MAX_OUTPUT_CHARS:
            raise ValueError("judgment final answer size budget exhausted")
        text.append(_get(part, "text"))
    if not text or not "".join(text).strip():
        raise ValueError("missing final answer text")
    message = SimpleNamespace(content="".join(text), tool_calls=None, function_call=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], model=reported_model)
