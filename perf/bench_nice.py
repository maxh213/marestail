#!/usr/bin/env python3
import importlib
import os

import harness

root = harness.use_tree()

from marestail.config import Config


def load_nice() -> object | None:
    try:
        return importlib.import_module("marestail.nice")
    except ImportError:
        return None


def bound(name: str) -> object | None:
    if nice is None:
        return None
    return getattr(nice, name, None)


def record(target: str, found: object | None, *args: object) -> None:
    if not callable(found):
        harness.absent(target)
        return
    harness.emit(target, harness.measure(lambda: found(*args)))


def record_refuse() -> None:
    refuse = bound("_refuse")
    if not callable(refuse):
        harness.absent("nice._refuse")
        return

    def call() -> None:
        try:
            refuse(-1)
        except SystemExit:
            return

    harness.emit("nice._refuse", harness.measure(call))


nice = load_nice()
if nice is not None:
    os.environ.pop(getattr(nice, "ENV", "MARESTAIL_NICE"), None)

loaded = Config(root=root, raw={})
off = Config(root=root, raw={"run": {"nice": 0}})

record("nice.parse", bound("parse"), 19)
record("nice.level", bound("level"), loaded)
record("nice._setting", bound("_setting"), loaded)
record("nice._configured", bound("_configured"), loaded)
record("nice._as_int", bound("_as_int"), 19)
record("nice._text_int", bound("_text_int"), "19")
record("nice._accepted", bound("_accepted"), 19)
record_refuse()
record("nice._score_path", bound("_score_path"))
record("nice.apply off", bound("apply"), off)
record("nice._drop_priority", bound("_drop_priority"), 19)
record("nice._idle_io", bound("_idle_io"))
record("nice._prefer_oom", bound("_prefer_oom"))
record("nice.apply", bound("apply"), loaded)
