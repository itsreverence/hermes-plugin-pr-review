"""Offline Responses boundary; optionally exercise installed Hermes and SDK."""
import json
import signal
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/hermes-pr-review/scripts"))
from pr_review_lib import native_transport
from pr_review_lib.native_transport import complete


MODEL = "fixture-model"
MESSAGES = [{"content": "trusted"}, {"content": "untrusted"}]


def message(text="{}", phase=None):
    return {"type": "message", "role": "assistant", "phase": phase,
            "content": [{"type": "output_text", "text": text}]}


def events(items=None, **terminal):
    items = [message()] if items is None else items
    return [*({"type": "response.output_item.done", "item": item, "output_index": i}
              for i, item in enumerate(items)),
            {"type": "response.completed", "response": {
                "status": "completed", "model": MODEL, **terminal}}]


class Stream(list):
    closed = False

    def close(self):
        self.closed = True


def fixture_consume(stream, *, model, on_event=None):
    """Like Hermes, ignore ordinary callback errors and terminal output."""
    output = []
    for event in stream:
        if on_event:
            try:
                on_event(event)
            except (TimeoutError, InterruptedError):
                raise
            except Exception:
                pass
        event = event if isinstance(event, dict) else event.model_dump()
        if event["type"] == "response.output_item.done":
            output.append(event["item"])
        if event["type"] in {"response.completed", "response.incomplete", "response.failed"}:
            break
    return {"output": output}


@pytest.fixture(params=["fixture", "hermes"])
def consume(request):
    if request.param == "hermes":
        return pytest.importorskip("agent.codex_runtime")._consume_codex_event_stream
    return fixture_consume


def call(stream, consume, **kwargs):
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kw: stream))
    return complete(client, model=MODEL, messages=MESSAGES, timeout=30,
                    reasoning="high", consume=consume, **kwargs)


def test_no_tools_or_host_state_transmitted_and_stream_closed(consume):
    captured = {}
    stream = Stream(events([message("fixture")]))

    def create(**kwargs):
        captured.update(kwargs)
        return stream

    client = SimpleNamespace(responses=SimpleNamespace(create=create), max_retries=2)
    reply = complete(client, model=MODEL, messages=MESSAGES, timeout=30,
                     reasoning="high", consume=consume)
    assert reply.choices[0].message.content == "fixture"
    assert stream.closed
    assert client.max_retries == 0
    assert captured == {"model": MODEL, "instructions": "trusted", "input": [{"role": "user", "content": "untrusted"}], "tools": [], "store": False, "stream": True, "timeout": 30, "reasoning": {"effort": "high"}}


@pytest.mark.parametrize("mutation", ["no_terminal", "incomplete", "failed", "wrong_model", "tool", "hosted_tool", "refusal"])
def test_incomplete_or_tool_output_cannot_be_success(mutation, consume):
    data = events()
    if mutation == "no_terminal":
        data.pop()
    elif mutation in ("incomplete", "failed"):
        data[-1]["type"] = "response." + mutation
        data[-1]["response"]["status"] = mutation
    elif mutation == "wrong_model":
        data[-1]["response"]["model"] = "other"
    elif mutation in ("tool", "hosted_tool"):
        data[0]["item"] = {"type": "function_call" if mutation == "tool" else "web_search_call"}
    else:
        data[0]["item"]["content"] = [{"type": "refusal", "refusal": "no"}]
    stream = Stream(data)
    with pytest.raises(ValueError):
        call(stream, consume)
    assert stream.closed


@pytest.mark.parametrize("kind", ["function_call", "web_search_call", "computer_call"])
def test_terminal_only_forbidden_output_rejected(kind, consume):
    stream = Stream(events(output=[{"type": kind}]))
    with pytest.raises(ValueError):
        call(stream, consume)
    assert stream.closed


@pytest.mark.parametrize("terminal", [{}, {"output": None}, {"output": []}])
def test_terminal_output_optional(terminal, consume):
    assert call(Stream(events(**terminal)), consume).choices[0].message.content == "{}"


def test_only_final_answer_phase_is_selected(consume):
    items = [message("I will inspect.", "commentary"), message("Thinking", "analysis"),
             message("{}", "final_answer")]
    assert call(Stream(events(items)), consume).choices[0].message.content == "{}"


@pytest.mark.parametrize("items", [[message(), message()],
                                  [message(phase="final_answer"), message()],
                                  [message(phase="commentary")], [message(phase="unknown")]])
def test_missing_or_ambiguous_answer_rejected(items, consume):
    with pytest.raises(ValueError):
        call(Stream(events(items)), consume)


@pytest.mark.parametrize("budget", ["events", "bytes", "items", "event_size", "final_size"])
def test_budgets_stop_before_parser_accumulation(budget, consume, monkeypatch):
    seen = []
    if budget == "events":
        monkeypatch.setattr(native_transport, "MAX_STREAM_EVENTS", 3, raising=False)
        data = [{"type": "response.output_text.delta", "delta": "a"}] * 20
    elif budget == "bytes":
        monkeypatch.setattr(native_transport, "MAX_STREAM_BYTES", 500, raising=False)
        data = [{"type": "response.reasoning_text.delta", "delta": "é" * 100}] * 20
    elif budget == "items":
        monkeypatch.setattr(native_transport, "MAX_OUTPUT_ITEMS", 3, raising=False)
        data = [{"type": "response.output_item.done", "item": {"type": "reasoning"}}] * 20
    elif budget == "event_size":
        monkeypatch.setattr(native_transport, "MAX_EVENT_BYTES", 500, raising=False)
        data = [{"type": "response.output_item.done", "item": message("é" * 500)}]
    else:
        data = events([message("x" * 120_001)])
    stream = Stream(data + events())

    def counted(stream, **kwargs):
        def count():
            for event in stream:
                seen.append(event)
                yield event
        return consume(count(), **kwargs)

    with pytest.raises(ValueError, match="budget|size"):
        call(stream, counted)
    assert stream.closed
    if budget in {"events", "bytes", "items"}:
        assert len(seen) <= 3, "over-budget events must never reach the real parser"
    if budget == "event_size":
        assert not seen


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="POSIX deadline")
@pytest.mark.parametrize("stage", ["create", "read", "heartbeat"])
def test_real_sdk_wall_deadline_and_stream_cleanup(stage, consume):
    httpx = pytest.importorskip("httpx")
    openai = pytest.importorskip("openai")
    closed = []

    class Bytes(httpx.SyncByteStream):
        def __iter__(self):
            if stage == "read":
                time.sleep(0.8)
            if stage == "heartbeat":
                for _ in range(16):
                    time.sleep(0.05)
                    yield b": heartbeat\n\n"
            for event in events():
                yield ("data: " + json.dumps(event) + "\n\n").encode()

        def close(self):
            closed.append(True)

    def handle(request):
        if stage == "create":
            time.sleep(0.8)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Bytes())

    # A dummy key with MockTransport: no resolver, credentials, sockets or model.
    with openai.OpenAI(api_key="offline-fixture", base_url="https://offline.invalid/v1",
                       http_client=httpx.Client(transport=httpx.MockTransport(handle))) as client:
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            complete(client, model=MODEL, messages=MESSAGES, timeout=0.2,
                     reasoning="high", consume=consume)
        assert time.monotonic() - started < 0.6
        assert client.max_retries == 0
        if stage != "create":
            assert closed


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="POSIX deadline")
def test_existing_alarm_is_not_overridden():
    old_handler = signal.getsignal(signal.SIGALRM)
    signal.setitimer(signal.ITIMER_REAL, 10)
    called = []
    try:
        client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kw: called.append(True)))
        with pytest.raises(ValueError, match="timer|deadline"):
            complete(client, model=MODEL, messages=MESSAGES, timeout=1,
                     reasoning="high", consume=fixture_consume)
        assert not called
        assert 8 < signal.getitimer(signal.ITIMER_REAL)[0] <= 10
        assert signal.getsignal(signal.SIGALRM) == old_handler
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def test_non_main_thread_rejected_before_request():
    from concurrent.futures import ThreadPoolExecutor
    called = []
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kw: called.append(True)))
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(complete, client, model=MODEL, messages=MESSAGES,
                                 timeout=1, reasoning="high", consume=fixture_consume)
        with pytest.raises(ValueError, match="thread|deadline"):
            future.result()
    assert not called
