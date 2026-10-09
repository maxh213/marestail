Feature: The merge of main is green

  No behaviour changes. `marestail gate --tier full` passes on this repo again.
  The seven tools scripts still pass. Mutation verdicts, the remote box
  protocol and the help text stay as the merged code has them. This repo's
  `marestail.toml` has python, sonar, deadcode, docs and comments only, so
  the full tier runs those gates and not the other languages.
  `marestail status` and `marestail queue` are not commands.
  `tests/conftest.py` keeps `os.environ["MARESTAIL_REMOTE"] = "0"`.

  Scenario: the full gate passes
    When I run "marestail gate --tier full" in this checkout with the venv active
    Then the exit code is 0
    And the last line is "GATE PASSED"
    And the result lines, in order, are py.tests, py.crap, py.lint, py.deps, py.runtime, comments, depth, deadcode, docs, py.mutation, sonar
    And every result line matches "^\[(ok  |FAIL)\] .{14}.+  \(\d+\.\d+s\)$"
    And every result line starts with "[ok  ]"
    And passing this command is the proof that ruff, mypy --strict, coverage and mutant killing are clean
    And the functions over complexity 4 are split: playwright_failures, playwright_spec_findings, count_tests, run_mutation, ensure_up, status_command, running_jobs, walk_beds and remote_label
    And deadcode is clean, so offloads in marestail/remote.py and has_marestail in marestail/tui/collect.py are called or gone

  Scenario: the scripts that pin the merged features still pass
    When I run each script below with the repo on PYTHONPATH
    Then each exits 0 and its last non-empty line is the text shown
      | tools/test-route.py | route ok |
      | tools/test-agent-backends.py | agent backends ok |
      | tools/test-watch.py | watch ok |
      | tools/test-nice.py | nice ok |
      | tools/test-practices.py | practices ok |
      | tools/test-muex-watchdog.py | muex watchdog ok |
      | tools/test-ts-playwright.py | ts playwright unit assertions OK |

  Scenario: a Python timeout is killed unless mutation_timeouts is fail
    Given a repo whose marestail.toml is "[git]\nbase = \"main\"\n[python]\nroot = \".\"\nvenv = \".venv\"\nmutation_scope = \"all\"\nmutation_workers = 4\n" and pkg/a.py exists
    And .venv/bin/mutmut writes mutants/pkg.a.meta as {"exit_code_by_key": {"pkg.a.f__mutmut_1": 1, "pkg.a.f__mutmut_2": 36}} and exits 0
    And MARESTAIL_REMOTE is 0
    When I run "marestail gate --only py.mutation --tier full"
    Then the exit code is 0 and one result line matches "^\[ok  \] py\.mutation    all 2 mutants killed \(1 killed by timeout\)  \(\d+\.\ds\)$"
    And the output ends with "GATE PASSED"
    And mutmut's arguments are "run --max-children 4"
    When the same toml also has mutation_timeouts = "fail"
    Then the exit code is 1 and the output contains
      """
      [FAIL] py.mutation    1 of 2 mutants not killed
      """
    And the next line is "       pkg.a.f__mutmut_2: timeout"
    And the output ends with "GATE FAILED: py.mutation"
    And the output does not contain "killed by timeout"

  Scenario: TypeScript CompileError is killed and Timeout is counted on its own
    Given a ts repo with mutation_scope "all" and no tooling directory, so stryker is started as npx
    And the npx stub writes reports/mutation/mutation.json and exits 0
    And that report has src/a.ts mutants CompileError at line 2 and Timeout at line 4, both mutator BooleanLiteral and replacement "x"
    When the ts.mutation gate runs with MARESTAIL_REMOTE=0
    Then it is ok and the summary is "all mutants killed (1 killed by timeout)"
    And the findings do not contain "CompileError"
    When the toml has [ts] mutation_timeouts = "fail" and the same report
    Then it is not ok and the summary is "1 surviving mutants"
    And the findings are exactly "src/a.ts:4 BooleanLiteral Timeout: x"
    And the summary does not contain "killed by timeout"

  Scenario: C# still fails a CompileError and forgives a Timeout the same way
    Given a dotnet pair that gets past the stryker preconditions and a report of one mutant
    When the mutant status is CompileError
    Then cs.mutation is not ok, the summary is "1 of 1 mutants not killed" and the finding contains "CompileError"
    When the mutant status is Timeout and [dotnet] mutation_timeouts is not "fail"
    Then cs.mutation is ok and the summary is "all 1 mutants killed (1 killed by timeout)"
    When the mutant status is Timeout and [dotnet] mutation_timeouts = "fail"
    Then cs.mutation is not ok, the summary is "1 of 1 mutants not killed" and the finding contains "Timeout"
    And that summary does not contain "killed by timeout"

  Scenario: Elixir forgives a timeout unless the section says fail
    Given mix muex prints a JSON report of one mutation, file lib/app.ex, line 4, mutator "Elixir.Muex.Mutators.Arithmetic", status "timeout", description "plus"
    When [elixir] mutation_timeouts is not "fail"
    Then ex.mutation is ok and the summary is "all 1 mutants killed (1 killed by timeout)"
    When [elixir] mutation_timeouts = "fail"
    Then ex.mutation is not ok and the summary is "1 of 1 mutants not killed"
    And the finding is "lib/app.ex:4 Arithmetic timeout: plus"
    And the summary does not contain "killed by timeout"

  Scenario: Java, Rust and Erlang do not grow a timeout phrase
    Given a PIT mutations.xml whose only mutation has status TIMED_OUT
    Then java.mutation is ok and the summary is "all 1 mutants killed" and does not contain "killed by timeout"
    And [java] mutation_timeouts = "fail" leaves that summary unchanged
    Given a cargo-mutants outcomes.json whose only mutant summary is Timeout
    Then rs.mutation is ok and the summary is "all 1 mutants killed" and does not contain "killed by timeout"
    Given an Erlang mutant whose status is timeout
    Then er.mutation is ok and the summary is "all 1 mutants killed" and does not contain "killed by timeout"

  Scenario: an offloaded mutation names the box or says why it ran locally
    Given HOME has .config/marestail/remote.toml
      """
      [remote]
      enabled = true
      instance = "marestail-mutation"
      project = "proj"
      zone = "europe-west1-b"
      gates = ["py.mutation"]
      workers = { "py.mutation" = 8 }
      usd_per_hour = 1.2
      user = "max"
      container = "mt"
      """
    And gcloud, ssh and rsync on PATH are fakes, and the Python fixture above is the repo
    When gcloud describe prints a TERMINATED instance and gcloud start exits 1 with stderr "PERMISSION_DENIED"
    Then the gate exits 0 and the summary contains "all 2 mutants killed (1 killed by timeout) (remote unavailable: PERMISSION_DENIED; ran locally)"
    And mutmut's arguments are "run --max-children 4"
    And remote-usage.jsonl is absent
    When gcloud describe prints RUNNING with natIP 1.2.3.4, ssh exits 0 at once and rsync deposits the same meta file
    Then the summary contains "all 2 mutants killed (1 killed by timeout) (on marestail-mutation, 0s)"
    And the ssh command contains "max-children 8", "docker exec mt", "flock /var/lib/marestail/job.lock", "/var/lib/marestail/busy.d/" and "npm ci --no-audit --no-fund --loglevel=error"
    And remote-usage.jsonl is one line whose keys start ts, event, instance, gate, repo, result, seconds, usd_per_hour, usd
    And event is "job", instance is "marestail-mutation", gate is "py.mutation", result is "ok", seconds is 0, usd_per_hour is 1.2, usd is 0.0
    And ts matches "^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$" and repo is the fixture root
    When MARESTAIL_REMOTE is 0 and the same toml exists
    Then the summary is "all 2 mutants killed (1 killed by timeout)" and contains neither "(on " nor "remote unavailable"
    When the box is RUNNING but rsync exits 1 with stderr "connection reset"
    Then the summary contains "(remote sync failed: connection reset; ran locally)" and mutmut still runs locally
    When the job ssh exits 255 with empty output and the next gcloud describe exits 1
    Then the summary is "no mutants were generated (connection to marestail-mutation dropped mid-run and it did not come back: instance is unknown)"
    When the job ssh exits 255 with empty output twice and the box stays RUNNING
    Then the summary is "no mutants were generated (connection to marestail-mutation dropped twice mid-run; not rerun locally)"

  Scenario: remote status prints the box, the jobs and the spend, or says it is not configured
    Given HOME is /tmp/qa-020a-empty and that directory has no remote.toml
    When I run "marestail remote status"
    Then the exit code is 1 and stdout is "no remote configured (/tmp/qa-020a-empty/.config/marestail/remote.toml)\n"
    Given the toml from the offload scenario and a fake gcloud whose describe prints RUNNING 1.2.3.4
    And ssh prints "/repo/web stryker 01:02 running"
    And the monitoring request fails, as it does for a fake access token
    When I run "marestail remote status"
    Then stdout is
      """
      marestail-mutation (proj/europe-west1-b): RUNNING 1.2.3.4
      offloaded gates: py.mutation
        running: stryker for web, 01:02
      today: VM up unknown; 0 jobs, 0.00h busy
      this month: VM up unknown; 0 jobs, 0.00h busy
      """
    When ssh instead prints "idle 120 15"
    Then the third line matches "^  no jobs: idle for 2 min, powers itself off after 15 idle min \(about \d{2}:\d{2}\)$"
    And the running line is absent

  Scenario: help lists remote, and status and queue stay absent
    When I run "marestail --help"
    Then the exit code is 0
    And stdout contains "{gate,run,tasks,install,sonar,watch,perf,visual,route,remote,graph,depth}"
    And stdout contains "remote              the remote box that offloaded gates run on"
    When I run "marestail remote"
    Then the exit code is 2 and stderr contains "the following arguments are required: remote_command"
    When I run "marestail remote --help"
    Then stdout contains "status    box state, uptime and estimated spend today and this month"
    When I run "marestail status"
    Then the exit code is 2 and stderr contains "invalid choice: 'status'"
    When I run "marestail queue"
    Then the exit code is 2 and stderr contains "invalid choice: 'queue'"
    When I run "marestail route --help" with MARESTAIL_DANDELION unset and PATH set to "/usr/bin:/bin"
    Then the exit code is 127 and stderr contains "dandelion is not installed"

  Scenario: a run copies its terminal text into pipeline.log
    Given a committed git repo on branch main whose tasks/t.md is "# t\n" and whose marestail.toml disables practices and perf and sets base to "main"
    And PATH is /usr/bin:/bin, so claude is absent
    When I run "marestail run tasks/t.md --from specifier --to specifier --auto --retries 1 --model m --agent claude"
    Then the exit code is 1 and stderr is empty
    And stdout contains "pipeline stopped at specifier" and "missing handoff .marestail/handoffs/t/01-specifier.md"
    And .marestail/runs/t/pipeline.log is byte for byte the same as stdout

  Scenario: install copies the Python rulebook only for a Python target
    Given an existing directory whose only file is an empty pyproject.toml
    When I run "marestail install <that directory>"
    Then guidance/py.md exists and contains "## Python-native idioms"
    And stdout ends with "installed into <that directory>; edit marestail.toml and sonar-project.properties\n"
    Given an existing empty directory
    When I run "marestail install <that directory>"
    Then guidance/py.md does not exist

  Scenario: docs match the code without new rulebooks in this repo
    When I read the "Environment variables" section of "README.md"
    Then it lists `USER` as the ssh user on the box when remote.toml has no user key, and `max` when USER is unset
    And it still lists every variable features/000-green-the-repo.feature names
    When I run "marestail gate --tier fast --only docs"
    Then the exit code is 0 and the output ends with "GATE PASSED"
    And this repo's guidance/ still contains only guidance/ts.md and guidance/cs.md
    And the shipped rulebooks stay templates/guidance/py.md, templates/guidance/rb.md, templates/guidance/er.md, templates/guidance/ex.md and templates/guidance/go.md
    And README no longer cites a missing backtick path such as guidance/py.md

  Scenario: helpers the merge removed stay removed
    Then marestail.tui.collect has no list_dirs, self_repo or log_name
    And marestail.gates.ts_tests has no failures or empty_failures

  Scenario: the suite does not call the real box
    Given MARESTAIL_REMOTE is 0, off, false or no
    Then remote settings are ignored even if ~/.config/marestail/remote.toml exists
    And a test that exercises the box monkeypatches gcloud, ssh, rsync, describe and ensure_up
    And points remote.CONFIG and remote.USAGE at files under tmp_path
    And never reads the real remote.toml or remote-usage.jsonl
