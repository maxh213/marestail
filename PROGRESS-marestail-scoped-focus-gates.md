# PROGRESS — marestail-scoped-focus-gates

Worktree: /home/max/workspace/marestail-marestail-scoped-focus-gates
Branch: feat/marestail-scoped-focus-gates
Prompt: PROMPT-marestail-scoped-focus-gates.md

## Phase 1: scope plumbing
- [x] changed_lines in changes.py + Context helpers + cli --focus + report scope line
- [x] Verify: hunk-parse proof (prompt proof + extended: committed/staged/unstaged/rename/delete/binary)

## Phase 2: Python gates + fixture
- [ ] py_* + comments/deadcode/depth honor scope; tools/samples/scope-py.sh
- [ ] Verify: three verdicts (scoped PASS / all FAIL / dirtied FAIL)

## Phase 3: Ruby gates + fixture
- [ ] rb_* honor scope; tools/samples/scope-rb.sh (or documented skip)

## Phase 4: remaining languages + sonar
- [ ] ex_*/ts_*/cs_*/er_* honor scope; sonar post-filter + unit assertion

## Phase 5: docs + audit
- [ ] README scope paragraph; grep audit clean; --help shows --focus

## Final
- [ ] All success criteria; worktree.py done

## Decisions / notes
- User amendment (2026-09-12): scope follows the diff, not static paths — changed-line granularity for coverage, hunk→function mapping for CRAP; `--focus` is a manual add-on, not the primary mode. Prompt rewritten to match before starting.
- Execution plan: phase 1 solo (contract), phases 2-4 parallel subagents (disjoint gate files), phase 5 last.
- Phase 1 (2026-09-12): working-tree half of `changed_lines` uses `git diff -U0 HEAD` (covers staged AND unstaged; plain `git diff -U0` would silently miss staged-only changes). Untracked binary/unreadable files are absent from the map; tracked deletion-only files are present with an empty line set.
- Phase 1: `--scope` default changed from `"all"` to `None` so an explicit `--scope all` + `--focus` is a usage error while bare `--focus` implies changed scope. Effective default unchanged (`all` when no focus).
- Phase 1: hook mode reads `[focus] paths` via `config.focus_paths`; nonexistent config paths warn on stderr and are skipped (hard error would deadlock the Stop hook; the typo surfaces every stop attempt). CLI `--focus` stays a hard error.
- Phase 1: `run_gates` keeps its old signature/return (runner.py untouched); new `run_gates_with_context` returns `(results, ctx)` for scope-aware rendering. `Context.changed_under` now unions focus-derived files so legacy file-level gates see focused paths.
- Phase 1 verified: compileall; prompt hunk proof; extended hunk proof; worktree CLI errors (`--scope all --focus x` exit 2, `--focus /does/not/exist` non-zero); /tmp fixture render/json/hook checks; `--scope all` report byte-identical vs HEAD on a fixture.
- Phase 3 (2026-09-12): rb_tests/rb_crap/rb_lint/rb_mutation/rb_deps honor scope; tools/samples/scope-rb.sh green (34 assertions).
- Phase 3 fixture: ruby/gem/bundle/rspec NOT installed on this machine (checked `which ruby gem bundle rspec`) — rspec/SimpleCov integration skipped per prompt fallback; fixture is unit-level: fabricated SimpleCov .resultset.json + REAL git hunks from changed_lines/changed_files in a /tmp repo wired through the gates' filtering functions. Skip note printed by the script on machines without ruby.
- Phase 3 rb_tests: scoped = in_scope files only, missing lines ∩ gated_lines; missing SimpleCov arms gated by arm span ∪ condition start line (parsed from `"[:if, id, sl, sc, el, ec]"` keys) — a changed condition line gates its arms, a changed then-body does not gate the else arm; unparseable keys keep the finding (never silently dropped). Scoped percent = covered/total over gated relevant lines, stored as totals.scoped_percent_covered; rb-coverage.json gains per-file branch_lines ONLY when scoped (unscoped artifact byte-identical).
- Phase 3 rb_crap: methods gated when [def_line, end_line] ∩ hunk ≠ ∅; end lines computed Python-side (line_delta keyword-balance heuristic: string/comment/embdoc masking, line-start openers, trailing do-blocks, while/for...do counted once; fallback = next-def-1 on imbalance). Nested defs balance correctly because scans are per-method, not clamped. Moved methods are gated at destination (their def line is in the diff). Note for shared file: marestail/rb/scan.rb "complexity" mode emits only def-line; having Ripper emit each method's end line would replace the heuristic with exact ranges — NOT edited (shared with comments/deadcode/depth/graph agents).
- Phase 3 rb_lint/rb_deps: post-filter findings by ctx.in_scope (focus-inclusive); rb_mutation: ctx.scoped + changed_under (focus unions in via Phase 1 contract) — file-level scoping was already the design, no line granularity by prompt.
- Phase 2 (2026-09-12, python agent): py_tests intersects coverage.py JSON missing lines/branches with `ctx.gated_lines` (branch findings keyed on the branch start line); py_crap keeps radon function/method blocks whose [lineno, endline] intersects the gated line set (moved functions are gated in their new file via the new-file hunks); py_lint/py_mutation switched from `ctx.scope_changed` to `ctx.scoped` (file-level scoping was already correct); py_deps parses import-linter `-   mod.a -> mod.b` violation lines and keeps only those whose importer module file is in scope (unresolvable modules kept fail-closed; non-"Broken contracts" failures still fail); comments/deadcode/depth filter with `ctx.in_scope` so focus paths count. py.runtime stays global per prompt. Fixture `tools/samples/scope-py.sh`: /tmp repo (uv-or-venv bootstrap), dirty committed `core/dirty.py` (uncovered lines+branches, CRAP 12, comment, unreachable code, unused import, bad formatting, forbidden `core -> app` import) + clean working-tree change; 4 verdicts (scoped PASS / all FAIL / dirtied-changed FAIL / moved-function gated in app/helpers.py). Verified: compileall, zero comments/docstrings, `--scope all` report byte-identical vs 23a2470 (modulo wall-clock seconds), `--focus core/dirty.py` gates the focused file whole, untracked new module CRAP-gated (unimported files have no coverage record — same as unscoped; CRAP is the backstop). Committed as 3043b4a (own files only).
- Phase 4 (2026-09-12, ts/cs/er agent): all 15 assigned gates honor scope. Tests gates intersect report gaps with `ctx.gated_lines` (ts: istanbul statement start lines; branch arms gated by arm-loc ∪ branch-loc start line — touching a condition gates both arms. cs: coverlet missing_lines/missing_branches ∩ gated. er: cover missing_lines ∩ gated). CRAP gates keep functions whose span intersects the hunk set: ts uses istanbul fn line/endLine from ts_complexity.mjs; cs uses scanner startLine/endLine; er computes end lines Python-side as next-function-start-1 (escript emits only start lines — NOT edited, it's under marestail/erl/ shared tooling; over-inclusion of inter-function gaps is the safe direction). Lint gates post-filter to in-scope files (tsc has no per-file mode; eslint kept on `.` and filtered so config-ignored files don't error; erlc strong-warnings findings filtered, unparseable compiler output kept global/fail-closed; cs SARIF results filtered per .cs file, project-level/location-less results kept global). Deps gates keep violations whose FROM file is in scope (ts: depcruise err lines parsed incl. indented multi-line circular chains `sev rule: from → ...`; unparseable non-violation output fails closed). Mutation gates: ts/cs already file-scoped via --mutate/-m, switched to ctx.scoped so focus unions in; survivors post-filtered by in_scope; er.mutation stays a permanent honest skip. cs_tests attribute scan ([ExcludeFromCodeCoverage]) scoped to in-scope files.
- Phase 4 toolchain reality: node/npx PRESENT (v26.7.0) → tools/samples/scope-ts.sh is a REAL fixture (vitest+@vitest/coverage-v8, tsc, eslint, dependency-cruiser npm-installed in /tmp; 3 verdicts green + 20 unit assertions). dotnet MISSING → tools/samples/scope-cs.sh is unit-level only: full run_gate exercised with a monkeypatched marestail.dotnet layer (fabricated SARIF, coverlet dicts, stryker report, scanner members) + fabricated changed_lines_map; skip note printed. erlang OTP 29 PRESENT → tools/samples/scope-er.sh REAL fixture (erlc/eunit/cover; 3 verdicts + unit assertions green).
- Phase 4 pitfalls: typescript@latest is 7.0.2 (native rewrite) — breaks ts_complexity.mjs (no ts.SyntaxKind) AND makes dependency-cruiser cruise 0 modules; fixtures MUST pin typescript@5. depcruise err output INDENTS violations 2 spaces and prints circular chains across 3 lines ending in a bare →; anchored regexes must strip first. ts fixture files avoid TS type annotations so espree (eslint default parser) can parse them.
- Phase 4 verified: compileall clean; zero comments/docstrings (ast audit); --scope all gate report BYTE-IDENTICAL vs HEAD on an erlang fixture (seconds stripped); --focus src/partial.erl gates the focused file whole (pre-existing uncovered lines fail); --scope all --focus rejected with usage error. Shared-file note: marestail/dotnet.py `in_scope()` predates focus (checks ctx.changed only) — my cs gates bypass it with ctx.in_scope(dotnet.rel(...)); consider updating dotnet.in_scope for consistency (NOT edited: shared with comments/deadcode/depth/graph/sonar agents).

## Final verification (orchestrator, all green)
- compileall OK; style scan: zero comments/docstrings across marestail/
- audit: only docs.py, qa.py, py_runtime.py + __init__.py (registry, accepted exception)
- fixtures: scope-py/rb/ex/ts/er/cs/sonar all exit 0 (rb/cs documented toolchain skips, unit-level)
- --scope all --focus x → exit 2; --focus /does/not/exist → exit 1
- All 9 success criteria verified. Claim marked done.
