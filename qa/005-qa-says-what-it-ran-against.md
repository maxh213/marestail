# QA procedure: ran-against ending for marestail run

1. `cd` to the marestail-green repo root with `.venv` active.
2. `python3 tools/test-qa-ran-against.py`
   Expected: exit `0`, last line contains `ok` (covers app / harness / nothing / missing→still-missing / missing→retry-app / sentence-not-a-line / `--to hardener` / overnight exit 3 / watch dead-bed row).
3. In a temp repo (same shape as `tools/test-timeline.py`: stub via `MARESTAIL_CLAUDE`, features/qa seeded, no `[qa] cmd` or a no-op that passes), run:
   `marestail run tasks/t.md --from qa --to qa --auto --retries 2`
   with a stub handoff that includes a whole line exactly `ran-against: app`.
   Expected: exit `0`; last non-empty stdout line exactly `pipeline complete`.
4. Same window; handoff line exactly `ran-against: harness`.
   Expected: exit `3`; last non-empty line exactly `pipeline complete, NOT verified against the running app (qa ran against a harness)`.
5. Same window; handoff line exactly `ran-against: nothing`.
   Expected: exit `3`; last non-empty line exactly `pipeline complete, NOT verified against the running app (qa ran against nothing)`.
6. Same window; first handoff has no exact `ran-against:` line (e.g. only prose `We ran-against: app on a harness.`); second accepted handoff also has no exact line.
   Expected: QA invoked twice (absence as finding on the retry); exit `3` with the `… (qa ran against nothing)` line.
7. Same as 6, but the stub's second accepted handoff is a whole line exactly `ran-against: app`.
   Expected: exit `0`; last non-empty line exactly `pipeline complete`.
8. `marestail run tasks/t.md --from hardener --to hardener --auto --retries 1` with stub `VERDICT: PASS` (judge gate green).
   Expected: exit `0`; last non-empty line `pipeline complete`; no `NOT verified`.
9. `START_FROM=qa STOP_AT=qa tools/overnight.sh tasks/t.md` in a temp repo whose QA stub writes a whole line `ran-against: harness`.
   Expected: overnight process exit `3`; `overnight-*.md` task section has `- exit 3 after` and contains `NOT verified`.
10. With that overnight log's last non-empty line the harness `NOT verified` line, and the bed dead: call `collect_repo` on the bed (as `marestail watch --all` does).
    Expected: the string used for that bed's idle/finished row (dead-bed / `draw_idle_row` paint) includes `NOT verified`, not only `○ idle`.
11. `grep -n 'ran-against\|exit 3\|NOT verified' roles/qa.md README.md | head -40`
    Expected: `roles/qa.md` has the one required sentence; README `## Pipeline` names `app` / `harness` / `nothing` and exit 3.
12. `python3 tools/test-timeline.py; python3 tools/test-watch.py; python3 tools/test-perf.py; echo exit=$?`
    Expected: timeline and watch exit 0; test-perf still exit 1 with `verdict-commit-files: '' != 'perf/bench_x.py'`.
