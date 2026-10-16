# QA: 017 — architect patterns and design judge

Run these from the marestail-green root with the venv active. Expected results are exact unless noted.

1. Run `python3 tools/test-pattern-rulebooks.py`.
   Expected: exit 0, last line `patterns ok`. A dummy rule with an empty trigger, a `patterns/py.md`, a `method_missing` form, an unquoted distinctive ER-n phrase, a `WaitGroup.Go` mention without `1.25` and `go.mod`, or a rulebook missing its named rule set would fail this script. The names are a closed set in id order: TS-P9 is Factory, and the TS list adds Async coordination and Result directly after Function composition and `satisfies` after Const assertion; CS-P16 is Iterator; GO-P27 is WaitGroup, and the Go list adds Producer/consumer between Confinement and Timeouts and net/http handlers between Synctest and Handler struct; GO-4 has no ticker clause, because the Ticker pattern owns `Stop`; EX adds Notify many between Pool and Monitor, citing EX-12. Each dropped sheet row is absent: cs Mediator, CQRS, Specification, Aggregates and domain events; ts Event emitter/PubSub, Parse don't validate, Barrel files, Container/presentational, Server Components, the rare GoF row and Typed builder; rb Metaprogramming, Duck typing, Convention over configuration, Job, Callbacks, Instrumentation, Operations/interactors (and Draper only as `already a dependency`); ex shard-or-ETS, cast-over-`call`, GenStage, circuit-breaker, cluster-registry (Changesets, LiveView and PubSub and Presence are cited, not restated); er Release, distribution, hot code loading, ETS/DETS/Mnesia, share-nothing, typespecs; go singleflight, rate limiting, graceful shutdown, GOMAXPROCS, package layout, configuration, clean architecture.

2. Run:
   ```
   python3 - <<'PY'
   from marestail.pipeline import Judge, find, names, window
   from marestail.tui import theme
   design = find("design")
   assert design == Judge("design", None, bounce_to="architect", optional=True)
   assert (design.bounces, design.pause_after, design.writes, design.pinned_bounce, design.targets) == (0, False, (), False, ())
   assert names() == ["specifier", "critic", "coder", "cleaner", "architect", "design", "practices", "perf", "hardener", "qa"]
   assert [s.name for s in window("design", "design")] == ["design"]
   assert [s.name for s in window("architect", "practices")] == ["architect", "design", "practices"]
   assert [s.name for s in window("coder", "architect")] == ["coder", "cleaner", "architect"]
   assert names("hyper") == ["specifier", "critic", "coder", "architect", "blast", "hardener", "qa"]
   assert "design" not in names("hyper", True)
   assert names(None, True)[5] == "design"
   mono = theme.mono_theme()
   assert theme.role_attr(mono, "design") == mono.judge
   try:
       find("design", "hyper")
   except SystemExit as error:
       assert str(error) == "unknown role design; choose from specifier, critic, coder, architect, blast, hardener, qa"
   else:
       raise SystemExit("hyper accepted design")
   print("pipeline ok")
   PY
   ```
   Expected: exit 0, last line `pipeline ok`.

3. In a temp dir, call `run_step` on `find("design")` three times: no `guidance/patterns/*.md`; the same plus config `[design] enabled = false`; then with `guidance/patterns/ts.md` present and design left enabled. Mirror `tests/test_runner_flow.py` `test_run_step_judge` (capture stdout, stub `run_judge_loop`).
   Expected, in order: stdout `design: no pattern rulebooks; skipping` and success, loop not called; stdout `design disabled in marestail.toml; skipping` and not `no pattern rulebooks`; neither skip line, and the loop is called.

4. Stub `run_judge` to return `("BOUNCE", None, "1. TS-P9 src/order.ts:4: applied with no trigger\n")` once, then the same text again, and call `run_judge_loop` on `find("design")`.
   Expected: the first bounce calls `run_worker` with the architect step; the second prints `design repeated the same findings twice; the worker is not making progress, stopping for a human`, returns false, and does not call the architect again.

5. Grep `roles/design.md` for `src/order.ts:4`, `src/Walk.cs:10`, `src/pool.go:18`, `src/old.go:3`, `VERDICT: BOUNCE`, `VERDICT: PASS`, `## Patterns`, `## Pre-existing`, `guidance/patterns/`.
   Expected: all present. The order.ts fixture is one `return new Order(id)` cited as applied Factory TS-P9, verdict bounce; the role also teaches TS-P9's conditional form — a plain function when there is no invariant to protect, TS-15's named static factory only when TS-11 lets a class earn its place. The Walk.cs fixture is a hand-written `Current`/`MoveNext` cited as Iterator CS-P16, verdict bounce. The pool.go fixture with `go 1.25` and no `## Patterns` line bounces citing `GO-P27 src/pool.go:18`; the same trigger with `- GO-P27 src/pool.go:18: not applied because go.mod says go 1.22, which is below 1.25` passes, and with `go 1.22` and a silent `## Patterns` it still passes, because the rule's release condition is part of its trigger. A pass that only saw `src/old.go:3` includes `## Pre-existing` and `- GO-P27 src/old.go:3: Add(1) / go func / defer Done()`, and a pass with nothing pre-existing omits that heading.

6. Grep `roles/architect.md` for `Prefer a few deep modules over many shallow ones`, `guidance/patterns/*.md`, `## Proposals`, `## Patterns`, and `A factory or wrapper that hides nothing is the shallow module the role already rejects.`
   Expected: all present. The new text is one paragraph: apply a pattern only for a trigger in code this task touched, only in the rule's form, never add a dependency, and record applied and skipped triggers as `- <rule id> <file:line>: …`.

7. Run `bash tools/dryrun.sh`.
   Expected: exit 0 and a final line `remaining plan lines: 0`. `tools/dryrun.sh` creates `guidance/patterns/*.md` as well as `guidance/ts.md`. `tools/dryrun-plan.txt` has `judge design` on the line after `worker architect` and `judge BOUNCE` on the line after that. The stub's verdict for `judge design` is `VERDICT: PASS`.

8. Create a bare temp git repo at `/tmp/mt-017-bare` and run `marestail install /tmp/mt-017-bare`.
   Expected: `guidance/ts.md` and `guidance/patterns/ts.md` exist; `guidance/{cs,rb,ex,er,go}.md` and the other `guidance/patterns/*.md` do not.

9. Install a temp git repo that has `src/nested/a.erl` and nothing else from this task.
   Expected: `guidance/er.md` and `guidance/patterns/er.md` exist; `rb.md`, `ex.md`, `go.md`, and `cs.md` do not.

10. Install a temp git repo that has only `nested/Gemfile`, `nested/mix.exs`, and `nested/go.mod`.
    Expected: no `guidance/rb.md`, `ex.md`, or `go.md`, and no `guidance/patterns/{rb,ex,go}.md`.

11. Create a temp git repo at `/tmp/mt-017-langs` with root `Gemfile`, root `mix.exs`, root `go.mod` (`module x` / `go 1.22`), `src/nested/a.erl`, and `src/App.csproj`, then run `marestail install /tmp/mt-017-langs`.
    Expected: `guidance/{ts,cs,rb,ex,er,go}.md` and `guidance/patterns/{ts,cs,rb,ex,er,go}.md` all exist.

12. Prepend `# edited` to that tree's `guidance/patterns/ts.md` and `guidance/go.md`, then install again.
    Expected: both files still start with `# edited`.

13. On the repo from step 11, run `python3 -c "from pathlib import Path; from marestail import practices; root=Path('/tmp/mt-017-langs'); print(practices.files(root))"`.
    Expected: the printed paths are top-level `guidance/*.md` only. None contain `patterns`.

14. Run `python3 -c "from pathlib import Path; from marestail._hyper import hyper_files; print(sorted(hyper_files(Path('/tmp/mt-017-bare'))))"` on the bare repo, and the same on the step 11 repo.
    Expected: bare keys are `PERFORMANCE.md`, `guidance/ts.md`, `marestail.toml`, `tasks/README.md`. The marked repo adds only `guidance/cs.md`. No `guidance/patterns/` key and no `er.md`, `ex.md`, `rb.md`, or `go.md`.

15. Diff `templates/guidance/ts.md` and `cs.md` against `git show origin/main:templates/guidance/ts.md` and `cs.md`. Diff `er.md`, `ex.md`, and `rb.md` against `git show dfdb550:templates/guidance/<file>`.
    Expected: empty diffs. `rb.md` contains `RB-48` and does not contain `RB-49`. Do not use commit `4148a1e`.

16. Grep `templates/marestail.toml` for the commented `[design]` block.
    Expected: it sits beside `[practices]` and says the judge runs between architect and practices unless false, and skips repos with no `guidance/patterns/*.md`. The `[practices]` comment is unchanged.

17. In README.md, read the Pipeline row whose Step cell is `design`.
    Expected: it is directly between architect and practices; Kind `judge`, Gate `none`, hyper `—`; Does names the three bounces, the citation `<rule id> <file:line>`, and `design: no pattern rulebooks; skipping`. The sentence `Other languages get no shipped rulebook` is absent, while the Best practices section still has the `TS-1`… rulebook sentence, the `.csproj`/`CS-1` Arrange, Act, Assert sentence, and guidance files frozen (`guidance/**`). The next Best practices paragraph names `guidance/patterns/`, the architect, the design judge, not practices, and install of `er.md`, `ex.md`, `rb.md`, `go.md`, and `guidance/patterns/`.

18. Run `pytest tests/test_pipeline.py tests/test_runner_flow.py tests/test_install.py tests/test_install_hyper.py tests/test_tui_theme.py -q`.
    Expected: all pass. `tests/test_install_hyper.py` still expects the hyper key set from step 14.

19. Run each `tools/test-*.py` that already exits 0 (the `PASSING_SCRIPTS` list in `tests/test_green_repo.py`, which now includes `test-pattern-rulebooks.py`, plus the other scripts that passed before this task). Run `python3 tools/test-perf.py` too.
    Expected: each previously passing script still exits 0 with a last line containing `ok`. `tools/test-perf.py` exits 1 and its last line is `verdict-commit-files: '' != 'perf/bench_x.py'`. Its `pipeline_order` list includes `design`, so it does not fail earlier on the role list. `tools/test-run-hyper.py` passes because `[design] enabled = false` is in `TOML`, `HARD_PLAN` is unchanged, and `readme_documents` checks the updated blast row indices.

20. Diff `tasks/README.md` against `templates/tasks-README.md`.
    Expected: `templates/tasks-README.md` reads `specifier, critic, coder, cleaner, architect, design, practices, perf, hardener, QA`. `tasks/README.md` is that file with `design` removed from that one pipeline line, so it reads `specifier, critic, coder, cleaner, architect, practices, perf, hardener, QA`. No other line differs. `tasks/README.md` is not in `freeze.allow`.
