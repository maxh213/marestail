Feature: The qa gate starts the target's app when [qa] start is set

  After this task, a target can set optional `[qa] start` / `ready` / `port` /
  `ready_timeout` / `env`, and the `qa` gate brings that app up for `cmd`.
  Without `start`, behaviour is unchanged. Driven by `tools/test-qa-app.py`
  using a tiny Python HTTP server as the app.

  Background:
    Given a temporary git repo with marestail installed
    And `tools/test-qa-app.py` drives the cases below
    And the pasteable app is `app.py` that does
      `HTTPServer(("127.0.0.1", int(os.environ["PORT"])), SimpleHTTPRequestHandler).serve_forever()`
      with `start = "python3 app.py"`

  Scenario: start plus ready runs cmd only after the app answers
    Given `[qa]` has `cmd` that prints `$MARESTAIL_APP_URL` and exits 0,
      `start = "python3 app.py"`, `ready = "/"`, `port = 3400`
    When the `qa` gate runs
    Then `cmd` runs only after `GET http://localhost:<chosen-port>/` returns status < 500
    And `cmd`'s environment has `MARESTAIL_APP_URL=http://localhost:<chosen-port>` and `PORT=<chosen-port>`
    And the gate result is ok with summary containing `qa passed`
    And the app's process group is gone after the gate returns

  Scenario: app process group is gone when cmd fails
    Given the same `start`/`ready`/`port` as above
    And `cmd` exits 1
    When the `qa` gate runs
    Then the gate result is not ok
    And the app's process group is gone after the gate returns

  Scenario: start that never listens fails with did-not-answer and log tail
    Given `[qa] start = "sleep 60"`, `ready = "/"`, `port = 3400`, `ready_timeout = 2`
    When the `qa` gate runs
    Then the gate fails with summary exactly `qa: app did not answer on http://localhost:3400/ within 2s`
    And findings are the last ten non-empty lines of the app log
    And `cmd` is never run
    And the start process group is gone

  Scenario: a taken port moves to the next free one
    Given port 3400 is already bound
    And `[qa]` has `start = "python3 app.py"`, `ready = "/"`, `port = 3400`
      and `cmd = "printf '%s' \"$MARESTAIL_APP_URL\" > seen-url"`
    When the `qa` gate runs
    Then the file `seen-url` contains exactly `http://localhost:<p>` for some free `p` > 3400
    And the ready poll and the app both used that same `p`

  Scenario: env reaches the app and never the QA prompt
    Given `[qa] env = { CMS_URL = "https://example.test/g", LOCALE = "en-gb" }`
      and `start = "python3 app.py"` where `app.py` also prints `CMS_URL` and `LOCALE` to stdout
    When the `qa` gate runs
    Then the app log contains `https://example.test/g` and `en-gb`
    And `prompts.worker_prompt` for the qa worker does not contain `https://example.test/g` or `en-gb`

  Scenario: with no start the gate behaves as today
    Given `[qa]` has only `cmd = "true"` and `cwd = "."` (no `start`)
    When the `qa` gate runs
    Then it is still `bash -lc` of that cmd in `cwd` with timeout 3600 and no app process

  Scenario: empty cmd skips without starting even when start is set
    Given `[qa] start = "python3 app.py"` and `cmd` is empty
    When the `qa` gate runs
    Then the result is skipped with summary exactly `skipped: no [qa] cmd configured`
    And no app process was started and no `.marestail/qa-app.log` was written by a start

  Scenario: start runs in [qa] cwd
    Given `app.py` exists only under `web/` (not at repo root)
    And `[qa] cwd = "web"`, `start = "python3 app.py"`, `ready = "/"`, `port = 3400`,
      and `cmd = "true"`
    When the `qa` gate runs
    Then the gate is ok (start resolved `app.py` from `cwd`, not from the repo root)
    And the app's process group is gone afterwards

  Scenario: only the qa tier starts the app
    Given `[qa] start` is set
    When `marestail gate --tier fast`, `--tier sonar`, or `--tier full` runs
    Then no app process is started (the `qa` gate is not in those tiers)
    And `marestail gate --tier qa` starts the app when `start` is set

  Scenario: app log path
    Given `[qa] start` is set
    When `marestail gate --tier qa` runs with `MARESTAIL_TASK` unset
    Then app stdout/stderr is written to `.marestail/qa-app.log`
    When `runner.run_pipeline` starts for a task whose stem is `t`
    Then the runner sets `os.environ["MARESTAIL_TASK"]` to `t` before steps run
      (export at run start next to `share_scope`; always, not only when scoped)
    And when that run invokes the qa gate (`Run.gates("qa")` or a Stop-hook
      `marestail gate --tier qa` that inherits the export)
    Then the app log is `.marestail/runs/t/qa-app.log`

  Scenario: cleanup on cmd timeout
    Given `[qa] start = "python3 app.py"`, `cmd = "sleep 30"`, and env `MARESTAIL_QA_CMD_TIMEOUT=1`
    When the `qa` gate runs
    Then `cmd` times out (exit/code path as today's `shell.run` timeout, timeout value 1)
    And the app's process group is gone afterwards

  Scenario: cleanup on interrupt
    Given `[qa] start = "python3 app.py"` and a long-running `cmd`
    When the gate process receives SIGINT while the app is up
    Then the app's process group is gone afterwards (no orphan listener on the chosen port)

  Scenario: QA prompt gains one line when start is set
    Given `[qa] start` is set
    When `prompts.worker_prompt` is built for the qa worker
    Then the prompt contains exactly one new sentence: `The app is started for the qa gate; its address is in MARESTAIL_APP_URL.`
    And with no `start` that sentence is absent

  Scenario: install template lists the new keys commented out
    When `marestail install` writes `marestail.toml` from `templates/marestail.toml`
    Then the `[qa]` section still has `cmd` and `cwd`
    And it includes commented-out keys `start`, `ready`, `port`, `ready_timeout`, and `env`

  Scenario: freeze and existing endpoints stay as they are
    Then for role `qa`, `qa/**` remains frozen (no new `freeze.allow` for qa)
    And `**/playwright.config.*` remains frozen for qa when that pattern is in freeze paths
    And `[qa] cmd`, `cwd`, the 3600 s timeout, and skip-on-empty-cmd are unchanged when `start` is absent
    And `python3 tools/test-qa-app.py` exits 0 with last line containing `ok`
    And existing `tools/test-*.py` keep their current pass/fail contracts (including `tools/test-perf.py` exit 1)

  Scenario: README documents the keys and qa-tier-only start
    Then the README `[qa]` / acceptance text names `start`, `ready`, `port`, `ready_timeout`, and `env`
    And it says the app is started only for the `qa` tier
    And the README Environment variables table names `MARESTAIL_TASK` and `MARESTAIL_QA_CMD_TIMEOUT`
