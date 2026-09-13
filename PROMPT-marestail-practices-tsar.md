# marestail practices tsar

## Overview
Add a `practices` judge to the marestail pipeline: a language best-practices tsar. For each task it reviews the code the task touched against per-language rulebooks living in the target repo's `guidance/` folder (`guidance/ts.md`, `guidance/ruby.md`, …). It bounces to the coder only when it can cite a numbered rule from a guidance file that the task's changes violate.

The step is configurable per repo with `[practices] enabled`, and it only runs when the repo actually has guidance files: a repo with no `guidance/*.md` is skipped, so existing repos are unaffected. marestail ships a curated TypeScript rulebook (`templates/guidance/ts.md`, distilled from the two cheat sheets listed below) that `marestail install` copies into repos.

## Context & Constraints

Repo: `maxh213/marestail`. You are in the worktree `/home/max/workspace/marestail-marestail-practices-tsar` on branch `feat/marestail-practices-tsar`, based on `main` (9d364df), which includes the perf judge and the diff-scoped gates.

Code style, which the whole repo follows:
- stdlib-only Python 3.12+, no new dependencies.
- No comments or docstrings; names carry the meaning.
- Small functions, dataclasses.
- Match the surrounding files.

Files you will touch, and what they do today:
- `marestail/pipeline.py`: `Worker` and `Judge` dataclasses, plus `PIPELINE = specifier, critic, coder, cleaner, architect, perf, hardener, qa`. `Judge` fields: `name, tier, bounce_to, bounces, pause_after, writes, pinned_bounce, optional`.
- `marestail/runner.py`:
  - `run_step` runs a worker, or for a judge first applies the optional skip (`if step.optional and state.config.get(step.name, "enabled", True) is False: print(f"{step.name} disabled in marestail.toml; skipping")`), then `run_judge_loop`.
  - `run_judge_loop` bounces to a worker with the judge's report as feedback, re-runs the judge, and stops when the same numbered findings repeat twice.
  - `run_judge` / `judge_attempt` invoke the agent, `discard_edits` (a judge with an empty `writes` gets every non-`.marestail/` edit wiped), `parse_verdict` (`VERDICT: PASS|BOUNCE [target]`; with `pinned_bounce` a named target is dropped), and `record_commit` with subject `<name> verdict: <verdict>`.
  - `measuring()` is the existing precedent for special-casing one judge by name.
- `marestail/prompts.py`: `judge_prompt` assembles role text (`roles/<name>.md`), task, spec listing, handoffs so far, and verdict instructions. Judges get no diff from the prompt; role files tell the agent to run `git diff` itself.
- `marestail/config.py`: `Config(root, raw)`, `section(name)`, `get(section, key, default)`.
- `marestail/freeze.py`: `SPEC = ["features/**", "qa/**", "tasks/**", "perf/**", "PERFORMANCE.md"]`; workers may not commit changes to frozen paths.
- `marestail/install.py`: `install()` copies templates into a target repo with `copy_if_missing` (`marestail.toml`, `sonar-project.properties`, `tasks/README.md`, `PERFORMANCE.md`); `GITIGNORE_LINES` is always appended, `GITIGNORE_GENERATED_LINES` only with `--gitignore-generated`.
- `marestail/tui/theme.py`: `JUDGE_ROLES = frozenset({"critic", "perf", "hardener"})`.
- `roles/hardener.md`: the model for a machinery-free judge — 11 lines, telling the agent to review `git diff <[git] base>...HEAD -- . ':!.marestail'`.
- `roles/perf.md`: the model for bounce-vs-flag rule wording.
- Tests: there is no pytest suite.
  - `tools/test-perf.py` and `tools/test-agent-backends.py` are plain scripts with an `expect(name, got, want)` helper. `tools/test-perf.py` has fixture builders (`new_repo`, `state`, `agent_stub`, `replaced_invoke`) worth mirroring.
  - `tools/stub-claude` simulates agents from plan lines; its generic `judge <verdict>` action writes the verdict file for any judge.
  - `tools/dryrun.sh` builds a throwaway repo at `/tmp/marestail-dryrun/repo` (its `marestail.toml` is just `[git]` with `base = "main"`), drives a full pipeline with the stub following `tools/dryrun-plan.txt`, and prints `remaining plan lines: N` (0 = pass).
- `README.md`: the Pipeline table; its `## Performance` section shows the shape of a step section. `templates/tasks-README.md` names the pipeline order.

The TypeScript rulebook sources (read-only; never modify them and never copy them into the repo verbatim):
- `/home/max/Downloads/oop-in-typescript-cheat-sheet.md`
- `/home/max/Downloads/react-ts-nextjs-cheat-sheet.md` (an identical copy exists as `react-ts-nextjs-cheat-sheet (1).md`; ignore it)

### Decisions already made (do not re-litigate)

#### The step
1. **Name and placement.** The step is named `practices`, and the role file is `roles/practices.md`. It is `Judge("practices", None, bounce_to="coder", pinned_bounce=True, optional=True)`, placed after `architect` and before `perf`, so style fixes land before perf certifies its measurements and the hardener still reviews last. `writes` stays empty — the judge edits nothing.
2. **On/off per repo.** `optional=True` reuses the existing skip: `[practices] enabled = false` prints `practices disabled in marestail.toml; skipping` and makes no commit. Like perf, the default is on.
3. **Runs only with guidance.** The step also skips when the target repo has no guidance files. `run_step` gains, right after the optional skip, a name-based check (the `measuring()` precedent):

   ```python
   if step.name == "practices" and not practices.files(state.config.root):
       print("practices: no guidance files; skipping")
       return True
   ```

   Guidance is deliberately not tied to the `marestail.toml` language sections: the template toml ships every section, and the dry-run repo has none. The file stem names the language (`ts.md` is TypeScript); any `*.md` directly under the repo-root `guidance/` is a rulebook.
4. **What the judge reviews.** The diff for this task against the base branch — `git diff <[git] base>...HEAD -- . ':!.marestail'`, the same scope the hardener uses — plus the handoffs to know what the task touched. It applies each rulebook only to files in that rulebook's language, and only flags lines the task added or changed. It needs no worktrees, no start commit, and no new prompt plumbing.
5. **Bounce vs pass (the rule `roles/practices.md` states).**
   - **BOUNCE** to the coder only for a clear violation of a numbered rule in a `guidance/*.md` file, in code this task touched. Each numbered finding names the rule id, file:line, the rule, and the fix. Never bounce on taste, on a rule that is not in a guidance file, or on pre-existing code. `pinned_bounce` already forces any named target to coder.
   - **PASS** otherwise. Under `## Reviewed`, list the guidance files and languages applied. Under `## Pre-existing`, list violations in code the task did not touch (informational only: rule id and file:line; omit the section when empty).
   - React rules apply only to React code, and Next.js rules only when the repo is a Next.js app; a plain TypeScript backend is judged on the language and OOP rules alone.
6. **`guidance/**` is frozen and committed.** Add `guidance/**` to `freeze.SPEC`, so no agent can weaken a rulebook during a run to pass its own review. Guidance files are maintainer policy and are committed to the target repo: they go into neither `GITIGNORE_LINES` nor `GITIGNORE_GENERATED_LINES`.
7. **Install ships the TypeScript rulebook.** `install()` copies `templates/guidance/ts.md` to `guidance/ts.md` with `copy_if_missing`, unconditionally (the `PERFORMANCE.md` precedent). Existing repos never re-run install, so for them the decision-3 skip keeps the step off until a maintainer adds a rulebook. Other languages get no shipped rulebook; repos write their own.
8. **The TypeScript rulebook.** `templates/guidance/ts.md` is a distillation of the two cheat sheets named in *Context*, written for a reviewing agent, not a learner:
   - Header: one or two lines stating that the `practices` judge applies these rules to `*.ts`/`*.tsx` changes, with the versions line from the sheets (TypeScript 5.8+, React 19.2, Next.js 16, Node 24).
   - Sections: `## Language & types`, `## Design & OOP`, `## React`, `## Next.js App Router` — each a rule list in one continuing sequence, `TS-1`, `TS-2`, … Every rule is one statement an agent can check a diff against, with a `❌`/`✅` pair only where the contrast is not obvious from the statement.
   - Cover, at minimum: every item from both sheets' quick-review checklists (OOP sheet §10, React sheet §9); the prose-only rules — `interface` vs `type` convention, `implements` to catch drift, named static factories instead of constructor overloads, parameter properties vs `erasableSyntaxOnly`, `noImplicitOverride` and explicit `override`, single responsibility at module level, constructor-parameter and factory-closure DI, small interfaces, exhaustive `assertNever` switches, generics over `any`, the `Error` hierarchy with `instanceof` catch-site filtering, `Result` for expected failures vs throwing, `satisfies` over `as`, schema validation at boundaries, the recommended tsconfig flags, annotate boundaries and infer the middle, class instances cannot cross the server→client boundary, `useOptimistic`, `next/form`, `after()` for post-mutation work; the outdated-advice rows of the React sheet's §0 table folded into the rules they belong to (`forwardRef`, `.Provider`, sync `cookies()`, `enum`, `useFormState`, `revalidateTag` cache profile, `middleware.ts` → `proxy.ts`, defensive memoization); and condensed versions of the OOP sheet's §0.5 layer map and §9 decision table.
   - Leave out: the ecosystem-defaults table, the sources footers, and anything that needs a human's product knowledge.
   - At most 250 lines. The judge reads this file on every run; keep it dense.

### Suggested module layout
- One new module, `marestail/practices.py` — no package:
  - `files(root: Path) -> list[Path]`: the sorted `*.md` files directly under `<root>/guidance/` (no recursion), `[]` when the folder is missing.
  - Nothing else. The runner calls `practices.files`; the role file does the rest.

## Phases

### Phase 1: The practices step exists and is skippable
- Insert the `Judge` into `PIPELINE` between `architect` and `perf` (decision 1), and add `practices` to `JUDGE_ROLES`.
- Create `marestail/practices.py` with `files()`, and add the decision-3 skip to `run_step`.
- Add `guidance/**` to `freeze.SPEC` (decision 6).
- Write `roles/practices.md`, covering: its identity (it judges code against the repo's `guidance/*.md` rulebooks; it never edits anything); reading every rulebook first and noting the language each covers; the decision-4 diff command and scope; the decision-5 bounce vs pass rule and the `## Reviewed` / `## Pre-existing` sections. Keep it under 20 lines.
- Create `tools/test-practices.py`, in the style of `tools/test-perf.py` (its fixtures are the model), covering:
  - the exact pipeline order and the practices judge's fields;
  - `files()` over a temp dir: two rulebooks found sorted, a nested `guidance/sub/x.md` ignored, a non-markdown file ignored, a missing folder giving `[]`;
  - the no-guidance skip: the message and no commit, in a temporary git repo;
  - the `[practices] enabled = false` skip;
  - `guidance/**` in `freeze.SPEC`;
  - a stubbed PASS producing a commit with subject `practices verdict: PASS`;
  - `roles/practices.md` exists and mentions `guidance`.

  It ends with `print("practices ok")`.
- Keep the dry run green:
  - `tools/dryrun.sh`: add a committed `guidance/ts.md` to the throwaway repo's init commit — a one-rule placeholder written inline with `printf`, not the real template;
  - `tools/dryrun-plan.txt`: insert the three lines `judge BOUNCE`, `code good`, `judge PASS` between `worker architect` and `perf BOUNCE` — the generic stub `judge` action already does everything a verdict-only judge needs.

Deliverables: `marestail.pipeline.names()` includes `practices` between `architect` and `perf`, `roles/practices.md` exists, both skips work, and the dry run exercises a practices bounce and pass.
Verify: `python3 tools/test-practices.py && python3 tools/test-agent-backends.py && tools/dryrun.sh`

### Phase 2: The TypeScript rulebook and install
- Write `templates/guidance/ts.md` per decision 8, distilling the two cheat sheets.
- `install()`: copy it to `guidance/ts.md` with `copy_if_missing` (decision 7).
- Tests in `tools/test-practices.py`:
  - `install()` into a temporary dir (with `GROK_HOME` pointed at a temporary dir) creates `guidance/ts.md`, and does not overwrite an existing one;
  - neither `.gitignore` mode lists `guidance/`;
  - the template defines at least 40 `TS-\d+` rule ids, is at most 250 lines, has the four decision-8 section headings, and contains the strings `useEffectEvent`, `erasableSyntaxOnly`, `"use cache"`, `satisfies`, `assertNever`, `proxy.ts`.

Deliverables: `marestail install` gives a repo the curated TypeScript rulebook.
Verify: `python3 tools/test-practices.py && python3 tools/test-agent-backends.py && tools/dryrun.sh`

### Phase 3: Gates and documentation
- Audit the gates for walkers that would read a root-level `guidance/` (at minimum check `marestail/gates/comments.py` and `marestail/gates/docs.py`; most gates read configured source folders only). Where one walks every file from a root, exclude the root-level `guidance/` the same root-relative way `perf/` was excluded, and add a test. Record what you changed — or that nothing needed changing — in PROGRESS-marestail-practices-tsar.md.
- `README.md`: add a `practices` row to the Pipeline table (kind judge, gate none) between architect and perf, and a `## Best practices` section explaining: the rulebooks in `guidance/`, that the step skips repos without them, `[practices] enabled = false`, the shipped TypeScript rulebook and how install places it, the bounce rule (a cited rule id in task-touched code), and the `## Pre-existing` pass section.
- `templates/marestail.toml`: add a commented `# [practices]` block in the style of the `# [perf]` block: `# enabled = true  # the practices judge runs between architect and perf unless false; skips repos with no guidance/*.md`.
- Update the pipeline order sentence in `templates/tasks-README.md`.
- Add one sentence to `roles/hardener.md` saying that `guidance/**` belongs to the repo's maintainers and is not findings against the coder.

Deliverables: the docs describe the shipped behaviour, and the gates ignore rulebooks.
Verify: `grep -q '| practices |' README.md && grep -q 'practices' templates/marestail.toml && grep -q practices templates/tasks-README.md && python3 tools/test-practices.py && python3 tools/test-agent-backends.py && tools/dryrun.sh`

## Success Criteria (all must be true)
- [ ] `python3 tools/test-practices.py` exits 0 and prints `practices ok`.
- [ ] `python3 tools/test-agent-backends.py` exits 0 and prints `agent backends ok`.
- [ ] `tools/dryrun.sh` exits 0 and prints `remaining plan lines: 0`.
- [ ] `marestail.pipeline.names()` equals `["specifier", "critic", "coder", "cleaner", "architect", "practices", "perf", "hardener", "qa"]`.
- [ ] After the dry run, `git -C /tmp/marestail-dryrun/repo log --reverse --format=%s` contains a subject starting with `practices verdict: BOUNCE` on an earlier line than a subject starting with `practices verdict: PASS`, and `guidance/ts.md` is tracked.
- [ ] With no `guidance/` folder, a pipeline run makes no practices commit and prints `practices: no guidance files; skipping`; with `[practices] enabled = false` it prints `practices disabled in marestail.toml; skipping` (both covered in `tools/test-practices.py`).
- [ ] `guidance/**` is in `freeze.SPEC`, and neither install mode adds it to `.gitignore`.
- [ ] `marestail install` into an empty temporary dir creates `guidance/ts.md`, and does not overwrite an existing one.
- [ ] `templates/guidance/ts.md` has the four decision-8 sections, at least 40 `TS-\d+` rules, and at most 250 lines (covered in `tools/test-practices.py`).
- [ ] `grep -nE '^\s*#' marestail/practices.py` prints nothing, and every import in it is stdlib or `marestail.*`.

## Out of Scope
- Curated rulebooks for languages other than TypeScript; repos write their own `guidance/*.md` (do not build unless asked).
- Detecting languages from file extensions, or tying rulebooks to `marestail.toml` language sections.
- Injecting rulebook contents into the judge prompt; the agent reads the files itself.
- Per-rule severity, per-rule disables, or inline suppression comments.
- Any config keys beyond `[practices] enabled`.
- Bouncing to any role other than the coder; a cap on practices bounces.
- Migrating or installing guidance into existing target repos.
- Changing how the other judges work.

## Rules for the Implementing Agent
- Never delete, skip, or weaken a test to make it pass; flag suspect tests in PROGRESS-marestail-practices-tsar.md instead.
- Record failed approaches and key decisions in PROGRESS-marestail-practices-tsar.md as you go, including the Phase 3 gate audit.
- Commit after each completed phase.
- Stay inside this worktree. Do not touch sibling worktrees or the main checkout (`/home/max/workspace/marestail` is on `runtime-gate`, and other `marestail-*` worktrees belong to other runs).
- Do not run marestail gates or pipelines inside real target repos (e.g. StripeDonationPortal, animus-chat). Only use `tools/dryrun.sh` and temporary dirs.
- The cheat sheets in `~/Downloads` are read-only sources; distill them into `templates/guidance/ts.md`, and never modify them or copy them into the repo.
