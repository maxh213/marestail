Feature: Durable per-step pipeline timeline

  After this task, every `marestail run` writes `.marestail/runs/<task>/timeline.md`
  and `.marestail/runs/<task>/timeline.json`. Live stdout and `marestail watch`
  stay as they are; the timeline is an extra artefact.

  Background:
    Given a temporary git repo with marestail installed and a stub agent on PATH
    And the task file is "tasks/t.md" (stem "t")

  Scenario: a coder attempt that commits one file writes a timeline step
    When I run `marestail run tasks/t.md --from coder --to coder --auto --retries 1` with a stub that commits "src.py" and writes a handoff whose first paragraph is "coded the adder"
    Then `.marestail/runs/t/timeline.json` is `{"task": "t", "steps": [ … ]}` with exactly one step
    And that step has `id` matching `^\d+-coder$`, `role` "coder", `attempt` 1
    And `started_at` and `ended_at` are UTC ISO-8601 strings ending in "Z"
    And `gate` is a list (possibly empty), `waits` is a list
    And `agent` is present with keys backend, model, effort, account, minutes, summary
    And `agent.minutes` is a number >= 0 and `agent.summary` equals the one-line `describe(output)` printed after `finished in`
    And `commits` is a non-empty list of `{hash, subject}` and `files` includes "src.py"
    And `done` is "coded the adder"
    And there is no `verdict` key on that worker step
    And `.marestail/runs/t/timeline.md` has one `##` section whose heading contains that step `id`
    And stdout still contains a line matching `^== coder \(\d+-coder\) attempt 1$`
    And stdout still contains a line matching `^\s+\d+-coder finished in [0-9.]+ min:`

  Scenario: a judge whose gate fails still records the failed gate then the agent
    Given `tools/test-timeline.py` doubles `Run.gates` so the hardener's single pre-loop `gate_for` returns one `Result` with `gate="sonar"`, `ok=False`, `seconds=41.0` (and does not call `state.gates` again per attempt)
    When I run `marestail run tasks/t.md --from hardener --to hardener --auto --retries 1` with a stub that writes `VERDICT: PASS`
    Then `gate_for` still ran once before the attempt loop (gate behaviour unchanged)
    And every timeline step for that judge round that reuses the shared result has `gate` containing `{"name": "sonar", "seconds": 41.0, "ok": false}`
    And that step's `"verdict"` is `"BOUNCE"` (gated_verdict forces bounce; do not copy the stub's PASS line)
    And `agent` is present when a session ran
    And Sonar's FAIL is in the timeline even though it printed on stdout before `== hardener`

  Scenario: a rate-limit then success records a waits entry
    Given `MARESTAIL_LIMIT_WAIT_SECONDS` is 0
    And a stub claude whose first session prints only `{"is_error": true, "result": "rate limit exceeded"}` (exit 0; matches `backends.rate_limited`) and whose second session prints normal success JSON and does the coder work
    When I run `marestail run tasks/t.md --from coder --to coder --auto --retries 1`
    Then that step's `waits` contains `{"reason": "rate-limit", "seconds": 0}`
    And stdout still contains `rate limited; waiting`

  Scenario: a dandelion-unrouted wait then success records a waits entry
    Given `MARESTAIL_LIMIT_WAIT_SECONDS` is 0
    And `--model dandelion/route` with a stub dandelion whose first `route` prints `none` (exit 1) and whose second prints `claude-opus-5 high claude`
    And a stub claude that then succeeds
    When a worker attempt completes
    Then that step's `waits` contains `{"reason": "dandelion-unrouted", "seconds": 0}`
    And stdout still contains a line matching `dandelion/route:`

  Scenario: two attempts append two steps that never disagree
    Given a temp repo pre-seeded with features/qa matching the stub coder Audit lines (as after a successful specify)
    When I run `marestail run tasks/t.md --from coder --to coder --auto --retries 2` with STUB_PLAN lines `code bad-audit` then `code` (first session leaves verify failing; second commits + handoff)
    Then `timeline.json` has `"steps"` of length 2 with `attempt` 1 then 2
    And `timeline.md` has exactly two `##` sections
    And after each attempt the markdown sections and JSON steps list the same ids in the same order

  Scenario: overnight summary embeds the task timeline
    Given a temp repo with `MARESTAIL_CLAUDE` pointing at `tools/stub-claude`, STUB_PLAN line `code`, and features/qa pre-seeded so the coder window exits 0
    When `START_FROM=coder STOP_AT=coder tools/overnight.sh tasks/t.md` finishes (STOP_AT is a role name, not a freestyle token)
    Then `.marestail/runs/overnight-<stamp>.md` still has the `### tasks/t.md` section with exit, HEAD, and the existing grep lines
    And under that section it also includes the contents of that task's `timeline.md` (or a heading naming `timeline.md` plus a step `id` from the run)

  Scenario: timeline files are runner-owned
    Then workers cannot keep edits to `.marestail/runs/**/timeline.md` or `timeline.json`
    And those names are listed in freeze so a worker commit that touches them is rejected or reverted

  Scenario: schema fields for every attempt
    Then every timeline step's keys appear in this order when present: id, role, attempt, started_at, ended_at, gate, waits, agent, verdict, commits, files, done
    And `agent` sits after `waits` and before `verdict` when the attempt invoked a session
    And `verdict` sits after `agent` (or after `waits` if `agent` is omitted) and before `commits` when the role is a judge
    And `agent` is omitted only when the attempt never invoked a session (unlimited-retry exhaustion with no session)
    And a passing judge step has `"verdict": "PASS"`
    And a bounce with target has `"verdict": "BOUNCE specifier"` (string: verdict, space, target; no object)
    And a gate-forced bounce with no target has `"verdict": "BOUNCE"`
    And an AUTHOR attempt (perf stub writes `VERDICT: AUTHOR`) has `"verdict": "AUTHOR"` and still appends a timeline step when the session ends (even though `judged` returns None and the attempt loop continues)
    And `verdict` is omitted for workers
    And `done` is the handoff's first paragraph if present, else the first commit subject, else `agent.summary`

  Scenario: existing live log and watch stay unchanged
    Then every existing stdout wording is unchanged: `==`, `finished in`, `verdict`, `rate limited; waiting`, `dandelion/route:`, `GATE FAILED`, `pipeline complete`, `pipeline stopped at`
    And handoff / `*.json` / `*.prompt.md` layout is unchanged
    And `python3 tools/test-watch.py` exits 0 with last line containing `ok`
    And that script is green-shaped: it asserts today's `latest_log` (overnight-*.log only) and bed discovery; it does not restore origin/main's pipeline.log preference
    And `marestail watch` still parses `==` and `finished in` as it does today
    And this task does not add `pipeline.log` writing or change `latest_log`

  Scenario: README documents the timeline artefacts
    When I read the README Overnight paragraph that names `.marestail/runs/overnight-<stamp>.md`
    Then the same paragraph (or the next sentence) names `timeline.md` and `timeline.json`
    And the README does not claim a `.marestail/runs/<task>/pipeline.log` path that this branch does not write

  Scenario: the timeline diagnostic script passes
    When I run `python3 tools/test-timeline.py`
    Then it exits 0 and its last line contains `ok`
