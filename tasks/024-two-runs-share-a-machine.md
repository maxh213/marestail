+++
depends = ["022-queue-runs-tasks-in-dependency-order"]
stack = true
+++
# 024 — `MARESTAIL_RUN_SUFFIX`: two runs of one repo share a machine

After this task, two `marestail run`s of the same repo can run side by side on one machine, each in its own checkout, without editing a tracked file. Set `MARESTAIL_RUN_SUFFIX=a1` (or pass `--run-suffix a1`) and every name the run shares with the rest of the machine gets the suffix: the Sonar project becomes `marestail_a1`, the perf Postgres containers end in `_a1` and listen on their own ports, and `marestail perf db down` removes only that run's containers. With the variable unset, every name, port and command line is exactly what it is today.

Today the only way to do this is by hand. On 2026-09-15 dandelion's task 014 ran in a second worktree beside task 013 on main, and its `marestail.toml` had to be edited to `project_key = "dandelion-014"` so the two Sonar analyses would not overwrite each other, with a note to revert the key before merging. A scheduler that runs several pipelines of one repo on one machine needs the same thing without touching the repo.

Line numbers below are green's at `5e9911d`. Phases 018–023 land first and may shift them; the function names hold.

## What collides today, and what does not

Two runs of one repo on one machine are always in two checkouts (a run commits to its checkout's branch), so anything keyed by the checkout path or kept under `.marestail/` is already separate. This is every machine-wide or fixed name green uses, checked in the code:

| Name | Where | Scope today | With a suffix |
|---|---|---|---|
| Sonar project key, from `[sonar] project_key` | `gates/sonar.py:82`, passed as `-Dsonar.projectKey` (`:151`), `/k:` (`:266`) and to every API call (`:406`, `:447`, `:471`, `:486`, `:497`, `:512`, `:552`); `cli.py:456` for `sonar setup` | machine-wide: both checkouts read the same tracked `marestail.toml`, so both analyses land on one project | `<key>_<suffix>` |
| Sonar project name | Docker scanner: none passed, so `sonar.projectName` in `sonar-project.properties` applies; .NET scanner: `/n:` from `[sonar] project_name`, else the key (`gates/sonar.py:258`, `:266`); `sonar setup`: `[sonar] project_name`, else the repo folder name (`cli.py:456`) | machine-wide | `<name> (<suffix>)`, passed to both scanners |
| SonarQube server: container `marestail-sonarqube`, host port 9000, volumes `marestail_sonar_data`, `_extensions`, `_logs` (compose project `sonar`, so Docker names them `sonar_marestail_sonar_*`) | `sonar/docker-compose.yml:4-14`, `sonar/setup.py:13` | machine-wide, shared on purpose: one server holds every project | **not suffixed**. `sonar up` and `sonar down` never read the suffix |
| Sonar credentials, erlang plugin jar and plugins folder under `~/.config/marestail/` | `sonar/client.py:9`, `sonar/setup.py:22-24` | machine-wide, shared on purpose | not suffixed |
| Scanner working files: `.marestail/sonar-cache`, `.marestail/scannerwork`, `.sonarqube/`, `.marestail/dotnet-tools`, `.marestail/sonar-dotnet.sh` | `gates/sonar.py:139`, `:153`, `:22`, `:229`, `:259` | per checkout | not suffixed |
| Perf tree containers `{prefix}db-{repo_key}-{tree}` | `perf/db.py:105-106` | per checkout already: `repo_key` hashes the checkout path (`:97-99`) | `{prefix}db-{repo_key}-{tree}_<suffix>` |
| Perf golden-build containers `{prefix}golden-{golden name}` | `perf/db.py:108-109` | per checkout already: the golden name hashes the checkout path (`:387-398`) | `{prefix}golden-{golden name}_<suffix>` |
| Perf file helper `{prefix}files` | `perf/db.py:101-103`, started by `ensure_helper` (`:241-252`) | machine-wide. Two runs that find no helper at the same moment both `docker rm -f` and `docker run --name` it, and one kills the other's | `{prefix}files_<suffix>` |
| Perf working copies `/perf/work/{repo_key}-{tree}` inside the volume | `perf/db.py:114-115` | per checkout already | `/perf/work/{repo_key}-{tree}_<suffix>` |
| Perf host ports: head 55432, baseline 55433, pre-marestail 55434 (`[perf.db] port` or `MARESTAIL_PERF_DB_PORT` moves the base) | `perf/db.py:42`, `:45`, `:111-112`, `:130` | machine-wide: both runs publish `127.0.0.1:55432`, and the second `docker run -p` fails | offset per suffix, see below |
| Perf volume `marestail-perf-pgdata` (`MARESTAIL_PERF_DB_VOLUME`) | `perf/db.py:41`, `:132` | machine-wide, shared on purpose: it caches goldens (6.9 GB for one 50M-row table), keyed by checkout path, image, schema, seed and rows | **not suffixed**, so a later run in the same checkout reuses its golden whatever its suffix |
| Golden names, and their status and log files under `~/.config/marestail/perf-db/` | `perf/db.py:387-398`, `:430-435` | per checkout (path in the hash) | not suffixed, for the same reason |
| Perf password file `~/.config/marestail/perf-db.json` | `perf/db.py:269-277` | machine-wide, shared on purpose | not suffixed |
| Postgres database name `bench` | `perf/db.py:46` | inside each container | not suffixed: each container has its own |
| Golden-build publish `127.0.0.1::5432` | `perf/db.py:296` | Docker picks a free port | not suffixed |
| Detached perf worktrees `tempfile.mkdtemp(prefix="marestail-perf-")` under `$TMPDIR` | `perf/trees.py:17`, `:150` | unique per call | not suffixed |
| QA app port 3400 (`[qa] port`) | `gates/qa.py:11`, `:34`; `gates/_serve.py:41-57` | machine-wide, but a taken port already falls back to a free one | not suffixed |
| Mutation working dirs: `mutants/` (mutmut), `.stryker-tmp` and `reports/mutation`, `.marestail/stryker`, cargo-mutants `--output .marestail`, er scratch under `.marestail`, PIT under the Maven build dir | `gates/py_mutation.py:57`, `gates/ts_mutation.py:12-13`, `gates/cs_mutation.py:14`, `gates/rs_mutation.py:50-51`, `gates/er_mutation.py:86`, `java.py:119-120` | per checkout | not suffixed |
| Stop-hook files `.claude/settings.json`, `.agents/hooks.json`, `.grok/hooks/marestail-gate.json`, `.cursor/hooks.json` | `_install.py:86-90` | per checkout; the hook command carries no name, and the hook reads the suffix from the environment the agent inherited | not suffixed |
| Grok trust store `~/.grok/trusted_folders.toml` | `_install.py:200`, `:213-214` | machine-wide, keyed by path | not suffixed |
| Hook counters `.marestail/hook-<session>.count` and `.turn`, run folders `.marestail/runs/<task>/`, handoffs | `cli.py:284`, `:383`; `runner.py:165`, `:169` | per checkout | not suffixed |
| `docker run --rm` containers for the .NET SDK, Erlang and Ruby images | `dotnet.py:75`, `erlang.py:49`, `ruby.py:47-59` | unnamed | not suffixed |

Green has no lock files, no Docker networks of its own (the Sonar scanner runs with `--network host`, `gates/sonar.py:130-131`) and no fixed `/tmp` paths. Kimi has no hook file in green. Cargo, Maven and npm caches under `~` are shared and have their own locking.

So what actually collides is the Sonar project, the perf host ports and the perf helper; `perf db down` also reaches every run's containers. The suffix fixes those. The tree containers, golden containers and working copies get it too, so `perf db down` can find exactly this run's containers and `docker ps` shows which run owns each.

## Rule

Add `marestail/run_suffix.py`. It imports only `os` and `re`.

- `ENV = "MARESTAIL_RUN_SUFFIX"`. The value must fullmatch `[a-z0-9-]{1,32}`.
- `current() -> str | None` reads the environment on every call (no caching), returns `None` when the variable is unset, and the value when it is valid. Otherwise it raises `SystemExit` with exactly `MARESTAIL_RUN_SUFFIX must be 1 to 32 characters from a-z, 0-9 and -, got {raw!r}`. Set to the empty string is not the same as unset: it fails with `got ''`.
- `named(name: str) -> str` returns `name` when `current()` is `None`, else `f"{name}_{suffix}"`.
- `titled(name: str) -> str` returns `name` when `current()` is `None`, else `f"{name} ({suffix})"`.

The separator is `_` because `_` cannot occur in a suffix. `marestail` with suffix `green` is `marestail_green`, never the `marestail-green` another checkout's hyphenated key could already be, and a Docker name ending `_a1` cannot end with any other suffix.

### Where the suffix comes from

1. `--run-suffix S` on `marestail run`, `marestail gate` and `marestail sonar`. It follows `--agent` beating `MARESTAIL_AGENT` (`runner.py:1245-1254`) and `--scope` being exported for the hooks (`runner.py:323-328`): when given, the command handler sets `os.environ["MARESTAIL_RUN_SUFFIX"] = S` before anything else, so every child inherits it (agent CLIs, their Stop hooks, `marestail perf run`, the background `perf db golden --wait` child started at `perf/db.py:654-660`). argparse validates it with a `type=` function that raises `argparse.ArgumentTypeError("must be 1 to 32 characters from a-z, 0-9 and -, got 'A1'")`, so a bad flag exits 2 with `marestail run: error: argument --run-suffix: must be 1 to 32 characters from a-z, 0-9 and -, got 'A1'`.
2. `MARESTAIL_RUN_SUFFIX`, for every command. `perf run` and `perf db` take no flag: inside a run they inherit the variable, and a person runs them with it set.

`run_pipeline`'s signature does not change. `run_command` in `cli.py` exports the flag and then calls `run_pipeline` as today, so 022's queue, which starts `marestail run` as a subprocess, passes the suffix through the environment and needs no new option.

### When a bad value stops things

- `marestail run`: `run_pipeline` calls `run_suffix.current()` straight after `nice.apply(config)` (added by 018) and before `pick_model`, so a bad value stops the run before any agent starts. A bad nice value still raises first.
- `marestail gate`: `gate_command` calls `run_suffix.current()` before any gate runs and before `--hook` is handled, whatever the tier, so `gate --tier fast` rejects a bad value too. The message is the `SystemExit` above, exit 1.
- `marestail sonar setup` and `prune`, `perf run` and `perf db`: through the first `current()` call.
- Commands that never read it (`install`, `watch`, `graph`, `depth`, `route`, `sonar up`, `sonar down`, and 019's `tasks check`, 021's `status` and 022's `queue` apart from the `run` subprocesses it starts) ignore the variable, valid or not.

## Sonar

- The gate (`analyse`, `gates/sonar.py:80-92`) takes `key = run_suffix.named(project_key)`. Every use downstream (scanner, report task, quality gate, issues, hotspots, measures, language checks) then uses the suffixed key, unchanged otherwise.
- With a suffix set and no `[sonar] project_key`, the gate fails without scanning: `Result("sonar", False, "no project key", ["marestail.toml:1 [sonar] project_key is required when MARESTAIL_RUN_SUFFIX is set"])`. Without a suffix, a missing key behaves as today. That case is outside this task.
- Docker scanner (`scanner_properties`, `:148-156`): with a suffix, `-Dsonar.projectName=<titled name>` follows `-Dsonar.projectKey=<key>_<suffix>`. The name before titling is `[sonar] project_name`, else `sonar.projectName` from `sonar-project.properties` (read with the existing `project_properties`), else the unsuffixed key. For green that is `-Dsonar.projectKey=marestail_a1 -Dsonar.projectName=marestail (a1)`. Without a suffix, no `sonar.projectName` argument is added, as today.
- .NET scanner (`write_scan_script`, `:257-273`): `/k:"<key>_<suffix>" /n:"<titled name>"`, where the name before titling is `[sonar] project_name`, else the unsuffixed key. So a repo with only `project_key = "proj"` gets `/n:"proj (a1)"`, never `proj_a1 (a1)`.
- `marestail sonar setup` (`cli.py:456`): `setup.setup(named(key), titled(name))`, with the same missing-key `SystemExit` message as above. It prints `sonar ready: http://localhost:9000/dashboard?id=marestail_a1`.
- The gate does not create the project. SonarQube creates a missing project on its first analysis when the token may provision projects, and the stored token is the admin's (`sonar/setup.py:113-131`), so a suffixed run's first scan creates `<key>_<suffix>` with the server's default quality gate and profiles. `sonar setup` creates projects the same way with no extra settings (`sonar/setup.py:121-124`), so both routes give the same project.
- A new project has no new-code baseline, so on its first analysis the quality gate's new-code conditions pass trivially. The gate's own checks don't depend on that: issues, hotspots, overall coverage and duplication (`:437-443`, `:507-513`) read the whole project, so a suffixed run is gated as strictly as today.

### `marestail sonar prune`

Suffixed projects pile up on the server: one per distinct suffix, each with its analysis history. A scheduler that reuses a few suffixes (one per slot) keeps that bounded, but one that uses a fresh suffix per run does not. So add one small action, and nothing that searches or deletes in bulk:

- `marestail sonar prune [--run-suffix S]`. `prune` becomes the fourth choice in `SONAR_ACTIONS` (`cli.py:68`).
- The suffix comes from the flag, else `MARESTAIL_RUN_SUFFIX`. With neither: `SystemExit("sonar prune needs a run suffix: pass --run-suffix or set MARESTAIL_RUN_SUFFIX; it never deletes the unsuffixed project")`.
- The key is `named([sonar] project_key)`, with the missing-key message above when the key is unset.
- No credentials (`client.credentials()` is `None`): `SystemExit("sonar is not set up; run: marestail sonar setup")`.
- `api/projects/search?projects=<key>` with the stored token. When it finds nothing, print `no sonar project <key>` and exit 0, so a repeat is harmless. Otherwise `POST api/projects/delete project=<key>` and print `deleted sonar project <key>`, exit 0. An HTTP error comes through `Client.request`'s existing `SystemExit`.
- It lives in `marestail/sonar/setup.py` as `prune(key: str) -> None` next to `ensure_project`, and never starts or stops the server.

## Perf database

`Database` (`perf/db.py:80-115`) gains a last field `suffix: str | None = None`, so every existing positional construction still works. `build_database` (`:118-134`) sets it from `run_suffix.current()`. Then:

- `helper`, `tree_container(tree)`, `golden_container(name)` and `work_dir(tree)` append `_<suffix>` when `suffix` is set, and are unchanged when it is `None`.
- `port`: `MARESTAIL_PERF_DB_PORT`, when set, is used exactly as given, suffix or not. That lets a caller hand out ports itself. Otherwise the base is `[perf.db] port` or 55432, as today, plus `len(TREE_PORT_OFFSETS) * slot` when a suffix is set, where `slot = 1 + int(hashlib.sha256(suffix.encode()).hexdigest()[:8], 16) % 1000`. `tree_port` is unchanged, so it adds 0, 1 or 2 on top. Worked values from the default base: `a1` → slot 648 → head 57376, baseline 57377, pre-marestail 57378; `b2` → slot 857 → head 58003; `slot-1` → slot 437 → head 56743. From `[perf.db] port = 6000`, `a1` gives head 7944.
- Two suffixes that hash to the same slot (1 in 1000 for a pair) do not share a database. The second run's `docker run -p` fails with Docker's "port is already allocated", which `step` turns into `DatabaseError("starting <container> failed (exit 125): …")`, so the sample fails loudly. `MARESTAIL_PERF_DB_PORT` is the way out, and the README says so. No port-range check is added: a base so high that the offset passes 65535 fails at `docker run` in the same way.
- Labels: when `suffix` is set, `ensure_helper` and `start_postgres` add `--label marestail.run-suffix=<suffix>` straight after `--name <name>`. Without a suffix their argument lists are byte-identical to today.
- `down` (`:709-714`): with a suffix, it lists `docker ps -a --filter label=marestail.run-suffix=<suffix> --format {{.Names}}` and removes exactly those, which leaves other runs' containers, the unsuffixed containers and the volume alone. Without a suffix it is today's code: every container whose name starts with the prefix, including suffixed runs' containers. A person who runs `perf db down` with no suffix still clears the whole machine, as today. The `down` help text (`cli.py:123`) becomes `remove this run's performance database containers (every one when MARESTAIL_RUN_SUFFIX is unset), keeping the volume`.
- `release`, `reset`, `build_golden`, `discard_build` and `url` already go through these methods, so the run's own containers, working copies and printed URL follow with no further change. `golden_name`, `prune`, the status and log files and `password` do not change.
- `TREE_PORT_OFFSETS` has no `control` entry, so `tree_port("control")` raises `KeyError` today when `[perf] control` and `--db` are combined. That bug is out of scope; don't fix it here.

## README

- `## Environment variables`: add `` | `MARESTAIL_RUN_SUFFIX` | per-run suffix (`[a-z0-9-]{1,32}`) for the names a run shares with the machine: Sonar project `<key>_<suffix>`, perf containers `…_<suffix>` and their ports; `--run-suffix` on `run`, `gate` and `sonar` sets it | ``, after `MARESTAIL_TASK`.
- `## Use`: two lines in the code block, after `marestail sonar setup`: `marestail run tasks/001.md --run-suffix a1   # a second run of this repo on this machine, in its own checkout` and `marestail sonar prune --run-suffix a1   # delete the Sonar project that suffix created`.
- `## Performance`: one sentence at the end of the `--db` paragraph: with `MARESTAIL_RUN_SUFFIX` set, the containers end in `_<suffix>`, the ports move by a per-suffix offset unless `MARESTAIL_PERF_DB_PORT` is set, `down` removes only that run's containers, and goldens in the shared volume are still reused.
- One paragraph after the `## Use` code block: what the suffix is for, the table's short form (Sonar key and name, perf containers and ports; not the SonarQube server, the volume or anything under `.marestail/`), that the gate creates the suffixed project on first analysis, and that `sonar prune` deletes it.

## `.importlinter`

Add `marestail.run_suffix` to the layer that holds `marestail.report : marestail.config : marestail.changes : marestail._location`, and to the `source_modules` of the `foundations-import-nothing-above` contract. `marestail.gates.sonar`, `marestail.perf.db`, `marestail.sonar.setup` (if `prune` needs it; it can take the key as an argument like `setup`), `marestail.cli` and `marestail.runner` import it downwards.

## Tests

Pytest, in-process and hermetic, with the existing fakes (`fake_run`, `FakeClient`, `Recorder`, `make_db`, `make_context`). No Docker, no SonarQube, no agent.

`tests/conftest.py` gets an autouse fixture that runs `monkeypatch.delenv("MARESTAIL_RUN_SUFFIX", raising=False)`. The gates run pytest inside `marestail run`, so a suffixed run of this repo would otherwise inherit the variable into its own tests and every unsuffixed name assertion would fail. A test that exercises the flag first does `monkeypatch.setenv("MARESTAIL_RUN_SUFFIX", "zz")`, so monkeypatch removes whatever the handler writes at teardown, and asserts the flag wins (`os.environ["MARESTAIL_RUN_SUFFIX"] == "a1"` afterwards).

`tests/test_run_suffix.py`:
- unset → `current()` is `None`, `named("k") == "k"`, `titled("K") == "K"`.
- `a1` → `named("k") == "k_a1"`, `titled("K") == "K (a1)"`.
- accepted: `a`, `0`, `-`, `a-1-b`, and a 32-character value.
- rejected, each asserting the full message `MARESTAIL_RUN_SUFFIX must be 1 to 32 characters from a-z, 0-9 and -, got …`: `""` (`got ''`), `A1`, `a_1`, `a.1`, `a/b`, `a 1`, `a1\n`, `é`, and a 33-character value.
- the argparse `type=` function returns a valid value unchanged and raises `argparse.ArgumentTypeError` with `must be 1 to 32 characters from a-z, 0-9 and -, got 'A1'` for `A1`.

`tests/test_cli.py`:
- `run --run-suffix a1` with the env at `zz`: `run_pipeline` (stubbed, as `test_run_command` does) is called with today's arguments and the env is `a1` when it is called. `run --run-suffix A1` exits 2 and stderr ends with `argument --run-suffix: must be 1 to 32 characters from a-z, 0-9 and -, got 'A1'`.
- `gate --run-suffix a1` exports the value before the gates run (record it from the stubbed `run_gates_with_context`). `gate` with env `A1` raises `SystemExit` with the full message and runs no gate, for `--tier fast` and for `--hook`.
- `sonar setup` with `project_key = "key"` and suffix `a1` calls `setup.setup("key_a1", "repo (a1)")`. With `project_name = 'N'` and `project_key = "key"` it calls `("key_a1", "N (a1)")`. With a suffix and no key it raises the missing-key message. The existing unsuffixed `test_sonar_setup` cases stay unchanged.
- `sonar up` and `sonar down` with suffix `a1` set call `setup.up` and `setup.down` exactly as without it.
- `sonar prune --run-suffix a1` calls `setup.prune("key_a1")`. `sonar prune` with neither flag nor env raises the needs-a-suffix message. With the env at `b2` and no flag it calls `setup.prune("key_b2")`.
- `run_pipeline` calls `run_suffix.current()` after `nice.apply` and before `pick_model`; a bad env value raises before `run_steps` (extend `pipeline_env` in `tests/test_runner_flow.py`, as 018 does for nice).

`tests/test_sonar_setup.py`:
- `prune` with no credentials raises `sonar is not set up; run: marestail sonar setup`.
- project found → one `api/projects/search` get with `projects=key_a1`, one `api/projects/delete` post with `project=key_a1`, prints `deleted sonar project key_a1`.
- project missing → no post, prints `no sonar project key_a1`.
- `COMPOSE`'s file still names `container_name: marestail-sonarqube` and `"9000:9000"`, and `DEFAULT_URL` is `http://localhost:9000`, with a suffix set: the server is never suffixed.

`tests/test_gates_sonar.py`:
- `test_scanner_command` with suffix `a1`: the same list as today except `-Dsonar.projectKey=proj_a1` followed by `-Dsonar.projectName=proj (a1)`. With a `sonar-project.properties` holding `sonar.projectName=Props`, the name is `Props (a1)`. With `[sonar] project_name = "Nice"` it is `Nice (a1)`, which beats the properties file.
- The unsuffixed `test_scanner_command` still asserts no `sonar.projectName` argument.
- A clean gate run with suffix `a1`: every `FakeClient` get that names a project carries `proj_a1` (`projectKey`, `componentKeys`, `project`, `component`).
- Suffix set and no `project_key`: the `no project key` result above, and `fake_run` saw no call.
- .NET script with suffix `a1`: `/k:"proj_a1" /n:"proj (a1)"`, and `/n:"Nice (a1)"` with `project_name = "Nice"`. Without a suffix the script text is byte-identical to today's.

`tests/test_perf_db.py`:
- `test_database_names` stays as it is for `suffix=None`. A suffixed twin with `make_db(tmp_path, suffix="a1")` asserts `helper == "mp-files_a1"`, `tree_container("head") == f"mp-db-{key}-head_a1"`, `golden_container("g") == "mp-golden-g_a1"`, `work_dir("head") == f"/perf/work/{key}-head_a1"`, and `volume == "vol"`.
- `build_database` with env suffix `a1` and no port settings: `port == 57376`, `suffix == "a1"`. With `b2`: 58003. With `slot-1`: 56743. With `[perf.db] port = "6000"` and `a1`: 7944. With `MARESTAIL_PERF_DB_PORT=7000` and `a1`: 7000. `tree_port` on the `a1` database gives `[57376, 57377, 57378]`. `test_build_database_defaults` stays equal to today's `Database(...)`, because `suffix` defaults to `None`.
- `golden_name` is the same with and without a suffix.
- `start_postgres` and `ensure_helper` with `suffix="a1"` add `--label marestail.run-suffix=a1` right after `--name <name>`. With `None`, the existing `test_start_postgres` and `test_ensure_helper_starts` lists hold unchanged.
- `down` with `suffix="a1"` runs `docker ps -a --filter label=marestail.run-suffix=a1 --format {{.Names}}` and removes only the names it returned. The existing `test_down` holds for `None`.
- `url --tree head` under suffix `a1` prints a URL on port 57376.
- `release` with a suffix removes `…-head_a1` containers and `…-head_a1` work dirs.

`tests/test_perf_trees.py`: `TEMP_PREFIX` is still `marestail-perf-` with a suffix set. `tests/test_gates_qa.py`: the preferred port is still 3400 with a suffix set.

## Must not break

- With `MARESTAIL_RUN_SUFFIX` unset and no `--run-suffix`, every command line, container name, port, Sonar key, API call and printed line is byte-identical to today. The existing tests pin that and must pass unedited, apart from the new conftest fixture.
- `marestail run` on a task file with no front matter; the existing `pipeline.log`, `timeline.json` and handoff layout; stdout of a run (the suffix prints nothing new).
- `run_pipeline`'s signature and its order: config load, nice, suffix check, `pick_model`, `pick_scope`, `share_scope`, `MARESTAIL_TASK`, `make_run`, `record_start`, steps.
- `marestail sonar up`, `down` and `setup` without a suffix; the SonarQube container, port and volumes.
- The goldens in `marestail-perf-pgdata`: a suffixed run in a checkout reuses the golden an unsuffixed run built there.
- 020's event fields and 021's JSON shape: the suffix is not added to either.
- The queue driver scripts in `.marestail/phases/`: never edit, move or delete anything under `.marestail/phases/`, because a live driver runs from there.

## Done when

`tests/test_run_suffix.py` and the new cases above pass with every branch covered; the existing tests pass; `MARESTAIL_RUN_SUFFIX=a1 marestail gate --tier sonar` would scan `marestail_a1` (pinned by the scanner-command test); `perf db down` under a suffix removes only labelled containers; `marestail sonar prune` exists; `.importlinter` lists `marestail.run_suffix`; and the README carries the table row, the two example lines, the Performance sentence and the paragraph.
