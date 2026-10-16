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
    Given a dotnet pair App/App.csproj and AppTests/AppTests.csproj, [dotnet] mutation_scope "all", and dotnet tool restore exits 0
    And stryker writes a report whose only mutant is in App/A.cs: status S, mutatorName "Equality", replacement "a != b", location.start.line 3
    When S is CompileError
    Then cs.mutation is not ok, the summary is "1 of 1 mutants not killed" and the findings are exactly "App/A.cs:3 Equality CompileError: a != b"
    When S is Timeout and [dotnet] has no mutation_timeouts
    Then cs.mutation is ok, the summary is "all 1 mutants killed (1 killed by timeout)" and there are no findings
    When S is Timeout and [dotnet] mutation_timeouts = "fail"
    Then cs.mutation is not ok, the summary is "1 of 1 mutants not killed" and the findings are exactly "App/A.cs:3 Equality Timeout: a != b"
    And that summary does not contain "killed by timeout"

  Scenario: Elixir forgives a timeout unless the section says fail
    Given an elixir root with lib/app.ex and [elixir] mutation_scope = "all", so no base note is added
    And MARESTAIL_REMOTE is 0 and "mix help muex" exits 0
    And the muex run exits 0 and prints {"mutations": [{"location": {"file": "lib/app.ex", "line": 4}, "mutator": "Elixir.Muex.Mutators.Arithmetic", "status": "timeout", "description": "plus"}]}
    When [elixir] has no mutation_timeouts
    Then ex.mutation is ok, the summary is "all 1 mutants killed (1 killed by timeout)" and there are no findings
    When [elixir] mutation_timeouts = "fail"
    Then ex.mutation is not ok and the summary is "1 of 1 mutants not killed"
    And the findings are exactly "lib/app.ex:4 Arithmetic timeout: plus"
    And the summary does not contain "killed by timeout"

  Scenario: Java, Rust and Erlang do not grow a timeout phrase
    Given MARESTAIL_REMOTE is 0
    And a maven root with a pom.xml that declares pitest-maven, src/main/java/app/App.java, src/test/java/app/AppTest.java and [java] mutation_scope = "all"
    And PIT writes mutations.xml with one mutation: status TIMED_OUT, sourceFile App.java, mutatedClass app.App, mutatedMethod run, lineNumber 5, mutator VoidMethodCallMutator, description "removed call to helper"
    Then java.mutation is ok, the summary is "all 1 mutants killed" and there are no findings
    And [java] mutation_timeouts = "fail" gives the same result
    Given a crate with src/lib.rs, "cargo mutants --version" exits 0 and the gate runs without --scope changed
    And outcomes.json is a Baseline outcome with summary "Success" and one outcome whose scenario is {"Mutant": {"file": "src/lib.rs", "name": "src/lib.rs:5:5: replace + with * in add", "span": {"start": {"line": 5, "column": 5}}, "function": {"function_name": "add"}}} and whose summary is "Timeout"
    Then rs.mutation is ok, the summary is "all 1 mutants killed" and there are no findings
    And [rust] mutation_timeouts = "fail" gives the same result
    Given src/a.erl, test/a_tests.erl and [erlang] mutation_scope = "all"
    And the generated manifest holds one mutant: src/a.erl line 3, operator comparison, "<" replaced by ">="
    And the sources, the tests and the mutant compile, the unmutated eunit run exits 0, and the mutant's eunit run exits 124
    Then er.mutation is ok, the summary is "all 1 mutants killed" and there are no findings
    And [erlang] mutation_timeouts = "fail" gives the same result

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
    And gcloud prints nothing for "auth print-access-token", so nothing reaches the network
    And the ssh job command is the one containing "flock /var/lib/marestail/job.lock"
    When gcloud describe prints a TERMINATED instance and gcloud start exits 1 with stderr "PERMISSION_DENIED"
    Then the gate exits 0 and the summary contains "all 2 mutants killed (1 killed by timeout) (remote unavailable: PERMISSION_DENIED; ran locally)"
    And mutmut's arguments are "run --max-children 4"
    And remote-usage.jsonl is absent
    When describe prints STOPPED and gcloud start exits 1 and prints nothing
    Then the summary contains "(remote unavailable: start failed; ran locally)" and mutmut's arguments are "run --max-children 4"
    When describe prints TERMINATED, gcloud start exits 0, and every later describe prints RUNNING with natIP 1.2.3.4
    Then the summary contains "all 2 mutants killed (1 killed by timeout) (on marestail-mutation, 0s)" and the local mutmut did not run
    And gcloud ran "compute instances start marestail-mutation --project=proj --zone=europe-west1-b --quiet" once and describe twice
    And remote-usage.jsonl is two lines: first {"ts", "event": "vm-start", "instance": "marestail-mutation", "usd_per_hour": 1.2} with keys in that order, then the job line with result "ok"
    When the first describe prints SUSPENDED and the rest print RUNNING
    Then gcloud ran "compute instances resume marestail-mutation …" and not "start", and the usage file again starts with a vm-start line
    When the first two describes print STOPPING, the third TERMINATED, and the rest RUNNING
    Then the gate sleeps 5 seconds once (between the second and third describe), runs start once, describes four times and the summary contains "(on marestail-mutation, 0s)"
    And SUSPENDING is waited for the same way, and the wait is not counted in the job's seconds
    When describe prints RUNNING but the ssh readiness probe "sg docker -c 'docker exec mt true'" never exits 0
    Then after BOOT_WAIT (240 seconds, which a test may shorten) the summary contains "(remote unavailable: ssh never came up; ran locally)"
    When gcloud describe prints RUNNING with natIP 1.2.3.4, every ssh exits 0 at once and the rsync whose last argument ends in "/mutants/" writes the same meta file there
    Then the summary contains "all 2 mutants killed (1 killed by timeout) (on marestail-mutation, 0s)"
    And the local mutmut did not run
    And the ssh job command contains "max-children 8", "docker exec mt", "docker exec -w", " mt setsid", "flock /var/lib/marestail/job.lock", "/var/lib/marestail/busy.d/" and "npm ci --no-audit --no-fund --loglevel=error"
    And remote-usage.jsonl is one line whose keys are, in order, ts, event, instance, gate, repo, result, seconds, usd_per_hour, usd
    And event is "job", instance is "marestail-mutation", gate is "py.mutation", result is "ok", seconds is 0, usd_per_hour is 1.2, usd is 0.0
    And ts matches "^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$" and repo is the fixture root
    When MARESTAIL_REMOTE is 0 and the same toml exists
    Then the summary is "all 2 mutants killed (1 killed by timeout)" and contains neither "(on " nor "remote unavailable"
    When MARESTAIL_REMOTE is unset, the box would describe as RUNNING, and the toml is the same except for 'gates = ["ts.mutation"]'
    Then the gate exits 0 and the summary is exactly "all 2 mutants killed (1 killed by timeout)", with neither "(on " nor "remote unavailable"
    And mutmut's arguments are "run --max-children 4"
    And gcloud, ssh and rsync were never called
    And remote-usage.jsonl is absent
    When the box is RUNNING but rsync exits 1 with stderr "connection reset"
    Then the summary contains "(remote sync failed: connection reset; ran locally)", mutmut's arguments are "run --max-children 4" and remote-usage.jsonl is absent
    When the box is RUNNING, the job ssh exits 255 and prints nothing, and every gcloud describe after the first exits 1
    Then the exit code is 1 and the result line starts "[FAIL] py.mutation    mutmut failed (connection to marestail-mutation dropped mid-run and it did not come back: instance is unknown)  ("
    And the output ends with "GATE FAILED: py.mutation" and the local mutmut did not run
    And remote-usage.jsonl is one job line with result "dropped", seconds 0 and usd 0.0
    When the box stays RUNNING and the job ssh exits 255 and prints nothing both times it runs
    Then the exit code is 1 and the result line starts "[FAIL] py.mutation    mutmut failed (connection to marestail-mutation dropped twice mid-run; not rerun locally)  ("
    And the job ssh ran twice and the local mutmut did not run
    And remote-usage.jsonl is one job line with result "dropped"
    When the box stays RUNNING, the first job ssh exits 255 and prints nothing, and the second exits 0
    Then the exit code is 0 and the summary contains "all 2 mutants killed (1 killed by timeout) (on marestail-mutation, 0s)"
    And the job ssh ran twice, describe ran twice and the local mutmut did not run
    And remote-usage.jsonl is one job line with result "ok"
    When the box is RUNNING and the job ssh exits 1 and prints "boom"
    Then the exit code is 1 and the output contains
      """
      [FAIL] py.mutation    mutmut failed (on marestail-mutation, 0s)
             boom
      """
    And the output ends with "GATE FAILED: py.mutation", the job ssh ran once and the local mutmut did not run
    And remote-usage.jsonl is one job line with result "exit 1", seconds 0 and usd 0.0

  Scenario: remote status prints the box, the jobs and the spend, or says it is not configured
    Given HOME is /tmp/qa-020a-empty and that directory has no remote.toml
    When I run "marestail remote status"
    Then the exit code is 1 and stdout is "no remote configured (/tmp/qa-020a-empty/.config/marestail/remote.toml)\n"
    Given a fresh HOME with the toml from the offload scenario and no remote-usage.jsonl
    And the fake gcloud describe prints RUNNING 1.2.3.4 and prints nothing for "auth print-access-token", so spend is unknown and nothing reaches the network
    And the status ssh is the one whose command contains "marestail-idle-check", and it prints
      """
      /repo/web stryker 01:02 running
      idle 120 15
      """
    When I run "marestail remote status"
    Then the exit code is 0 and stdout is
      """
      marestail-mutation (proj/europe-west1-b): RUNNING 1.2.3.4
      offloaded gates: py.mutation
        running: stryker for web, 01:02
      today: VM up unknown; 0 jobs, 0.00h busy
      this month: VM up unknown; 0 jobs, 0.00h busy
      """
    And no line contains "no jobs:"
    When the status ssh prints the same two lines but exits 1
    Then the exit code is 0 and stdout is
      """
      marestail-mutation (proj/europe-west1-b): RUNNING 1.2.3.4
      offloaded gates: py.mutation
      today: VM up unknown; 0 jobs, 0.00h busy
      this month: VM up unknown; 0 jobs, 0.00h busy
      """
    When every gcloud describe exits 1
    Then the exit code is 0 and stdout is
      """
      marestail-mutation (proj/europe-west1-b): unknown
      offloaded gates: py.mutation
      today: VM up unknown; 0 jobs, 0.00h busy
      this month: VM up unknown; 0 jobs, 0.00h busy
      """
    And the first line has no trailing space and no ssh was run
    When ssh instead prints only "idle 120 15"
    Then the third line matches "^  no jobs: idle for 2 min, powers itself off after 15 idle min \(about \d{2}:\d{2}\)$"
    And the running line is absent
    Given remote-usage.jsonl holds, stamped with the current UTC time, a job line with seconds 1800, a job line with seconds 5400 and a vm-start line, and a job line stamped "2000-01-01T00:00:00+00:00" with seconds 3600
    And ssh prints "/repo/web stryker 01:02 queued" and then "idle 120 15" on the next line
    When I run "marestail remote status"
    Then the third line is "  running: stryker for web, 01:02 (queued)" and no line contains "no jobs:"
    And the last two lines are "today: VM up unknown; 2 jobs, 2.00h busy" and "this month: VM up unknown; 2 jobs, 2.00h busy"

  Scenario: remote status prices uptime per instance when monitoring answers
    This is a test-level case: QA cannot fake the monitoring API at the UI.
    Given remote.CONFIG holds the offload scenario's toml (usd_per_hour 1.2) and remote.USAGE holds one job line stamped now for instance "old-box" with seconds 1800 and usd_per_hour 0.5
    And describe gives RUNNING 1.2.3.4 and the status ssh prints "idle 120 15"
    And the "gcloud auth print-access-token" subprocess prints "tok"
    And urllib.request.urlopen is faked to return {"timeSeries": [{"points": [{"value": {"doubleValue": 3600.0}}, {"value": {"doubleValue": 1800.0}}]}]}
    When status_command runs
    Then it returns 0 and the last two lines are "today: VM up 3.00h = $2.55; 1 jobs, 0.50h busy" and "this month: VM up 3.00h = $2.55; 1 jobs, 0.50h busy"
    And urlopen was called four times: old-box then marestail-mutation for today, then the same for this month
    And each request is to "https://monitoring.googleapis.com/v3/projects/proj/timeSeries", with header Authorization "Bearer tok" and timeout 20
    And its query has filter 'metric.type="compute.googleapis.com/instance/uptime" AND metric.labels.instance_name="<instance>"', aggregation.perSeriesAligner "ALIGN_SUM", interval.startTime today's or the month's first UTC midnight as "YYYY-MM-DDT00:00:00Z", and aggregation.alignmentPeriod the window in whole seconds, at least 60, followed by "s"
    When urlopen raises OSError, or returns a body that is not JSON
    Then both lines read "VM up unknown" and the job counts stay "1 jobs, 0.50h busy"

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
    And a test that exercises the box monkeypatches remote.gcloud, remote.ssh, remote.rsync, remote.describe and remote.ensure_up as it needs
    And a test that reaches status_command also fakes remote.uptime_seconds, or its "gcloud auth print-access-token" subprocess and urllib.request.urlopen, because uptime_seconds calls neither remote.gcloud nor remote.ssh
    And the urlopen branch of uptime_seconds is covered with the fake response of the pricing scenario, never a real request
    And such a test points remote.CONFIG, remote.USAGE and remote.LOCK at files under tmp_path, so ensure_up never locks ~/.config/marestail/remote.lock, which a live marestail on this machine holds while it offloads
    And the hermetic guard in tests/conftest.py still refuses sockets
    When the whole of tests/ runs with HOME set to a fresh empty directory and PATH starting with gcloud, ssh and rsync tripwires that append their arguments to a log and exit 99
    Then pytest passes, the log is empty and the fresh HOME has no .config/marestail
