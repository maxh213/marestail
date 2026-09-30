#!/usr/bin/env python3
from pathlib import Path

import harness

root = harness.use_tree()

from marestail import runner
from marestail.config import Config

config = Config(root=root, raw={})
task = Path("/work/task.md")
cursor = runner.Run(config=config, task=task, model="auto", retries=1, agent="cursor", effort="high")
hermes = runner.Run(config=config, task=task, model="x-ai/grok-4.6", retries=1, agent="hermes", effort="xhigh")

harness.emit("runner.model_name bare", harness.measure(lambda: runner.model_name(cursor)))
harness.emit("runner.model_name slash", harness.measure(lambda: runner.model_name(hermes)))
harness.emit("runner.agent_label", harness.measure(lambda: runner.agent_label(cursor)))

model_label = getattr(runner, "model_label", None)
if model_label is None:
    harness.absent("runner.model_label")
else:
    harness.emit("runner.model_label", harness.measure(lambda: model_label("cursor", "auto")))
