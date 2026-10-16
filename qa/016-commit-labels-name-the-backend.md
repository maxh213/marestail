# QA procedure: commit labels name the backend

Temp repos below use the same shape as `tools/test-timeline.py`: `git init -b main` with a user set, `marestail.toml` with `[git] base = "main"`, `.gitignore` containing `.marestail/`, `tasks/t.md`, and features/qa plus passing tests seeded as its `seed_features` does. `STUB_PLAN` is a file whose first line is `code`; `tools/stub-claude` from this repo serves as the agent binary for both claude and cursor (their output parsing is identical). Run every `marestail run` with `MARESTAIL_AGENT` unset, and keep the plan file, the dandelion stub and any logs outside the repo directory — the runner's verify fails on uncommitted files.

Pasteable dandelion stub (step 7) — write as `$SCRATCH/dandelion`, chmod +x:

```sh
#!/bin/sh
printf 'kimi-k3-max cursor\n'
```

1. `cd` to the marestail-green repo root with `.venv` active.
2. `python3 tools/test-agent-backends.py`
   Expected: exit `0`, last line `agent backends ok`. `grep -n "label-" tools/test-agent-backends.py` shows the updated expectations: `label-model-only` → `claude/mymodel`, `label-with-effort` → `claude/mymodel high`, `label-grok` → `grok/mymodel xhigh`, `label-kimi` → `kimi/mymodel`, `label-kimi-effort` → `kimi/mymodel high`, `label-junie` → `junie/mymodel`, `label-hermes` → `hermes/mymodel`, and the new pin `label-cursor-auto` → `cursor/auto high`; `label-no-model` → `claude`, `label-kilo-default` → `kilo/stepfun/step-3.7-flash:free high` and `label-kilo-plain` → `kilo/other` are unchanged.
3. `MARESTAIL_DANDELION_SRC=/nonexistent python3 tools/test-route.py`
   Expected: exit `0`, last line `route ok`; its pinned labels `grok-4.6 xhigh` and `claude-opus-5 high` are unchanged (both name their backend). The override only skips the environment-dependent dandelion-source check, which reads a file outside this repo and exits 1 in this environment at HEAD already.
4. Label probe for every table row and no-model path:

```bash
env -u MARESTAIL_AGENT -u MARESTAIL_GROK_EFFORT -u MARESTAIL_KILO_VARIANT python3 - <<'PY'
from pathlib import Path
from marestail.config import Config
from marestail.runner import Run, agent_label
def st(agent=None, model=None, effort=None, route=None):
    return Run(config=Config(root=Path("/tmp"), raw={}), task=Path("/tmp/t.md"), model=model, retries=0, agent=agent, effort=effort, route=route)
rows = [
    (st("cursor", "auto", "high"), "cursor/auto high"),
    (st("cursor", "kimi-k3-max"), "cursor/kimi-k3-max"),
    (st("agy", "gemini-3.8-flash-high", "high"), "agy/gemini-3.8-flash-high high"),
    (st("junie", "gemini-3.8-flash", "high"), "junie/gemini-3.8-flash high"),
    (st("claude", "claude-opus-5", "high"), "claude-opus-5 high"),
    (st("grok", "grok-4.6", "xhigh"), "grok-4.6 xhigh"),
    (st("kimi", "kimi-code/kimi-for-coding-highspeed"), "kimi-code/kimi-for-coding-highspeed"),
    (st("hermes", "x-ai/grok-4.6", "xhigh"), "x-ai/grok-4.6 xhigh"),
    (st(route="dandelion/route"), "dandelion/route"),
    (st("claude"), "claude"),
    (st("kilo"), "kilo/stepfun/step-3.7-flash:free high"),
    (st("kilo", "kilo/other"), "kilo/other"),
]
bad = [(agent_label(s), want) for s, want in rows if agent_label(s) != want]
print("labels ok" if not bad else f"WRONG: {bad!r}")
PY
```

   Expected: prints exactly `labels ok`.
5. End-to-end cursor on auto: in a fresh temp repo, note `S=$(git rev-parse HEAD)`, then `STUB_PLAN=<plan file> MARESTAIL_CURSOR=<this repo>/tools/stub-claude marestail run tasks/t.md --from coder --to coder --auto --agent cursor --model auto --effort high`.
   Expected: exit `0`; `git log --format=%s $S..HEAD` shows every subject starting with `[cursor/auto high] `; no subject starts with `[auto high] ` or `[cursor/cursor/`.
6. End-to-end claude, byte-identical: fresh temp repo, note `S=$(git rev-parse HEAD)`, then `STUB_PLAN=<plan file> MARESTAIL_CLAUDE=<this repo>/tools/stub-claude marestail run tasks/t.md --from coder --to coder --auto --agent claude --model claude-opus-5 --effort high`.
   Expected: exit `0`; every subject in `git log --format=%s $S..HEAD` starts with `[claude-opus-5 high] ` (exactly as before this task; no `claude/` prefix).
7. End-to-end routed cursor on K3: fresh temp repo, note `S=$(git rev-parse HEAD)`, then `MARESTAIL_DANDELION=$SCRATCH/dandelion STUB_PLAN=<plan file> MARESTAIL_CURSOR=<this repo>/tools/stub-claude marestail run tasks/t.md --from coder --to coder --auto --model dandelion/route` (no `--agent`/`--effort`; dandelion picks them).
   Expected: exit `0`; every subject in `git log --format=%s $S..HEAD` starts with `[cursor/kimi-k3-max] `; `.marestail/runs/t/01-coder.prompt.md` contains `[cursor/kimi-k3-max] ` and no `[dandelion/route] `.
8. `python -m pytest tests -q`
   Expected: all pass; `grep -rn "cursor/auto" tests/ | head` finds the new label pins (one test per row of the task table, plus the branch and no-model rows). Existing expectations that asserted a bare model now carry the prefix: tests/test_runner_backends.py `test_agent_label` rows `claude/opus high`, `kilo/other`, `hermes/mymodel`, `hermes/mymodel xhigh`; tests/test_runner_flow.py `test_invoke_routes_each_session` routes to `Commit as [claude/opus high] here` with `state.labels == {"claude/opus high"}`, and the `"m e"` label argument is `"claude/m e"` in `test_run_worker_succeeds_after_feedback`, `test_judge_attempt_records_bounce` and `test_author_phase_stops_when_benches_settle`; tests/test_runner_git.py revert subjects start `[claude/m e] `; tests/test_timeline.py `test_remember_agent` records model `claude/m`; tests/test_runner_visual.py shows `cursor/gpt-9 high` in `test_an_out_of_usage_judge_model_falls_back_at_once` and `test_commit_verdict_stamps_the_judge_and_marks_a_blind_pass`.
9. `grep -n "cursor/auto\|backend/<model>" README.md`
   Expected: the paragraph that describes the commit stamp states the `<backend>/<model>` rule with the `[cursor/auto high]` example, and still says a model-less run stamps the bare backend name.
10. `python3 tools/test-perf.py; echo exit=$?`
    Expected: still exit `1` with last line `verdict-commit-files: '' != 'perf/bench_x.py'`; every other `tools/test-*.py` keeps its current contract.
11. `python3 tools/test-visual-judge.py`
    Expected: exit `0`, last line `visual judge ok`. `grep -n "cursor/gpt-9" tools/test-visual-judge.py` shows the updated pins: hardener-stamp `[cursor/gpt-9] hardener verdict: PASS`, the fallback-timeline model `cursor/gpt-9`, and fallback-stamp/route-stamp `[cursor/gpt-9] visual verdict: PASS`; its `[claude-fable-5-1]` and `[kilo/x]` pins are unchanged.
