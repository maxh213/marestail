# QA: 017 — architect patterns and design judge

1. From the marestail-green root with the venv active, run `python3 tools/test-pattern-rulebooks.py`.
   Expected: exit 0; last line contains `ok`.

2. Run `python3 -c "from marestail.pipeline import PIPELINE, window, steps, HYPER; names=[s.name for s in PIPELINE]; i=names.index('architect'); print(names[i:i+3]); print([s.name for s in window('architect','practices')]); print('design' in [s.name for s in steps(HYPER)])"`.
   Expected: first line is `['architect', 'design', 'practices']`; second lists architect, design, practices; third is `False`.

3. Grep `tools/dryrun-plan.txt` for the block around architect.
   Expected: a line `judge design` appears immediately after `worker architect`.

4. In an empty temp git repo, run `marestail install /tmp/mt-017-bare` (create the dir first).
   Expected: `guidance/ts.md` and `guidance/patterns/ts.md` exist; no `guidance/go.md`, `er.md`, `ex.md`, `rb.md`, or `cs.md`; no other `guidance/patterns/*` besides `ts.md`.

5. In a fresh temp git repo at `/tmp/mt-017-langs` add a root `go.mod` (`module x` / `go 1.22`), a `Gemfile`, a `mix.exs`, an `a.erl`, and `src/App.csproj`, then run `marestail install /tmp/mt-017-langs`.
   Expected: `guidance/{ts,cs,rb,ex,er,go}.md` and `guidance/patterns/{ts,cs,rb,ex,er,go}.md` all exist.

6. Edit `/tmp/mt-017-langs/guidance/patterns/ts.md` to start with `# edited`, run `marestail install /tmp/mt-017-langs` again.
   Expected: the file still starts with `# edited`.

7. On the sample from step 5 (which has both `guidance/*.md` and `guidance/patterns/*.md`), run `python3 -c "from pathlib import Path; from marestail import practices; print(any('patterns' in p.parts for p in practices.files(Path('/tmp/mt-017-langs'))))"` (use that sample's path).
   Expected: prints `False`.

8. Diff `templates/guidance/er.md` and `ex.md` against `git show dfdb550:templates/guidance/er.md` and `ex.md`; `rb.md` against `git show 4148a1e:templates/guidance/rb.md`; and confirm `ts.md` / `cs.md` match HEAD before this task (or `git show HEAD:templates/guidance/ts.md` if this task has not rewritten them).
   Expected: no wording drift on the ported or pre-existing rulebooks.

9. Read `roles/architect.md` and `roles/design.md`.
   Expected: architect tells the role to read `guidance/patterns/*.md`, apply only on trigger, never add a dependency (propose under `## Proposals`), and list applied / deliberately-skipped triggers under `## Patterns`. design tells the judge to bounce only for the three cases in the feature, each as `<rule id> <file:line>`, and to list pre-existing under `## Pre-existing` on a pass.

10. Open README.md Pipeline table and Best practices.
    Expected: a `design` row between architect and practices; one short Best-practices paragraph on `guidance/patterns/`, who reads them, and what design bounces for.

11. Run the other `tools/test-*.py` scripts that already exit 0 today (same set as 016).
    Expected: each exits 0 with last line containing `ok`. `tools/test-perf.py` still exits 1 with last line `verdict-commit-files: '' != 'perf/bench_x.py'`.
