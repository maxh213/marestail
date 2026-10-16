#!/usr/bin/env python3
import harness

root = harness.use_tree()

from marestail.cli import main
from marestail.context import build
from marestail.gates import comments, docs

config = harness.make_config(root)
ctx = build(config, False)


def gate_docs() -> None:
    main(["gate", "--tier", "fast", "--only", "docs"])


if harness.has_toml(root):
    harness.emit("marestail gate --only docs", harness.measure(gate_docs))
else:
    harness.absent("marestail gate --only docs")

harness.emit("docs.run_gate", harness.measure(lambda: docs.run_gate(ctx)))
harness.emit("comments.python_findings", harness.measure(lambda: comments.python_findings(ctx)))
