#!/usr/bin/env python3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail.gates.ex_mutation import guarded


class Ctx:
    def elixir(self, key, default=None):
        return {"muex_run_limit": 2}.get(key, default)


def expect(name, got, wanted):
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def fake_run(seconds):
    return f'bash -c \'n="mix test --max"; exec -a "$n-failures 1 fake" sleep {seconds}\''


def hung_run_is_killed():
    started = time.time()
    result = subprocess.run(guarded(Ctx(), ["bash", "-c", f"{fake_run(60)}; echo carried-on"], poll=1), capture_output=True, text=True, timeout=30)
    expect("hung-killed-quickly", time.time() - started < 10, True)
    expect("muex-carries-on", "carried-on" in result.stdout, True)
    expect("says-why", "killed a muex test run" in result.stderr, True)


def quick_run_is_left_alone():
    result = subprocess.run(guarded(Ctx(), ["bash", "-c", f"{fake_run(1)}; echo done"], poll=1), capture_output=True, text=True, timeout=30)
    expect("quick-untouched", "killed" in result.stderr, False)
    expect("quick-finishes", "done" in result.stdout, True)


def other_trees_are_left_alone():
    outsider = subprocess.Popen(["bash", "-c", "n='mix test --max'; exec -a \"$n-failures 1 other-bed\" sleep 30"])
    try:
        time.sleep(3)
        subprocess.run(guarded(Ctx(), ["bash", "-c", "sleep 4"], poll=1), capture_output=True, text=True, timeout=30)
        expect("outsider-alive", outsider.poll(), None)
    finally:
        outsider.kill()


if __name__ == "__main__":
    hung_run_is_killed()
    quick_run_is_left_alone()
    other_trees_are_left_alone()
    print("muex watchdog ok")
