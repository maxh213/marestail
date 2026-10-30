Feature: two runs of one repo share a machine

  `MARESTAIL_RUN_SUFFIX` or `--run-suffix` lets two runs of one repo share a
  machine, each in its own checkout, without editing a tracked file. A suffix
  matches `[a-z0-9-]{1,32}`. Names gain `_<suffix>` and the Sonar project name
  gains ` (<suffix>)`. The separator is `_` because a suffix cannot contain `_`.

  Suffixed: the Sonar project key and name, perf helper, tree containers,
  golden containers, work directories, and perf host ports. Not suffixed: the
  SonarQube container `marestail-sonarqube`, port 9000, its volumes, credentials
  and plugins under `~/.config/marestail/`, the volume `marestail-perf-pgdata`,
  golden names and their status and log files, `perf-db.json`, the database
  name `bench`, the QA port, detached perf worktrees, mutation directories,
  hook files, and anything under `.marestail/`. `sonar up` and `sonar down`
  never read the suffix. With the variable unset and no flag, every command
  line, container name, port, Sonar key, API call and printed line stays
  byte-identical to today.

  `run_suffix.py` imports only `os` and `re` at module level. `argument`
  imports `argparse` inside the function. `current()` reads the environment on
  every call. Empty is not unset. `named` and `titled` call `current()` again.

  `jobs.run_jobs` and the task read stay where they are. `current()` is called
  once, immediately after `nice.apply` and before `jobs.run_jobs`. A bad nice
  raises first. A bad suffix raises before jobs and before the task is read.
  `run_pipeline`'s signature does not change. The flag is set on the
  environment by the `run`, `gate` and `sonar` handlers before they do anything
  else, and only when the flag is present. `gate` checks the suffix before
  `--hook` and before config is loaded. `prune(key)` does not read the
  environment and `sonar/setup.py` does not import `run_suffix`.

  The suffix is applied once. `proj_a1_a1` and `proj_a1 (a1)` never appear.
  Nothing under `.marestail/phases/` is edited. Event fields and status JSON
  keys do not gain a suffix. `tree_port("control")` still raises `KeyError`.

  Background:
    Given `MARESTAIL_RUN_SUFFIX` is unset unless a step sets it
    And the autouse `hermetic` fixture in `tests/conftest.py` also runs `monkeypatch.delenv("MARESTAIL_RUN_SUFFIX", raising=False)` beside the `MARESTAIL_JOBS` line

  Scenario: an unset suffix leaves names alone
    When `current()` is called
    Then it returns None
    And `named("k")` returns `k`
    And `titled("K")` returns `K`
    When the variable is then set to `a1` and `current()` is called again
    Then it returns `a1`
    When the variable is deleted and `current()` is called again
    Then it returns None

  Scenario Outline: a valid suffix is appended once
    When `MARESTAIL_RUN_SUFFIX` is `<suffix>`
    Then `current()` returns `<suffix>`
    And `argument("<suffix>")` returns `<suffix>`
    And `named("k")` returns `<named>`
    And `titled("K")` returns `<titled>`

    Examples:
      | suffix                           | named                               | titled                               |
      | a1                               | k_a1                                | K (a1)                               |
      | a                                | k_a                                 | K (a)                                |
      | 0                                | k_0                                 | K (0)                                |
      | -                                | k_-                                 | K (-)                                |
      | a-1-b                            | k_a-1-b                             | K (a-1-b)                            |
      | aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa | k_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa | K (aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) |

  Scenario Outline: a bad suffix is a SystemExit and the flag is an argument error
    When `MARESTAIL_RUN_SUFFIX` is the value whose Python repr is <shown>
    Then `current()` raises SystemExit "MARESTAIL_RUN_SUFFIX must be 1 to 32 characters from a-z, 0-9 and -, got <shown>"
    And `argument` of that same value raises argparse.ArgumentTypeError "must be 1 to 32 characters from a-z, 0-9 and -, got <shown>"
    And the SystemExit text is one line: the newline value's repr is the two characters `\` and `n`, not a line break

    Examples:
      | shown                               |
      | ''                                  |
      | 'A1'                                |
      | 'a_1'                               |
      | 'a.1'                               |
      | 'a/b'                               |
      | 'a 1'                               |
      | 'a1\n'                              |
      | 'é'                                 |
      | 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' |

  Scenario: the flag wins and a bad flag exits 2
    Given `MARESTAIL_RUN_SUFFIX` is `zz`
    When `marestail run t.md --from coder --to qa --auto --model m --retries 2 --agent grok --effort high --scope hard --focus a --focus b --run-suffix a1` runs
    Then `run_pipeline` is called with `(Path("t.md"), "coder", "qa", True, "m", 2, "grok", "high", "hard", ["a", "b"])`
    And `os.environ["MARESTAIL_RUN_SUFFIX"]` is `a1` while it is called
    When `marestail run t.md` runs with the variable still `zz` and no flag
    Then `run_pipeline` is called and the variable is still `zz`
    When `marestail run t.md --run-suffix A1` runs
    Then the exit code is 2, `run_pipeline` is not called, and stderr ends with `argument --run-suffix: must be 1 to 32 characters from a-z, 0-9 and -, got 'A1'\n`
    And `marestail gate --run-suffix A1` and `marestail sonar setup --run-suffix A1` exit 2 with that same ending
    And `marestail run --help`, `marestail gate --help` and `marestail sonar --help` mention `--run-suffix`
    And `marestail perf --help`, `marestail queue --help` and `marestail status --help` do not

  Scenario: the pipeline checks the suffix after nice and before jobs
    When `run_pipeline` runs a valid task with the variable unset
    Then the recorded order is `nice`, `suffix`, `jobs`, `read`, `pick_model`
    And stdout does not contain `MARESTAIL_RUN_SUFFIX`
    When `MARESTAIL_NICE` is `high` and `MARESTAIL_RUN_SUFFIX` is `A1`
    Then the SystemExit is `nice must be an integer 0-19, got 'high'`
    And the recorded order is `nice`
    When `MARESTAIL_NICE` is unset and `MARESTAIL_RUN_SUFFIX` is `A1`
    Then the SystemExit is `MARESTAIL_RUN_SUFFIX must be 1 to 32 characters from a-z, 0-9 and -, got 'A1'`
    And the recorded order is `nice`, `suffix`
    And jobs is not called, the task is not read, and stdout is empty
    When `MARESTAIL_JOBS` is `0` and `MARESTAIL_RUN_SUFFIX` is `A1`
    Then the SystemExit is that same suffix message and jobs is not called
    When `MARESTAIL_JOBS` is `0` and the suffix is unset
    Then the SystemExit is `MARESTAIL_JOBS must be a whole number 1 or more, got '0'`
    And the recorded order is `nice`, `suffix`, `jobs`
    And these existing orders gain `suffix` after every `nice`:
      | test | order |
      | test_run_pipeline_applies_nice_once_before_pick_model | nice, suffix, jobs, read, pick_model |
      | test_run_pipeline_reads_jobs_once_before_pick_model | nice, suffix, jobs, read, pick_model |
      | test_run_pipeline_raises_a_bad_jobs_before_the_task_is_read | nice, suffix, jobs |
      | test_run_pipeline_reports_a_bad_model_when_nice_is_valid | nice, suffix, jobs, read, pick_model |
      | test_run_pipeline_stops_on_a_broken_task_before_the_model | nice, suffix, jobs, read |
      | test_run_pipeline_accepts_a_valid_block_without_its_dependency | nice, suffix, jobs, read, pick_model, nice, suffix, jobs, read, pick_model |
      | test_run_pipeline_lets_a_missing_task_raise | nice, suffix, jobs |
    And `test_run_pipeline_raises_a_bad_nice_before_steps` and `test_run_pipeline_raises_a_bad_nice_before_a_bad_model` stay `nice`
    And outside a repo, `marestail run t.md` with `MARESTAIL_RUN_SUFFIX=A1` still raises `no marestail.toml found above` before the suffix message

  Scenario: gate rejects a bad suffix before the hook, the clash and the gates
    Given `MARESTAIL_RUN_SUFFIX` is `zz`
    When `marestail gate --run-suffix a1 --tier fast` runs
    Then `run_gates_with_context` is called and the variable is `a1` at that call
    When `MARESTAIL_RUN_SUFFIX` is `A1` and the command is `marestail gate --tier fast`
    Then the SystemExit is `MARESTAIL_RUN_SUFFIX must be 1 to 32 characters from a-z, 0-9 and -, got 'A1'`
    And no gate runs
    When the command is `marestail gate --hook` with that same bad value and stdin `{}`
    Then the SystemExit is that same message and the hook is not handled
    When the command is `marestail gate --scope all --focus a.py` with that same bad value
    Then the SystemExit is that same message, not `--focus cannot be combined with --scope all`
    When the command is `marestail gate --scope all --focus a.py --run-suffix a1`
    Then the exit code is 2 and stderr is `--focus cannot be combined with --scope all\n`
    And `marestail gate --tier nope` with `MARESTAIL_RUN_SUFFIX=A1` still exits 2, and stderr contains `invalid choice: 'nope'` and not the suffix message
    And outside a repo, `marestail gate` with that bad value raises the suffix message, not `no marestail.toml found above`

  Scenario: sonar setup suffixes the key and the name, and up and down do not
    Given a repo directory named `repo` and `[sonar] project_key = "key"`
    When `marestail sonar setup` runs with `MARESTAIL_RUN_SUFFIX=a1`
    Then `setup.setup` is called with `("key_a1", "repo (a1)")`
    When the file also has `project_name = "N"`
    Then `setup.setup` is called with `("key_a1", "N (a1)")`
    When `marestail sonar --run-suffix a1 setup` runs with the variable `zz` and `project_key = "key"`
    Then `setup.setup` is called with `("key_a1", "repo (a1)")`
    When the suffix is `a1` and there is no `project_key`
    Then the SystemExit is `marestail.toml:1 [sonar] project_key is required when MARESTAIL_RUN_SUFFIX is set`
    And `setup.setup` is not called
    When the suffix is unset, `test_sonar_setup` still calls `("key", "repo")` and `(None, "N")`
    When `marestail sonar up` and `marestail sonar down` run with the suffix `a1`, as the environment or as `--run-suffix a1`
    Then `setup.up` and `setup.down` are called with no arguments, as they are without a suffix
    And `MARESTAIL_RUN_SUFFIX=A1 marestail sonar up` still calls `setup.up`
    And `setup.DEFAULT_URL` is `http://localhost:9000`
    And `sonar/docker-compose.yml` still contains `container_name: marestail-sonarqube` and `"9000:9000"`

  Scenario: sonar prune deletes only the suffixed project
    Given `SONAR_ACTIONS` is `("up", "down", "setup", "prune")`
    And `[sonar] project_key = "key"` in a repo named `repo`
    When `marestail sonar prune` runs with neither flag nor variable
    Then the SystemExit is `sonar prune needs a run suffix: pass --run-suffix or set MARESTAIL_RUN_SUFFIX; it never deletes the unsuffixed project`
    And `setup.prune` is not called
    When the variable is `A1` and there is no flag
    Then the SystemExit is `MARESTAIL_RUN_SUFFIX must be 1 to 32 characters from a-z, 0-9 and -, got 'A1'`
    When `marestail sonar prune --run-suffix a1` runs with the variable `zz`
    Then `setup.prune` is called with `"key_a1"` and `setup.setup` is not called
    When the variable is `b2` and there is no flag
    Then `setup.prune` is called with `"key_b2"`
    When the suffix is `a1` and there is no `project_key`
    Then the SystemExit is `marestail.toml:1 [sonar] project_key is required when MARESTAIL_RUN_SUFFIX is set`
    When `prune("key_a1")` runs and `credentials()` is None
    Then the SystemExit is `sonar is not set up; run: marestail sonar setup`
    And no HTTP request is made
    When search returns `{"components": [{"key": "key_a1"}]}`
    Then there is one GET `api/projects/search` with `projects=key_a1`
    And one POST `api/projects/delete` with `project=key_a1`
    And stdout is `deleted sonar project key_a1\n`
    And the exit code is 0
    When search returns `{"components": []}`
    Then there is no POST, stdout is `no sonar project key_a1\n`, and the exit code is 0
    When search raises HTTP 401 with body `no`
    Then the SystemExit is `sonar GET api/projects/search?projects=key_a1: 401 no`
    And `prune` does not start or stop the server

  Scenario: the scanner and the gate use the suffixed project once
    When `scanner_command(context, creds, "proj")` runs with `MARESTAIL_RUN_SUFFIX=a1` and no project name
    Then the argument list is today's list except `-Dsonar.projectKey=proj_a1` is followed by `-Dsonar.projectName=proj (a1)`
    When `sonar-project.properties` contains `sonar.projectName=Props`
    Then the name argument is `-Dsonar.projectName=Props (a1)`
    When `[sonar] project_name = "Nice"` and the properties file still says `Props`
    Then the name argument is `-Dsonar.projectName=Nice (a1)`
    When the suffix is unset and the properties file says `sonar.projectName=Props`
    Then `test_scanner_command`'s list is unchanged and contains no `sonar.projectName` argument
    When a clean `run_gate` has `project_key = "proj"` and the suffix is `a1`
    Then the docker argv contains `-Dsonar.projectKey=proj_a1` and does not contain `proj_a1_a1`
    And every FakeClient get param named `projectKey`, `componentKeys`, `project` or `component` is `proj_a1`
    And the `api/ce/task` get is still `{"id": "T1"}`
    When the suffix is `a1`, credentials exist, and there is no `project_key`
    Then the result is `Result("sonar", False, "no project key", ["marestail.toml:1 [sonar] project_key is required when MARESTAIL_RUN_SUFFIX is set"])` with `seconds` `0.0`
    And `fake_run` saw no call and no client was constructed
    When credentials are missing, the suffix is `a1` and there is no `project_key`
    Then the result stays `("sonar", False, "not set up", ["run: marestail sonar setup"])`
    When `dotnet_scan` is given key `proj` and the suffix is `a1` and there is no project name
    Then the script contains `/k:"proj_a1"` and `/n:"proj (a1)"` and does not contain `proj_a1_a1` or `proj_a1 (a1)`
    When `project_name = "Nice"`
    Then the script contains `/k:"proj_a1"` and `/n:"Nice (a1)"`
    And with the suffix unset, `test_dotnet_scan_script` and `test_dotnet_scan_builds_project_when_solutions_are_ambiguous` stay byte-identical

  Scenario: perf names, ports and labels follow the suffix
    Given `make_db(tmp_path)` is today's database and `test_database_names` stays as it is
    When `make_db(tmp_path, suffix="a1")` builds `key`
    Then `helper` is `mp-files_a1`
    And `tree_container("head")` is `mp-db-<key>-head_a1`
    And `golden_container("g")` is `mp-golden-g_a1`
    And `work_dir("head")` is `/perf/work/<key>-head_a1`
    And `volume` is `vol`
    And `Database` takes `suffix` as its last field, defaulting to None, so `test_build_database_defaults` stays equal to today's `Database(...)`
    When `build_database` runs with `MARESTAIL_RUN_SUFFIX=a1` and no port setting
    Then `port` is `57376` and `suffix` is `a1`
    When the suffix is `b2`
    Then `port` is `58003`
    When the suffix is `slot-1`
    Then `port` is `56743`
    When `[perf.db] port` is `6000` and the suffix is `a1`
    Then `port` is `7944`
    When `MARESTAIL_PERF_DB_PORT=7000` and the suffix is `a1` and `[perf.db] port` is `6000`
    Then `port` is `7000`
    And `tree_port` of the `a1` database is `57376`, `57377`, `57378` for `head`, `baseline`, `pre-marestail`
    And the slot is `1 + int(hashlib.sha256(suffix.encode()).hexdigest()[:8], 16) % 1000`, added as `len(TREE_PORT_OFFSETS)` times that slot
    When the base port is `65535` and the suffix is `a1`
    Then `build_database` returns port `67479` and does not raise
    And `golden_name` is the same with `suffix` None and `suffix` `a1`
    When `start_postgres` and `ensure_helper` run with `suffix` `a1`
    Then `--label marestail.run-suffix=a1` is the argument immediately after `--name <name>`
    And with `suffix` None, `test_start_postgres` and `test_ensure_helper_starts` keep today's lists
    When docker replies exit 125 and `port is already allocated` for that run
    Then the error is `starting <container> failed (exit 125): port is already allocated`
    When `down` runs with `suffix` `a1` and docker prints `mp-files_a1\nmp-db-abc-head_a1\n`
    Then the list command is `docker ps -a --filter label=marestail.run-suffix=a1 --format {{.Names}}`
    And only those two names are removed, each with `docker rm -f -v`
    When `down` runs with `suffix` None and docker prints `mp-files\nmp-files_a1\nother\n`
    Then the list command is today's `docker ps -a --format {{.Names}}`
    And `mp-files` and `mp-files_a1` are removed and `other` is not
    And `test_down` stays `docker ps -a --format {{.Names}}`, then `docker rm -f -v mp-files` and `docker rm -f -v mp-db-1`
    When `url --tree head` runs under suffix `a1` with the frozen password
    Then stdout is `postgresql://postgres:pw24@127.0.0.1:57376/bench\n`
    When `release` runs with suffix `a1` and the helper is running
    Then it removes `mp-db-<key>-head_a1` and `mp-db-<key>-baseline_a1`
    And it execs `mp-files_a1` to remove `/perf/work/<key>-head_a1` and `/perf/work/<key>-baseline_a1`
    And `perf db` with no `migrate` still prints `configure [perf.db] migrate in marestail.toml` and does not read the suffix
    And `perf run` without `--db` does not read the suffix
    And `start_build`'s argv stays `perf db golden --tree <tree> --wait` with no `--run-suffix`
    And `tests/test_perf_trees.py` still sees `TEMP_PREFIX` `marestail-perf-` with the suffix set
    And the QA preferred port is still `3400` with the suffix set
    And `test_perf_parser_defaults_and_help` expects `down` help `remove this run's performance database containers (every one when MARESTAIL_RUN_SUFFIX is unset), keeping the volume`

  Scenario: commands that never read the suffix ignore a bad value
    Given `MARESTAIL_RUN_SUFFIX` is `A1`
    When `tasks check`, `status`, `abandon --help`, `watch --help`, `dash --help`, `visual --help`, `remote --help`, `graph`, `depth`, `route --help`, `install --help` and `queue --help` run
    Then none of them raises the suffix SystemExit
    And the queue child argv contains `run` and does not contain `--run-suffix`
    And with the variable `a1`, the queue child environment contains `MARESTAIL_RUN_SUFFIX=a1`
    And with the variable unset, the child environment does not gain that key
    And a run-start event has no `suffix` field, and status JSON keys are unchanged

  Scenario: the README and the layer say where the suffix lives
    Then the line after the `MARESTAIL_TASK` row is `| `MARESTAIL_RUN_SUFFIX` | per-run suffix (`[a-z0-9-]{1,32}`) for the names a run shares with the machine: Sonar project `<key>_<suffix>`, perf containers `…_<suffix>` and their ports; `--run-suffix` on `run`, `gate` and `sonar` sets it |`
    And the two lines after `marestail sonar setup        # local SonarQube in docker, token in ~/.config/marestail` are `marestail run tasks/001.md --run-suffix a1   # a second run of this repo on this machine, in its own checkout` and `marestail sonar prune --run-suffix a1   # delete the Sonar project that suffix created`
    And the first paragraph after the `## Use` code fence is `MARESTAIL_RUN_SUFFIX`, or `--run-suffix` on `run`, `gate` and `sonar`, lets two runs of one repo share a machine, each in its own checkout, without editing a tracked file. The suffix is added to the Sonar project key (`<key>_<suffix>`) and name (`<name> (<suffix>)`), and to the perf containers and their ports. It is not added to the SonarQube server, the perf volume, or anything under `.marestail/`. The gate creates the suffixed project on its first analysis, and `marestail sonar prune` deletes that project.
    And the nice paragraph is the next paragraph, still beginning `` `marestail run` starts at nice 19 ``
    And `test_readme_documents_run_priority` finds that nice paragraph after the new one instead of requiring it to be the first text after the fence
    And `test_readme_documents_junie` and `test_readme_documents_hermes` split on the original `marestail run tasks/001.md   # Claude (default)` line, not on the new `--run-suffix` line
    And the `--db` paragraph ends with this sentence: `With `MARESTAIL_RUN_SUFFIX` set, the containers end in `_<suffix>`, the ports move by a per-suffix offset unless `MARESTAIL_PERF_DB_PORT` is set, `down` removes only that run's containers, and goldens in the shared volume are still reused; two suffixes that hash to the same port fail at `docker run` because the port is already allocated, and `MARESTAIL_PERF_DB_PORT` is the way out.`
    And the `.importlinter` layer line that contains `marestail.report` and `marestail._location` also contains `marestail.run_suffix`
    And `foundations-import-nothing-above` lists `marestail.run_suffix` in `source_modules`
    And `marestail.run_suffix` is not added to the layer that contains `marestail.jobs`, and no new contract is added
    And `marestail/run_suffix.py` has module-level imports `os` and `re` only
