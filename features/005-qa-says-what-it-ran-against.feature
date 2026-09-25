Feature: A run not checked against the running app does not end as plain "pipeline complete"

  After this task, the last line of `marestail run` and its exit code show whether
  QA exercised the real app (`ran-against: app|harness|nothing`). Exit 3 is new;
  0, 1 and 2 keep today's meanings.

  Background:
    Given a temporary git repo with marestail installed and a stub agent on PATH
    And the task file is "tasks/t.md" (stem "t")
    And features/qa for t are pre-seeded so the QA worker can pass its gate
    And `tools/test-qa-ran-against.py` drives the cases below with that stub

  Scenario: QA handoff with ran-against app ends as today
    When I run `marestail run tasks/t.md --from qa --to qa --auto --retries 2` with a stub whose accepted QA handoff contains a line that is exactly `ran-against: app`
    Then the last non-empty stdout line is exactly `pipeline complete`
    And the exit code is 0

  Scenario: QA handoff with ran-against harness exits 3
    When I run the same window with a handoff line that is exactly `ran-against: harness`
    Then the last non-empty stdout line is exactly `pipeline complete, NOT verified against the running app (qa ran against a harness)`
    And the exit code is 3

  Scenario: QA handoff with ran-against nothing exits 3
    When I run the same window with a handoff line that is exactly `ran-against: nothing`
    Then the last non-empty stdout line is exactly `pipeline complete, NOT verified against the running app (qa ran against nothing)`
    And the exit code is 3

  Scenario: missing ran-against line retries QA once then settles on nothing
    When the first accepted QA handoff has no line whose entire content is exactly `ran-against: app`, `ran-against: harness`, or `ran-against: nothing`
    Then the runner retries QA once with that absence as the finding
    And after that retry the last non-empty stdout line is exactly `pipeline complete, NOT verified against the running app (qa ran against nothing)`
    And the exit code is 3

  Scenario: ran-against buried in a sentence counts as missing
    When the handoff contains `We ran-against: app on a harness.` as a line (or the same words mid-paragraph) and no whole line that is exactly `ran-against: app`
    Then that handoff is treated as missing (retry once, then nothing / exit 3 as above)

  Scenario: --to hardener never claims QA ran
    When I run `marestail run tasks/t.md --from hardener --to hardener --auto --retries 1` with a stub that writes `VERDICT: PASS` (and the judge gate passes)
    Then the last non-empty stdout line is exactly `pipeline complete`
    And the exit code is 0
    And stdout does not contain `NOT verified`

  Scenario: overnight stops on exit 3 and records NOT verified
    Given a temp repo where `STOP_AT=qa` and the QA stub writes `ran-against: harness`
    When `STOP_AT=qa tools/overnight.sh tasks/t.md` finishes
    Then overnight exits 3 (stops; does not start a later task)
    And `.marestail/runs/overnight-*.md` under that task's `###` section contains `NOT verified`
    And that section still has the existing `- exit 3 after` line shape

  Scenario: marestail watch shows the NOT verified ending
    Given an overnight log whose last non-empty line is `pipeline complete, NOT verified against the running app (qa ran against a harness)`
    When `marestail watch --all` collects that bed
    Then the bed's finished row text contains `NOT verified`
    And it is not only the plain idle finished state (`○ idle` / idle-only)

  Scenario: roles/qa.md requires the line and forbids fake app claims
    Then `roles/qa.md` has one sentence that QA's handoff must include exactly one of `ran-against: app`, `ran-against: harness`, `ran-against: nothing`
    And that sentence says QA must not claim `app` for a stand-in

  Scenario: README pipeline section documents ran-against and exit 3
    Then the README `## Pipeline` section names the three `ran-against` values (`app`, `harness`, `nothing`) and what each means
    And it says exit code 3 means the run finished but was not verified against the running app

  Scenario: other endings and the qa gate stay as they are
    Then every other stdout wording is unchanged: `== qa (`, `finished in`, `pipeline stopped at`, and plain `pipeline complete` on exit 0
    And exit codes 0, 1 and 2 keep their current meanings (3 is only this NOT-verified case)
    And the `qa` worker tier and the `qa` gate (including skip when no `[qa] cmd`) behave as today up to the last line
    And with no `[qa] cmd` QA still must write the `ran-against` line; the ending still follows that line
    And QA remains a worker (no bounce; only its harness; product defects stay in the handoff)

  Scenario: diagnostic and existing tools tests
    Then `python3 tools/test-qa-ran-against.py` exits 0 and its last line contains `ok`
    And the existing `tools/test-*.py` scripts keep their current pass/fail contracts (including `tools/test-perf.py` exit 1)
