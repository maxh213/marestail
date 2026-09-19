#!/usr/bin/env python3
import harness

root = harness.use_tree()

from marestail import depth

config = harness.make_config(root)


def analyse() -> None:
    depth.report(depth.analyse(config))


harness.emit("marestail depth", harness.measure(analyse))
