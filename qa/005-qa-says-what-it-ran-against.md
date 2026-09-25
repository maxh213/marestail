# QA procedure: ran-against ending for marestail run

1. `cd` to the marestail-green repo root with `.venv` active.
2. `python3 tools/test-qa-ran-against.py`
   Expected: exit `0`, last line contains `ok` (covers app / harness / nothing / missing-line retry / sentence-not-a-line / `--to hardener` / overnight exit 3).
3. In a temp repo (same shape as `tools/test-timeline.py`: stub via `MARESTAIL_CLAUDE`, features/qa seeded, no `[qa] cmd` or a no-op that passes), run:
   `marestail run tasks/t.md --from qa --to qa --auto --retries 2`
   with a stub handoff that includes a whole line exactly `ran-against: app`.
   Expected: exit `0`; last non-empty stdout line exactly `pipeline complete`.
4. Same window; handoff line exactly `ran-against: harness`.
   Expected: exit `3`; last non-empty line exactly `pipeline complete, NOT verified against the running app (qa ran against a harness)`.
5. Same window; handoff line exactly `ran-against: nothing`.
   Expected: exit `3`; last non-empty line exactly `pipeline complete, NOT verified against the running app (qa ran against nothing)`.
6. Same window; first handoff has no exact `ran-against:` line (e.g. only prose `We ran-against: app on a harness.`).
   Expected: QA is invoked a second time with that absence as feedback; run ends exit `3` with the `… (qa ran against nothing)` line.
7. `marestail run tasks/t.md --from hardener --to hardener --auto --retries 1` with stub `VERDICT: PASS` (judge gate green).
   Expected: exit `0`; last non-empty line `pipeline complete`; no `NOT verified`.
8. `STOP_AT=qa tools/overnight.sh tasks/t.md` in a temp repo whose QA stub writes `ran-against: harness`.
   Expected: overnight process exit `3`; `overnight-*.md` task section has `- exit 3 after` and contains `NOT verified`.
9. With that overnight log present, open `marestail watch --all` on the temp repo (or assert via the watch collect/render helpers the diagnostic uses).
   Expected: the bed's finished row text contains `NOT verified`, not only plain idle.
10. `grep -n 'ran-against\|exit 3\|NOT verified' roles/qa.md README.md | head -40`
    Expected: `roles/qa.md` has the one required sentence; README `## Pipeline` names `app` / `harness` / `nothing` and exit 3.
11. `python3 tools/test-timeline.py; python3 tools/test-watch.py; python3 tools/test-perf.py; echo exit=$?`
    Expected: timeline and watch exit 0; test-perf still exit 1 with `verdict-commit-files: '' != 'perf/bench_x.py'`.
