#!/usr/bin/env python3
import harness

root = harness.use_tree()

from pathlib import Path

from marestail.config import Config
from marestail.runner import Run
from marestail import route
from marestail.tui import collect

state = Run(
    config=Config(root=root, raw={}),
    task=Path("/work/task.md"),
    agent="hermes",
    model="x-ai/grok-4.6",
    effort="xhigh",
    retries=1,
)
prompt = Path("/work/prompt.md")

HERMES_VERIFIED = (
    '{"type":"system","subtype":"init","model":"x-ai/grok-4.6","session_id":"20260918_151301_be7c1c","timestamp":1789740781416}\n'
    '{"type":"tool_use","name":"terminal","input":{"command":"git status --short"},"timestamp":1789740714068}\n'
    '{"type":"tool_result","name":"terminal","output":"{\\"output\\": \\"exit1=0\\", \\"exit_code\\": 0, \\"error\\": null}","duration_ms":218,"is_error":false,"timestamp":1789740714290}\n'
    '{"type":"text","text":"pong","timestamp":1789740800465}\n'
    '{"type":"result","session_id":"20260918_151301_be7c1c","exit_code":0,"text":"pong","tokens":{"input":14851,"output":1,"total":14980,"cache_read":128,"cache_write":0},"duration_ms":19100,"timestamp":1789740800516}'
)

try:
    from marestail import backends
except ImportError:
    backends = None

if backends is not None:
    harness.emit("backends.hermes_command", harness.measure(lambda: backends.hermes_command(state, prompt)))
    harness.emit("backends.hermes_rate_limited", harness.measure(lambda: backends.hermes_rate_limited(2, HERMES_VERIFIED)))
    harness.emit("backends.hermes_summary", harness.measure(lambda: backends.hermes_summary(HERMES_VERIFIED)))
else:
    harness.absent("backends.hermes_command")
    harness.absent("backends.hermes_rate_limited")
    harness.absent("backends.hermes_summary")

try:
    route.parse("x-ai/grok-4.6 xhigh hermes")
    harness.emit("route.parse hermes", harness.measure(lambda: route.parse("x-ai/grok-4.6 xhigh hermes")))
except SystemExit:
    harness.absent("route.parse hermes")

if "hermes" in collect.BACKENDS and collect.backend_of(["python", "-m", "hermes_cli.main", "chat"]) == "hermes":
    harness.emit(
        "collect.backend_of hermes_cli",
        harness.measure(lambda: collect.backend_of(["python", "-m", "hermes_cli.main", "chat"])),
    )
else:
    harness.absent("collect.backend_of hermes_cli")
