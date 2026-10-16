Feature: a visual judge looks at the before and after pictures and says whether the fix is visible

  Pinned rules (every scenario relies on them):
  - Place: with a `[visual]` section in `marestail.toml`, the pipeline gains the judge `visual` right after
    `hardener` and right before `qa`, in every scope (hyper: `..., blast, hardener, visual, qa`). The
    unknown-role list then names it: `unknown role bogus; choose from specifier, critic, coder, cleaner,
    architect, practices, perf, hardener, visual, qa`. Without a `[visual]` section the pipeline, the role
    lists, `--from visual` (unknown role, today's text) and every line printed are exactly today's.
  - Skips, one line each, the step then counts as passed: `enabled = false` prints
    `visual disabled in marestail.toml; skipping`; no `qa/<task>.md` prints `visual: no qa/<task>.md; skipping`;
    no `visual` block prints `visual: no block in qa/<task>.md; skipping`. A skipped visual starts no app and no agent.
  - Gate: before each round the runner runs the `visual` gate alone (013) for the task; that run is the fresh
    capture of base and HEAD, and its result line is the whole gate report in the prompt. A failing gate forces
    `BOUNCE` to the coder whatever the judge writes (today's `gated_verdict`).
  - Bounces: `BOUNCE coder` reruns the coder, `BOUNCE specifier` reruns the specifier; either way the visual
    judge runs again next. Any other target, or none, goes to the coder. Repeat limits are today's.
  - Model: `[visual] judge_model`, default `claude-fable-5-1`. Every visual attempt first runs the claude
    backend (`MARESTAIL_CLAUDE`) with `--model <judge_model>` and no `--effort`, whatever `--agent`, `--model`,
    `--effort` or `dandelion/route` the run uses. Only when that session is rate or usage limited (today's
    claude reader) the runner prints `visual: <judge_model> is out of usage; judging with <backend> <model>`
    (`<model>` is the run's `--model`; without one the line ends at `<backend>`; with `dandelion/route` it reads
    `judging with dandelion/route`) and at once runs the same attempt on the run's own backend and model: no
    `rate limited; waiting` line, no entry in the step's `waits`. If the fallback is limited too, today's wait applies.
  - Images: backends that cannot read images: `kilo`. All others can. With `dandelion/route` the backend it picks decides.
  - Prompt `# Visual` section, pictures shown:
      `Symptom: <symptom>` (`Symptom: none given in qa/<task>.md` without one)
      `## Pictures`, then one `- <path>` line per file, paths relative to the repo root: per viewport in config
      order, `base/<vp>/element.png`, `base/<vp>/viewport.png`, `head/<vp>/element.png`, `head/<vp>/viewport.png`
      under `.marestail/runs/<task>/visual/`; a file that does not exist gets ` (missing)` after it.
      `## Geometry`, then `| viewport | measure | base | HEAD |`, `|---|---|---|---|` and per viewport the rows
      `x`, `y`, `width`, `height`, `scrollWidth`, `overlaps` from each tree's `geometry.json` (box numbers;
      overlaps comma-joined or `none`; `missing` when the tree has no record or no box).
    Pictures not shown: `## Pictures` is replaced by the one line
      `The pictures could not be shown: <backend> cannot read images. Judge from the geometry table alone.`
      and the prompt contains no `.png`.
  - A PASS from a session that did not see the pictures is recorded as `PASS (geometry only, pictures not seen)`
    in stdout (`   verdict PASS (geometry only, pictures not seen)`), the verdict commit subject and the timeline
    step's `verdict`. If the run's last visual verdict is that, and QA ran against `app` or the run has no QA
    step, the run ends `pipeline complete, NOT verified by eye`, exit 3. QA's own NOT-verified ending wins over it.
  - The judge writes nothing (today's `discard_edits`). Its verdict commit is empty and stamped with the model
    that judged: `[claude-fable-5-1] visual verdict: PASS`, or the run's label after a fallback. The timeline
    step's `agent` is that session's backend and model.
  - `marestail watch` colours `visual` as a judge.

  Background:
    Given the fixture of `tools/test-visual.py` (013 Background) with `viewports = { desktop = "1440x900", phone = "390x844@2 touch" }`
    And `marestail.toml` also has `[qa] cmd = "true"`, `[practices] enabled = false`, `[perf] enabled = false`
    And the `visual` block in `qa/t.md` also has `symptom: the widget is 440px wide`
    And `tasks/t.md` is `# Change the page text`
    And `features/t.feature` is written directly (not by running `tools/stub-claude specify`, which would overwrite `qa/t.md`)
      as the five lines `Feature: t`, `  Scenario: Adds one`, `    Given x`, `  Scenario Outline: Rejects bad input`, `    Given y`
    And on branch `work` `Page text` became `Other text`, committed
    And `MARESTAIL_CLAUDE`, `MARESTAIL_CURSOR` and `MARESTAIL_KILO` point at symlinks named `claude`, `cursor-agent`
      and `kilo` to one capture wrapper that saves `<basename of $0> <args>` to `$ARGS/NN.txt` and its stdin to
      `$PROMPTS/NN.txt` (NN = 01, 02, ... per invocation), then pipes stdin to `tools/stub-claude` and exits with its code
    And an invocation's role is read from the first line of its saved prompt (`You are the visual judge.` for visual)
    And `tools/stub-claude` gains the action `limit`, which prints `{"is_error": true, "result": "rate limit exceeded"}` and exits 1
    And `STUB_PLAN` holds one action per invocation and `MARESTAIL_LIMIT_WAIT_SECONDS=0`

  Scenario: the visual judge runs between the hardener and QA and sees the pictures
    Given STUB_PLAN is `judge PASS`, `judge PASS`, `worker qa`
    When I run `marestail run tasks/t.md --from hardener --to qa --auto --retries 2`
    Then the `== <role> (` lines name, in order, hardener, visual, qa
    And `$ARGS/02.txt` contains `--model claude-fable-5-1` and not `--effort`
    And `$PROMPTS/02.txt` contains the line `# Visual`, and the line `Symptom: the widget is 440px wide` comes after it
    And it contains the 8 lines, in order, `- .marestail/runs/t/visual/base/desktop/element.png`, `…/base/desktop/viewport.png`,
      `…/head/desktop/element.png`, `…/head/desktop/viewport.png`, then the same four for `phone`, each file existing
    And it contains `| desktop | x | 434 | 434 |`, `| desktop | width | 440 | 440 |`, `| desktop | scrollWidth | 1440 | 1440 |`,
      `| desktop | overlaps | none | none |`, `| phone | x | 0 | 0 |`, `| phone | scrollWidth | 572 | 572 |`
    And it contains `[ok  ] visual` and no other gate line
    And the one commit whose subject contains `visual verdict` is `[claude-fable-5-1] visual verdict: PASS` and changes no file
    And the exit code is 0 and the last line is `pipeline complete`

  Scenario: no visual block, no section, or disabled
    Given STUB_PLAN is `judge PASS`, `worker qa`
    When I run the same command with <change>
    Then stdout <shows> and the `== <role> (` lines name hardener, qa
    And 2 prompts were saved and the exit code is 0

    Examples:
      | change                              | shows                                                     |
      | the `visual` block removed          | has `visual: no block in qa/t.md; skipping`               |
      | `qa/t.md` deleted                   | has `visual: no qa/t.md; skipping`                        |
      | `[visual] enabled = false`          | has `visual disabled in marestail.toml; skipping`         |
      | the `[visual]` section removed      | has no line containing `visual`                           |

  Scenario: without a [visual] section the role list is today's
    Given the `[visual]` section removed
    When I run `marestail run tasks/t.md --from visual --auto`
    Then the exit code is 1 and stderr contains `unknown role visual; choose from specifier, critic, coder, cleaner, architect, practices, perf, hardener, qa`

  Scenario Outline: the verdict decides where the run goes
    Given STUB_PLAN is `judge PASS`, `judge <verdict>`, `<next>`, `judge PASS`, `worker qa`
    When I run `marestail run tasks/t.md --from hardener --to qa --auto --retries 2`
    Then the invocations are, in order, hardener, visual, <role>, visual, qa
    And `$ARGS/04.txt` contains `--model claude-fable-5-1`
    And the exit code is 0

    Examples:
      | verdict            | next      | role       |
      | BOUNCE coder       | code      | coder      |
      | BOUNCE specifier   | worker specifier | specifier  |
      | BOUNCE hardener    | code      | coder      |

  Scenario: a failing visual gate forces a bounce to the coder over a PASS
    Given on `work` the widget is `width:1408px`, committed
    And `.marestail/runs/t/start-commit` holds the output of `git rev-parse main`, so the gate's base is `main`
    And STUB_PLAN is `judge PASS`, `code`, `judge PASS`, `worker coder`
    When I run `marestail run tasks/t.md --from visual --to visual --auto --retries 1`
    Then stdout contains `   verdict BOUNCE` after the first `== visual (`, though the stub wrote PASS
    And the invocations are, in order, visual, coder, visual, coder
    And `$PROMPTS/02.txt` (the first coder) contains `[FAIL] visual` and `desktop: #widget is 1408px wide, .col is 572px (base: 440px)`
    And the second coder attempt is rejected (its handoff has no `## Audit`), so the run ends there
    And stdout contains no `repeated the same findings`, the last line is `pipeline stopped at visual` and the exit code is 1

  Scenario: another backend runs every other role; the visual judge still uses claude-fable-5-1
    Given STUB_PLAN is `judge PASS`, `judge PASS`, `worker qa`
    When I run `marestail run tasks/t.md --from hardener --to qa --agent cursor --model gpt-9 --auto --retries 2`
    Then `$ARGS/01.txt` and `$ARGS/03.txt` start with `cursor-agent` and contain `--model gpt-9`
    And `$ARGS/02.txt` starts with `claude` and contains `--model claude-fable-5-1`
    And the visual verdict commit is `[claude-fable-5-1] visual verdict: PASS`; the hardener's is `[gpt-9] hardener verdict: PASS`

  Scenario: a configured judge_model is used
    Given `[visual] judge_model = "claude-opus-5-5"` and STUB_PLAN is `judge PASS`
    When I run `marestail run tasks/t.md --from visual --to visual --auto`
    Then `$ARGS/01.txt` contains `--model claude-opus-5-5`

  Scenario: an out-of-usage judge model falls back at once to the run's backend
    Given STUB_PLAN is `limit`, `judge PASS`
    When I run `marestail run tasks/t.md --from visual --to visual --agent cursor --model gpt-9 --auto`
    Then stdout contains `visual: claude-fable-5-1 is out of usage; judging with cursor gpt-9`
    And stdout contains no `rate limited; waiting`
    And `$ARGS/02.txt` starts with `cursor-agent` and contains `--model gpt-9`, and `$PROMPTS/02.txt` lists the 8 pictures
    And the visual step in `.marestail/runs/t/timeline.json` has `"waits": []` and agent backend `cursor`, model `gpt-9`
    And the verdict commit is `[gpt-9] visual verdict: PASS` and the exit code is 0

  Scenario: a fallback backend that cannot read images judges from geometry alone
    Given STUB_PLAN is `limit`, `judge PASS`, `worker qa`
    When I run `marestail run tasks/t.md --from visual --to qa --agent kilo --model kilo/x --auto --retries 2`
    Then stdout contains `visual: claude-fable-5-1 is out of usage; judging with kilo kilo/x`
    And `$PROMPTS/02.txt` contains `The pictures could not be shown: kilo cannot read images. Judge from the geometry table alone.`,
      the geometry table, and no `.png`
    And stdout contains `   verdict PASS (geometry only, pictures not seen)`
    And the visual verdict commit subject is `[kilo/x] visual verdict: PASS (geometry only, pictures not seen)`
    And the last line is `pipeline complete, NOT verified by eye` and the exit code is 3

  Scenario: with dandelion/route the judge model is still asked first
    Given `MARESTAIL_DANDELION` points at the stub `dandelion` of `tools/test-timeline.py` (`stub_dandelion`): an executable
      file, so `route.require()` finds it; invoked as `<bin> route <args>`, it appends its args to `$CALLS`, takes the
      first line of `$PLAN` (`<exit> <plan line>`), prints the plan line on stdout and exits with `<exit>`
    And `$PLAN` is `0 gpt-9 cursor`, `0 gpt-9 cursor` and STUB_PLAN is `limit`, `judge PASS`, `worker qa`
    When I run `marestail run tasks/t.md --from visual --to qa --model dandelion/route --auto --retries 2`
    Then `$ARGS/01.txt` starts with `claude` and contains `--model claude-fable-5-1`
    And stdout contains `visual: claude-fable-5-1 is out of usage; judging with dandelion/route` before `   dandelion/route: gpt-9 cursor`
    And `$ARGS/02.txt` starts with `cursor-agent`, contains `--model gpt-9` and does not contain `claude-fable-5-1`
    And `$ARGS/03.txt` (qa) starts with `cursor-agent` and does not contain `claude-fable-5-1`
    And `$CALLS` has 2 lines, the visual verdict commit is `[gpt-9] visual verdict: PASS` and the exit code is 0

  Scenario: the other judges and roles keep their waits
    Given STUB_PLAN is `limit`, `judge PASS`
    When I run `marestail run tasks/t.md --from hardener --to hardener --auto`
    Then stdout contains `rate limited; waiting 0 min before retrying` and no `out of usage`

  Scenario: watch shows the visual step as a judge
    Given the run of the first scenario has finished
    When I open that run in `marestail watch`
    Then its steps list `visual` between `hardener` and `qa`, in the same colour as `hardener`

  Scenario: diagnostics and README
    When I run `python3 tools/test-visual-judge.py`
    Then it exits 0 and its last line is `visual judge ok`
    And every other `tools/test-*.py` exits as it did before this task (`tools/test-perf.py` still exits 1 with `verdict-commit-files: '' != 'perf/bench_x.py'`)
    And `roles/visual.md` holds `You are the visual judge.`, a blank line, then the task's paragraph verbatim
    And README's pipeline table has the row `| visual | judge | visual | visual |` between `hardener` and `qa`
    And README has a paragraph naming `judge_model`, `claude-fable-5-1`, the at-once fallback and `NOT verified by eye`
