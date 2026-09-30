Feature: Architect applies pattern rules; a design judge checks them

  After this task, `guidance/patterns/*.md` rulebooks ship with install, the
  architect applies a pattern only when its trigger is in the task's touched
  code, and a `design` judge between architect and practices bounces to the
  architect for a pattern without its trigger, a wrong form, or an unexplained
  trigger. Practices still reads only `guidance/*.md`.

  Background:
    Given this marestail-green checkout with its venv active
    And `tools/test-pattern-rulebooks.py` and `tools/test-install.py` drive the cases below

  Scenario: design runs after architect when pattern rulebooks exist
    Given a target with at least one file under `guidance/patterns/`
    When a pipeline reaches the design step after architect
    Then design runs (it is not skipped)
    And practices still runs after design when `guidance/*.md` exists

  Scenario: design skips when there are no pattern rulebooks
    Given a target whose `guidance/patterns/` is missing or empty of `*.md`
    When the design step would run
    Then stdout contains exactly the line `design: no pattern rulebooks; skipping`
    And the pipeline continues to practices

  Scenario: design is turned off in marestail.toml
    Given `[design] enabled = false` in the target's `marestail.toml`
    When the design step would run
    Then stdout contains exactly the line `design disabled in marestail.toml; skipping`

  Scenario Outline: design bounces to the architect for a rule violation
    Given pattern rulebooks are present and the architect handoff and task diff are in place
    When design finds <case>
    Then the verdict is `BOUNCE architect`
    And each finding cites `<rule id> <file:line>`

    Examples:
      | case                                                          |
      | a pattern applied without its trigger present                 |
      | a pattern applied in a form other than the rule's             |
      | a trigger in touched code neither applied nor explained under `## Patterns` |

  Scenario: design does not bounce on explained or pre-existing triggers
    Given a trigger in touched code that the architect listed under `## Patterns` with one line why it was not acted on
    Or a trigger only in code the task did not touch
    When design runs
    Then it does not bounce for those
    And on a PASS it may list pre-existing triggers under `## Pre-existing`

  Scenario: a repeated design bounce stops for a human
    Given design's previous bounce listed the same numbered findings as this bounce
    When design bounces again
    Then the run prints `design repeated the same findings twice; the worker is not making progress, stopping for a human` and stops

  Scenario: install places guidance and patterns by language marker
    When `marestail install` runs on a sample that has a `.csproj`, a root `Gemfile`, a root `mix.exs`, an `*.erl` under the tree, and a root `go.mod`
    Then the target gains `guidance/cs.md`, `guidance/rb.md`, `guidance/ex.md`, `guidance/er.md`, and `guidance/go.md`
    And it gains `guidance/patterns/cs.md`, `guidance/patterns/rb.md`, `guidance/patterns/ex.md`, `guidance/patterns/er.md`, `guidance/patterns/go.md`, and `guidance/patterns/ts.md`
    And a bare repo (none of those markers) still gets `guidance/ts.md` and `guidance/patterns/ts.md` only among those
    And a second install leaves an edited existing `guidance/patterns/ts.md` byte-for-byte

  Scenario: practices never sees pattern rulebooks
    Given `guidance/patterns/ts.md` exists and `guidance/` also has at least one top-level `*.md`
    When practices lists its rulebooks
    Then every path is a top-level `guidance/*.md` and none is under `guidance/patterns/`

  Scenario: existing guidance files and rule ids are unchanged
    Then `templates/guidance/ts.md` and `templates/guidance/cs.md` are byte-identical to before this task
    And `templates/guidance/er.md`, `ex.md` match `git show dfdb550:templates/guidance/{er,ex}.md`
    And `templates/guidance/rb.md` matches `git show 4148a1e:templates/guidance/rb.md`

  Scenario: pipeline order, window, hyper, and dry-run keep working
    Then `PIPELINE` places design immediately after architect and before practices
    And `window("architect", "practices")` includes design
    And a hyper run's role list has no design step (architect stays; design is dropped with practices)
    And `--from` / `--to` over design still select the same window shape as for other optional judges
    And `tools/dryrun-plan.txt` has a `judge design` line immediately after `worker architect`

  Scenario: pattern-rulebook and go practices checks pass
    When I run `python3 tools/test-pattern-rulebooks.py`
    Then the exit code is 0 and the last line contains `ok`
    And every `templates/guidance/patterns/*.md` rule id matches `<LANG>-P<n>`, is unique and sequential, and has non-empty trigger, form and not-when
    And every `GO-n` rule in `templates/guidance/go.md` is unique, sequential and non-empty
    And no pattern rule names a banned library unless the text also says "already a dependency"
    And no pattern rule restates EX-9..11, RB-4, or the listed ER-n forms
    And every Go helper named with a release states that `go.mod` release

  Scenario: other diagnostic scripts still pass
    When I run each existing `tools/test-*.py` that already exits 0 today
    Then each still exits 0 with a last line containing `ok`
    And `tools/test-perf.py` still fails exactly as before (`verdict-commit-files`)

  Scenario: README documents design and pattern rulebooks
    Then the Pipeline table has a `design` row between architect and practices
    And Best practices has one short paragraph that pattern rulebooks live in `guidance/patterns/`, the architect and design judge read them, and design bounces for the three cases above
