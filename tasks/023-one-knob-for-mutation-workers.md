+++
depends = ["018-runs-stay-nice"]
stack = false
+++
# 023 — one knob for mutation workers: `[run] jobs` and `MARESTAIL_JOBS`

After this task, `[run] jobs = 3` in `marestail.toml`, or `MARESTAIL_JOBS=3` in the environment, runs every language's mutation gate with 3 parallel workers, whatever the tool behind it: mutmut, Stryker, Stryker.NET, muex, mutant, cargo-mutants, PIT, or marestail's own Erlang mutation loop. A scheduler that runs several pipelines on one machine can then split the CPUs between them without editing each repo's tracked config. Today the worker count is set per language, under four different key names, and three gates have no setting at all:

| gate | tool | flag it takes | how it is set today | today's value | tool's own default |
|---|---|---|---|---|---|
| `py.mutation` | mutmut 3.8.0 | `--max-children N` | `[python] mutation_workers`, `py_mutation.py:58-60` | 4 | `os.cpu_count() or 4` (mutmut `__main__.py:950-951`) |
| `ts.mutation` | StrykerJS | `--concurrency N` | not passed, `ts_mutation.py:71-75` | the repo's `stryker.config` value, else the tool default | available parallelism − 1 above 4, else all of it (`concurrency-token-provider.js:47-49` in `@stryker-mutator/core` 10.0.0) |
| `ex.mutation` | muex | `--concurrency N` | `[elixir] muex_concurrency`, `ex_mutation.py:118`; a falsy value drops the flag (`option`, `ex_mutation.py:139-140`) | 4 | `System.schedulers_online()` (muex 0.11.2 `lib/muex/config.ex:222`) |
| `rb.mutation` | mutant | `--jobs N` | not passed, `rb_mutation.py:71` | the repo's mutant config `jobs`, else the tool default | the number of processors (mutant's docs; ruby is not installed on this machine, so not checked locally) |
| `cs.mutation` | Stryker.NET 4.16 | `--concurrency N` | not passed, `cs_mutation.py:155-174` | the repo's `stryker-config.json` value, else the tool default | half the logical processors, at least 1 (Stryker.NET's docs; `Concurrency must be at least 1` is in its `Stryker.Configuration.dll`; dotnet is not installed here) |
| `er.mutation` | marestail's `mutation.escript` | none: marestail runs the mutants itself | serial `for` loop, `er_mutation.py:141-145` | 1 | — |
| `rs.mutation` | cargo-mutants 27.1.0 | `--jobs N` | `[rust] mutation_jobs`, `rs_mutation.py:49-58` | 2 | 1 (`lab.rs:94`: `options.jobs.unwrap_or(1)`) |
| `java.mutation` | pitest-maven 1.30.0 | `-Dthreads=N` | `[java] mutation_threads`, `java_mutation.py:101` | 2 | 1 |

Every mutation tool listed has a parallel option, so jobs has an effect on every mutation gate. No gate runs files or modules in parallel itself today: nothing under `marestail/` uses a thread pool, a process pool or `os.cpu_count()`, and `run_gates_with_context` runs the gates one after another (`gates/__init__.py:131`). The Erlang gate is the only one where marestail is the mutation runner, so this task gives it a thread pool that jobs sizes.

There are no Gleam gates. main removed them in `6f0f356` ("Remove Gleam gates for now"), when `gl_mutation.py` was a 9-line stub, and nothing in green's `marestail/`, `tests/` or `templates/` mentions Gleam or `[gleam] mutation_jobs`. The rule the brief gave for `[gleam] mutation_jobs` (a language's own key keeps working and wins for that language) applies here to the four per-language keys that do exist, and a later Gleam or Go gate follows it too.

## Rule

Add `marestail/jobs.py`, which imports only `os`, `re` and `marestail.config`:

- `ENV = "MARESTAIL_JOBS"`.
- `parse(raw, name) -> int`. It accepts an `int` that is not a `bool` and is 1 or more, or a `str` that matches `[0-9]+` in full, with no whitespace, and is 1 or more (`"03"` is 3). Anything else raises `SystemExit(f"{name} must be a whole number 1 or more, got {raw!r}")`. `name` is `MARESTAIL_JOBS` for the variable and `[run] jobs` for the key, so:
  - `MARESTAIL_JOBS=0` gives `MARESTAIL_JOBS must be a whole number 1 or more, got '0'`
  - `MARESTAIL_JOBS=four` gives `MARESTAIL_JOBS must be a whole number 1 or more, got 'four'`
  - `[run] jobs = 0` gives `[run] jobs must be a whole number 1 or more, got 0`
  - `[run] jobs = -2`, `2.5`, `true` and `" 3"` give `got -2`, `got 2.5`, `got True` and `got ' 3'`.
- `run_jobs(config) -> int | None` reads both sources and validates both whenever they are present, so a broken tracked key is reported even when the variable overrides it:
  1. `MARESTAIL_JOBS`, when it is set to anything but the empty string. It is parsed first.
  2. `[run] jobs`, read with `config.get("run", "jobs")`, when it is not `None`.
  3. `None` when neither is present.

  The value used is the variable's when both are set. It is then capped at `os.cpu_count() or 1`, silently: a value above the machine's CPU count only adds contention, and contention turns slow kills into timeouts, which fail `py.mutation`, `ts.mutation` and `cs.mutation`.
- `pick(config, section, key, default)` gives the value one gate passes to its tool. The first match wins:
  1. `MARESTAIL_JOBS`, parsed and capped as above.
  2. The language's own key, when `key` is present in `[section]` at all, even as `0`. It is returned raw, uncapped and unvalidated, exactly as the gate reads it today.
  3. `[run] jobs`, parsed and capped.
  4. `default`, which is today's value.

  It raises the same `SystemExit` as `run_jobs` for a bad variable or a bad `[run] jobs`.

**0 is rejected, not read as unset.** "Zero workers" has no meaning, and the tools disagree about it today: mutmut's `Pool(processes=0)` raises, Stryker.NET refuses it, cargo-mutants silently uses 1 and muex's gate drops the flag. The empty variable, `MARESTAIL_JOBS=`, means unset, so `MARESTAIL_JOBS= marestail run ...` falls back to the key. The per-language keys are not changed by this rule: `[elixir] muex_concurrency = 0` still drops the flag, as `tests/test_gates_ex_mutation.py:125` pins today.

Why the variable beats the language key: it is the per-run setting an outside caller uses because it knows how busy the machine is, and it must not need to know which language keys a repo happens to set. Why the language key beats `[run] jobs`: in the same file, the more specific setting wins, as the brief's `[gleam] mutation_jobs` rule says.

## What each gate passes

`N` below is `jobs.pick(...)` or `jobs.run_jobs(...)`, read inside the gate each time it runs, from `ctx.config` and the process environment. This way `marestail run`, `marestail gate` and a worker's own `marestail gate --tier full` all see it.

| gate | call | when N is unset | when N = 3 |
|---|---|---|---|
| `py.mutation` | `pick(config, "python", "mutation_workers", 4)` | `mutmut run <patterns> --max-children 4` | `mutmut run <patterns> --max-children 3` |
| `ts.mutation` | `run_jobs(config)` | today's command, with no `--concurrency` | `--concurrency 3` straight after `--cleanTempDir always`, before any `--mutate` |
| `ex.mutation` | `pick(config, "elixir", "muex_concurrency", 4)` | `--concurrency 4`, as today | `--concurrency 3` in the same place |
| `rb.mutation` | `run_jobs(config)` | `bundle exec mutant run <subjects>`, as today | `bundle exec mutant run --jobs 3 <subjects>` |
| `cs.mutation` | `run_jobs(config)` | today's command, with no `--concurrency` | `--concurrency 3` after `-r progress`, before the `-m` patterns |
| `er.mutation` | `run_jobs(config) or 1` | one worker: the mutants run one at a time, in manifest order | three workers |
| `rs.mutation` | `pick(config, "rust", "mutation_jobs", 2)` | `--jobs 2`, as today | `--jobs 3` |
| `java.mutation` | `pick(config, "java", "mutation_threads", 2)` | `-Dthreads=2`, as today | `-Dthreads=3` |

Gate by gate:

- **Erlang.** `run_mutants` (`er_mutation.py:141-145`) hands the mutants that are not `skipped` to `concurrent.futures.ThreadPoolExecutor(max_workers=workers)` and consumes `pool.map(...)` in full, so an exception in a worker still reaches `run_one`. Each call keeps today's `check_mutant` unchanged. It is safe to run in threads: each mutant writes only its own dict and its own `ebin-<id>` folder (`er_mutation.py:157`) and runs in its own `escript` VM. `host_erlang` has already been cached by `compile_with_tests` and `baseline` before the pool starts, and `write_report` still runs once, after every mutant, in manifest order. The time-budget check before each mutant (`er_mutation.py:148-153`) and the per-mutant timeout, computed once from the baseline (`er_mutation.py:142`), are unchanged. Under Docker (no host Erlang), N workers means up to N containers at once.
- **Rust.** cargo-mutants declares `--in-place` with `conflicts_with = "jobs"` (`src/main.rs:183-190` of cargo-mutants 27.1.0 in `~/.cargo/registry`). Today, `[rust] mutation_args = ["--in-place"]` therefore makes `cargo mutants` stop with a usage error before it tests a single mutant, and `tests/test_gates_rs_mutation.py:92` pins that broken command line. When `--in-place` is among the `mutation_args`, the gate passes no `--jobs` at all, whatever N is: in-place mode has one source tree, so jobs has no effect there. Update that test. An explicit `--jobs` also beats `CARGO_MUTANTS_JOBS` in the environment, as it does today.
- **Java.** A pom that sets `<threads>` in the pitest plugin's `<configuration>` beats any `-Dthreads` property. That is Maven's rule, today as after this task. `templates/java-pitest.xml` sets none. Say so in the README.
- **TypeScript and C#.** `--concurrency` on the command line beats `concurrency` in `stryker.config.*` or `stryker-config.json`. With a TypeScript checker configured, StrykerJS splits N into `ceil(N/2)` checker processes and `floor(N/2)` test runners, at least 1 of each, and turns the checkers into runners once checking is done (`concurrency-token-provider.js:19-23`). N is still the total.
- **Ruby.** `--jobs` beats `jobs` in the repo's mutant config.

## CPU count, perf and the other tools' own threads

- N is capped at `os.cpu_count()`. The cap applies only to values that come from `MARESTAIL_JOBS` or `[run] jobs`; a per-language key is passed as it is today.
- N counts the mutation tool's workers, not every thread below them. cargo-mutants runs one `cargo build` and one `cargo test` per job, in its own copy of the tree. It already caps the build tasks across all jobs at NCPUS through its GNU jobserver (`lab.rs:52-57`, `--jobserver` defaults to true), and it warns by itself above 8 jobs (`options.rs:388-393`). libtest's test threads inside each `cargo test` are not capped by either marestail or cargo-mutants, and this task does not add `--test-threads`. mutmut uses N both for its generation pool and for its test children (`__main__.py:114-116`, `973`). PIT runs N minion threads in one JVM.
- Perf never reads the CPU count: nothing in `marestail/perf/` mentions CPUs, threads or workers. In one run, the perf step and the full-tier gates run in different steps, never at the same time. Across runs on one machine, a lower N leaves more CPU for another run's perf samples, which is what an outside scheduler sets `MARESTAIL_JOBS` for.

## Results do not depend on N

Jobs is a speed setting. It must never skip, sample down or weaken a gate:

- It changes only the worker-count flag, or the Erlang pool size. It never touches the scope, focus, the mutant set, `mutation_max`, `muex_max_mutations`, `muex_filter`, `muex_optimize`, timeouts, `--no-shuffle`, `--break-on-initial-test-failure`, `mutation_args`, which statuses count as killed, or the time budget.
- Every tool builds its mutant set before it schedules any of it, so the mutants are the same for every N. cargo-mutants already runs with `--no-shuffle` (`rs_mutation.py:54`), and the Erlang cap keeps an even spread of ids (`kept_ids`, `er_mutation.py:228-230`), not a random sample.
- Every gate judges each mutant from its own entry in the tool's report, never from counts in the output text, so the pass or fail result, the summary and the set of findings are the same for every N.
- The order of findings is fixed where it could follow completion order and matters. `rs.mutation` and `rb.mutation` cut their findings at `MAX_FINDINGS = 60` (`rs_mutation.py:108`, `rb_mutation.py:151`), and cargo-mutants appends outcomes to `outcomes.json` as they finish. With `--jobs 2`, today's default, which 60 findings you see can already change from one run to the next. Both gates sort their findings by (repo-relative path, line as an integer, finding text) before the cut. The Erlang gate keeps manifest order, because statuses are written into the manifest-order list. The other gates keep their report order.
- One effect of timing is left, and it was already there at N = 1. A mutant whose tests run past the tool's timeout under load is reported as timed out, which `py.mutation`, `ts.mutation` and `cs.mutation` fail, and an Erlang mutant the 7200-second budget does not reach is `not checked`. The CPU cap keeps the first rare. A larger N only makes the second rarer.

## Where else it applies

- `run_pipeline` calls `jobs.run_jobs(config)` once, straight after `config = config_module.load(Path.cwd())` (`runner.py:200` today, and after `nice.apply(config)` once 018 has added it), and before `pick_model`. A bad value then stops the run before any agent starts, not hours later in the hardener's full tier. The return value is not used there.
- Inside a gate, a bad value raises the same `SystemExit`, which `run_one` already turns into a failed result, `py.mutation crashed: SystemExit MARESTAIL_JOBS must be a whole number 1 or more, got '0'` (`gates/__init__.py:143-167`). `marestail gate` needs no separate check. The fast tier and the Stop hooks never read jobs.
- `.importlinter`: `marestail.jobs` imports only `marestail.config`, so add it to the layer that holds `marestail.context` and `marestail.ran_against`. The gates and the runner sit above it.
- `.claude/skills/add-language/SKILL.md` and `.agents/skills/add-language/SKILL.md`, which are identical copies: in "The gate contract", add one bullet. It says a mutation gate passes its tool's parallel flag from `jobs.pick(ctx.config, "<section>", "<key>", <today's default>)`, or from `jobs.run_jobs(ctx.config)` when the language has no key of its own, and it names the tool's flag in the README.

## Config template and README

- `templates/marestail.toml`: add a `[run]` section with `# jobs = 4  # parallel workers for every mutation gate; MARESTAIL_JOBS wins over it`. If 018 or another task has already added `[run]`, put the line there. Comment out the three shipped per-language values, which today pin every newly installed repo past `[run] jobs`, each with its default in the comment: `mutation_workers = 4` (template line 39), `mutation_jobs = 2` (line 79) and `mutation_threads = 2` (line 89). Each becomes `# mutation_workers = 4  # wins over [run] jobs for mutmut`, and so on. The defaults in the code are unchanged, so an installed repo behaves as before until someone sets jobs. Repos that already carry those keys keep them, and the keys keep winning over `[run] jobs`.
- README: after the mutation-scope paragraph (`README.md:49`), add a paragraph that gives the rule. It says: `[run] jobs` or `MARESTAIL_JOBS` sets the number of parallel mutation workers; the variable wins, then `[python] mutation_workers`, `[elixir] muex_concurrency`, `[rust] mutation_jobs` and `[java] mutation_threads` for their own language, then `[run] jobs`, then each tool's default as in the table above; the value is capped at the CPU count; 0 is an error; it never changes which mutants run or how they are judged. Name each tool's flag, the `--in-place` exception and the pom `<threads>` caveat.
- README environment table: `` | `MARESTAIL_JOBS` | parallel workers for every mutation gate (1 or more, capped at the CPU count; empty means unset); wins over `[run] jobs` and the per-language worker keys | ``.

## Must not break

- With neither `MARESTAIL_JOBS` nor `[run] jobs` set, every mutation gate's command line is byte-identical to today's. The one exception is `rs.mutation` with `--in-place` in `mutation_args`, which fails today. The Erlang gate runs its mutants one at a time, in manifest order, with the same timeouts, statuses and report.
- `[python] mutation_workers`, `[elixir] muex_concurrency`, `[rust] mutation_jobs` and `[java] mutation_threads` keep their meaning and defaults, including a falsy `muex_concurrency` dropping the flag.
- Every existing mutation-gate test, apart from the rs `--in-place` assertion at `tests/test_gates_rs_mutation.py:92` and any rs or rb test whose expected findings order changes with the sort.
- `tests/conftest.py`'s autouse `hermetic` fixture deletes `MARESTAIL_JOBS` (`monkeypatch.delenv("MARESTAIL_JOBS", raising=False)`). Otherwise a shell or scheduler that sets it would change the command lines that existing tests pin, including when the gauntlet runs pytest inside a run started with `MARESTAIL_JOBS` set.
- `marestail run` on a task file with no front matter; the existing `pipeline.log`, `timeline.json` and handoff layout; the queue driver scripts in `.marestail/phases/`. Never edit, move or delete anything under `.marestail/phases/`, because a live driver runs from there.
- stdout of a run and of `marestail gate`: the cap and the precedence print nothing.

## Tests

All in-process, with `subprocess` stubbed through the existing `fake_run` fixture and `os.cpu_count` stubbed with `monkeypatch` to 8, unless a test says otherwise. No test runs a mutation tool.

`tests/test_jobs.py`:

- `parse`: accepted `1`, `3`, `"3"`, `"03"`. Rejected, each asserting the full message for both names: `0`, `"0"`, `-1`, `"-1"`, `True`, `False`, `2.5`, `"2.5"`, `"four"`, `" 3"`, `[3]`.
- `run_jobs`: nothing set gives `None`; `[run] jobs = 3` gives 3; `MARESTAIL_JOBS=5` beats `[run] jobs = 3`; `MARESTAIL_JOBS=` (empty) falls through to `[run] jobs = 3`; `MARESTAIL_JOBS=0` raises the variable's message; `[run] jobs = 0` raises the key's message; a bad `[run] jobs` raises even when `MARESTAIL_JOBS=5` is set; `cpu_count` 2 caps `MARESTAIL_JOBS=16` to 2; `cpu_count` returning `None` caps to 1.
- `pick`: the variable beats a language key; a language key beats `[run] jobs`; `[run] jobs` beats the default; the default comes back when nothing is set; a language key of `0` comes back as `0`; a language key of 16 with `cpu_count` 2 comes back as 16 (not capped).

One test per gate, in that gate's existing test file, parametrised over **N unset**, **`MARESTAIL_JOBS=3`** and **`[run] jobs = 3`**. It reuses the file's existing full-run setup and asserts the exact command the stub received, as in the table under "What each gate passes":

- `tests/test_gates_py_mutation.py`: `[<venv>/bin/mutmut, "run", "--max-children", "4"]`, then `"3"`. Add a case where `[python] mutation_workers = 8` with `[run] jobs = 3` gives `"8"`, and `MARESTAIL_JOBS=3` over it gives `"3"`.
- `tests/test_gates_ts_mutation.py`: `BASE` exactly, then `BASE + ["--concurrency", "3"]`, and with a `--mutate` target, `BASE + ["--concurrency", "3", "--mutate", ...]`.
- `tests/test_gates_ex_mutation.py`: `["mix", "muex", "--format", "json", "--fail-at", "0", "--no-filter", "--concurrency", "4"]`, then the same ending `"3"`.
- `tests/test_gates_rb_mutation.py`: `["bundle", "exec", "mutant", "run", <subjects>]`, then `["bundle", "exec", "mutant", "run", "--jobs", "3", <subjects>]`.
- `tests/test_gates_cs_mutation.py`: today's full command, then the same with `"--concurrency", "3"` just before the first `-m`.
- `tests/test_gates_rs_mutation.py`: `[... "--jobs", "2"]`, then `"3"`; with `mutation_args = ["--in-place"]`, no `--jobs` for N unset and for N = 3.
- `tests/test_gates_java_mutation.py`: `-Dthreads=2`, then `-Dthreads=3`.
- `tests/test_gates_er_mutation.py`: stub `er_mutation.ThreadPoolExecutor` with a recorder that wraps the real one and assert `max_workers` 1, then 3. Then run the gate for real with 3 workers, over five mutants, with `fake_run` given a callable that answers by command (a list of replies would be popped in thread order), and assert the `Result` and `er-mutation.json` are identical to the same run with N unset. The existing `test_time_budget` and `test_per_mutant_timeout`, which drive a fake clock, must still pass unchanged at N unset.

Determinism:

- `tests/test_gates_rs_mutation.py`: 70 surviving outcomes across two files, fed once in source order and once shuffled with a fixed seed, give the same 60 findings, sorted by path, then line as an integer (`src/a.rs:9` before `src/a.rs:10`), then text.
- `tests/test_gates_rb_mutation.py`: the same for a mutant session report whose `subject_results` arrive in two different orders.

Runner:

- `tests/test_runner_flow.py`: `pipeline_env` records `jobs.run_jobs` calls. Assert that it is called once with the loaded config before `pick_model`, and that `MARESTAIL_JOBS=0` raises `SystemExit` with the full message before `run_steps`.
- `tests/test_gates_init.py`: a mutation gate whose `jobs` call raises `SystemExit` gives the `crashed` result carrying the message, and the gates after it still run.

## Done when

Every mutation gate passes its tool's parallel flag from `jobs.pick` or `jobs.run_jobs`, and the Erlang gate sizes its pool from them. With jobs unset, every command line is today's, apart from the rs `--in-place` fix. `MARESTAIL_JOBS` beats the per-language keys, which beat `[run] jobs`. 0 and non-integers are rejected with the messages above, and values above the CPU count are capped. rs and rb sort their findings before the cut. `tests/test_jobs.py` and the per-gate tests pass with every new branch covered, `.importlinter` lists `marestail.jobs`, the template, README and add-language skill carry the rule, and `conftest.py` clears `MARESTAIL_JOBS`.
