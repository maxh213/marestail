Feature: Durable per-step pipeline timeline

  After this task, every `marestail run` writes `.marestail/runs/<task>/timeline.md`
  and `.marestail/runs/<task>/timeline.json` that a stranger can read after the process
  is gone. Live stdout (`==`, `finished in`, `verdict`, …) and `marestail watch` stay
  exactly as they are; the timeline is an extra artefact.

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
    Given a judge role with a non-null gate tier whose `gate_for` run includes a result named "sonar" with `ok` false and `seconds` 41.0
    When that judge attempt finishes (agent may still run)
    Then the timeline step for that attempt has a `gate` entry `{name: "sonar", seconds: 41.0, ok: false}`
    And `agent` is present when a session ran
    And Sonar's FAIL is visible in the timeline even if it printed on stdout before the `== <judge>` line

  Scenario: a rate-limit then success records a waits entry
    Given `MARESTAIL_LIMIT_WAIT_SECONDS` is 0
    And the stub agent's first session exits rate-limited and the second succeeds
    When a worker attempt completes
    Then that step's `waits` contains `{reason: "rate-limit", seconds: 0}`
    And stdout still contains `rate limited; waiting`

  Scenario: two attempts append two steps that never disagree
    When the same role runs attempt 1 then attempt 2
    Then `timeline.json` has `"steps"` of length 2 with `attempt` 1 then 2
    And `timeline.md` has exactly two `##` sections
    And after each attempt the markdown sections and JSON steps list the same ids in the same order

  Scenario: overnight summary embeds the task timeline
    When `tools/overnight.sh tasks/t.md` finishes a run that wrote `.marestail/runs/t/timeline.md`
    Then `.marestail/runs/overnight-<stamp>.md` still has the `### tasks/t.md` section with exit, HEAD, and the existing grep lines
    And under that section it also includes the contents of that task's `timeline.md` (or a heading naming `timeline.md` plus a step `id` from the run)

  Scenario: timeline files are runner-owned
    Then workers cannot keep edits to `.marestail/runs/**/timeline.md` or `timeline.json`
    And those names are listed in freeze so a worker commit that touches them is rejected or reverted

  Scenario: schema fields for every attempt
    Then every timeline step includes, in order: id, role, attempt, started_at, ended_at, gate, waits, commits, files, done
    And `agent` is omitted only when the attempt never invoked a session (unlimited-retry exhaustion with no session)
    And `verdict` is present only for judges, as `PASS` / `BOUNCE` / `AUTHOR` plus bounce target when written
    And `done` is the handoff's first paragraph if present, else the first commit subject, else `agent.summary`

  Scenario: existing live log and watch stay unchanged
    Then every existing stdout wording is unchanged: `==`, `finished in`, `verdict`, `rate limited; waiting`, `dandelion/route:`, `GATE FAILED`, `pipeline complete`, `pipeline stopped at`
    And handoff / `*.json` / `*.prompt.md` layout is unchanged
    And `python3 tools/test-watch.py` exits 0 with last line containing `ok`
    And `marestail watch` still parses `==` and `finished in` as it does today

  Scenario: README documents the timeline artefacts
    When I read the README paragraph that mentions `.marestail/runs/<task>/pipeline.log`
    Then the same paragraph (or the next sentence) names `timeline.md` and `timeline.json`

  Scenario: the timeline diagnostic script passes
    When I run `python3 tools/test-timeline.py`
    Then it exits 0 and its last line contains `ok`
