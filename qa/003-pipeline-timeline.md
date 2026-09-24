# QA procedure: Durable per-step pipeline timeline

1. `cd` to the marestail-green repo root with `.venv` active.
2. `python3 tools/test-timeline.py`
   Expected: exit `0`, last line contains `ok`.
3. `python3 tools/test-watch.py`
   Expected: exit `0`, last line contains `ok` (green-shaped: overnight logs / discovery only; no pipeline.log preference).
4. In a temp repo (same layout as `tools/test-audit.py` / `tools/dryrun.sh`: stub agent via `MARESTAIL_CLAUDE`, git user set, `.marestail/` gitignored), run:
   `marestail run tasks/t.md --from coder --to coder --auto --retries 1`
   with a stub that commits `src.py` and writes handoff first paragraph `coded the adder`.
   Expected: stdout has `== coder (` and `finished in`; `.marestail/runs/t/timeline.json` has `task` `"t"` and one step with `role` `"coder"`, `attempt` `1`, non-empty `commits`/`files`/`done`, `agent.minutes >= 0`; `.marestail/runs/t/timeline.md` has one `##` section for that step id.
5. In `tools/test-timeline.py`, double `Run.gates` so hardener's pre-loop `gate_for` returns `[Result(gate="sonar", ok=False, summary="fail", seconds=41.0)]`, then run:
   `marestail run tasks/t.md --from hardener --to hardener --auto --retries 1`
   with a stub that writes `VERDICT: PASS`.
   Expected: `gate_for` / `state.gates` invoked once before attempts (not per attempt); every reused attempt step has `gate` containing `{"name": "sonar", "seconds": 41.0, "ok": false}`; `"verdict": "BOUNCE"` (not PASS); `agent` present.
6. With a stub that writes `VERDICT: AUTHOR` on the first perf attempt then `VERDICT: PASS` on the next, run `marestail run tasks/t.md --from perf --to perf --auto --retries 2` (gate ok so AUTHOR is not overwritten).
   Expected: first timeline step has `"verdict": "AUTHOR"` and is appended when that session ends; a later step follows even though `judged` returned None for AUTHOR.
7. With `MARESTAIL_LIMIT_WAIT_SECONDS=0`, run a worker whose first session is rate-limited then succeeds.
   Expected: step `waits` includes `{"reason": "rate-limit", "seconds": 0}`; stdout still has `rate limited; waiting`.
8. With `MARESTAIL_LIMIT_WAIT_SECONDS=0`, `--model dandelion/route`, stub dandelion plan `1 none` then `0 claude-opus-5 high claude`, stub claude succeeds.
   Expected: step `waits` includes `{"reason": "dandelion-unrouted", "seconds": 0}`; stdout still has `dandelion/route:`.
9. Cause two attempts of the same role.
   Expected: `timeline.json` has two steps; `timeline.md` has two `##` sections; ids match.
10. From a temp repo that already has `.marestail/runs/t/timeline.md`, run `tools/overnight.sh tasks/t.md` (stub agent, `STOP_AT` short).
    Expected: `overnight-*.md` still has exit/HEAD/grep lines under `### tasks/t.md`, and also embeds that timeline (heading `timeline.md` or a step id).
11. `grep -E 'timeline\.(md|json)' README.md` near the Overnight `.marestail/runs/overnight-<stamp>.md` mention.
    Expected: both names present; no new false claim of `.marestail/runs/<task>/pipeline.log`.
12. Confirm freeze lists the timeline artefacts (e.g. `grep -E 'timeline\.(md|json)' marestail/freeze.py`).
    Expected: a worker cannot keep a commit that only clobbers those files.
