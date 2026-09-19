#!/usr/bin/env python3
import harness

root = harness.use_tree()

from marestail.context import build

config = harness.make_config(root)


def build_all() -> None:
    build(config, False)


def build_changed() -> None:
    build(config, True, {"marestail"})


harness.emit("context.build all", harness.measure(build_all))
harness.emit("context.build changed", harness.measure(build_changed))
