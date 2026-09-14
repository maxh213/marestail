# PROGRESS — marestail-mutation-kimi-install-gitignore

Worktree: /home/max/workspace/marestail-marestail-mutation-kimi-install-gitignore
Branch: feat/marestail-mutation-kimi-install-gitignore
Prompt: PROMPT-marestail-mutation-kimi-install-gitignore.md
Status: COMPLETE

## Phase 1: Mutation testing on every full-tier run
- [x] ex/cs opt-in checks removed; fail closed with remediation when muex/Stryker absent
- [x] rb_mutation.py implemented on mutant (0.15 stdout + 0.16 session-JSON formats), bundle() prefix, 7200s, changed-scope subject mapping
- [x] tools/samples/ruby wired (mutant 0.15.1 pinned, config/mutant.yml); red: 99/116 not killed naming lib/box.rb; green after fixes (verified both mutant generations)
- [x] templates/marestail.toml opt-in toggles dropped; dead `mutation = true` removed from dotnet sample
- [x] config/mutant.yml added to freeze.py GATE_CONFIG (decision: mutation configs must be unmodifiable by agents, same as stryker.config.*)
## Phase 2: Kimi CLI backend
- [x] runner.py: kimi_command (kimi -p <prompt> --output-format stream-json, -m only with model, MARESTAIL_KIMI override), invoke_kimi retry loop, lenient JSONL parse/summary/rate-limit
- [x] cli.py: kimi in --agent choices; --gitignore-generated wired to install()
- [x] test-agent-backends.py kimi assertions; prints "agent backends ok"
- [x] Real smoke test: kimi 0.42.0 ran a specifier step end-to-end, [kimi]-stamped commit produced
## Phase 3: install --gitignore-generated
- [x] install.py: flag gitignores features/, qa/, tasks/, sonar-project.properties, hook configs; CLAUDE.md/AGENTS.md only when installer-created; marestail.toml never; idempotent; default path byte-identical (46/46 checks)
- [x] Verified end-to-end via real CLI in /tmp/inst-e2e (all three scenarios)
## Integration
- [x] README updated (gate table, kimi backend paragraph, effort note, install flag)
- [x] Verification: test-agent-backends ok, dryrun exit 0, rb.mutation red live, install scenarios pass, no "mutation = true"/"opt-in" remnants
- [x] Commits: 9f52f26 (phase 1), 43344ba (phase 2), 788bf03 (phase 3), 7b03fd7 (docs)

## Decisions
- Parallel delegation with strict file ownership; README and per-phase commits done by parent to avoid same-file/git-index races.
- Kimi prompt delivered in argv (spec pins exact argv); kimi emits no usage events in 0.42.0 so summaries show text only.
- No kimi Stop hook (out of scope per prompt).

## Gaps / failed approaches
- ex.mutation happy path not run e2e (no elixir sample exists; muex absent). Only the entry check changed; muex invocation/parsing byte-identical to the previously working gate. Fail-closed path verified live with mix present.
- cs.mutation verified by subagent via docker SDK fallback (Stryker executes, fails on planted survivors).

## Follow-up: Erlang language support (committed 6ec6884)
- [x] Gates: er.tests (eunit+cover escript), er.crap (erl_parse complexity + coverage), er.lint (erlc strong warnings as errors), er.deps (beam call-graph Tarjan cycles), er.mutation (visible skip — no Erlang mutation tester exists)
- [x] Neutral gates: comments/deadcode/depth erlang branches + 3 new escripts; graph.py branch; freeze (rebar.config, rebar.lock, **/*.app.src); template [erlang]; README column + paragraph
- [x] tools/samples/erlang planted-violation sample: full-tier red for every planted reason (verified independently), green when fixed, scope-changed + fail-closed + timing all checked
- [x] er.mutation note: complexity.escript counts nested if clauses, sample classify/1 scores cc=9 (red for the right function/line regardless)
- [x] cmd_rpg_that_plays_itself: install --gitignore-generated verified on a real repo (git status shows only shared files); baseline gates run (81 comments, deps cycle, no tests); marestail.toml trimmed to [erlang]; tasks/000-bootstrap-tests.md written (behaviour freeze refactor task)
- [~] Pipeline run 000 in flight: `marestail run tasks/000-bootstrap-tests.md --auto` (kimi), log /tmp/rpg-run-000.log
- [x] Pipeline run 000 COMPLETE, exit 0, full pipeline green in ~2h: specifier(3 critic bounces) -> coder(2 attempts; 33min first pass to GATE PASSED) -> cleaner -> architect(split world rules from OTP shell) -> hardener(1 bounce: 3 boundary mutants killed) -> qa(2 attempts). Final repo state: GATE PASSED — 114 eunit tests, 100% coverage, 0 CRAP>4, acyclic, zero comments, no dead code.
- [x] Frozen-file machinery fired correctly in production: 14-qa's `[qa] cmd = "make qa"` edit to marestail.toml auto-reverted (commit d76a1f6), recorded as 15-proposal.md; 16-qa finished within frozen config. Proposal awaits human decision in cmd_rpg_that_plays_itself.
