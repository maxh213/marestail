# QA: 023 — one knob for mutation workers: `[run] jobs` and `MARESTAIL_JOBS`

Run from the `marestail-green` repository root with the python environment active.
This procedure verifies that `MARESTAIL_JOBS` and `[run] jobs` configure parallel mutation workers across all supported languages, following precedence and CPU capping rules without altering gate verdicts.

## What you see

When `marestail run` encounters an invalid worker configuration, it halts immediately before model selection or agent startup:

```
$ MARESTAIL_JOBS=0 bin/marestail run tasks/t.md
MARESTAIL_JOBS must be a whole number 1 or more, got '0'
```

When `[run] jobs` in `marestail.toml` contains an invalid value:

```
$ bin/marestail run tasks/t.md
[run] jobs must be a whole number 1 or more, got 0
```

Inside `marestail gate`, an invalid value crashes the mutation gate without preventing subsequent gates from running:

```
[FAIL] py.mutation    py.mutation crashed: SystemExit MARESTAIL_JOBS must be a whole number 1 or more, got '0'
```

## Procedure

1. `parse ok`.
   In Python, invoke `jobs.parse(raw, name)`.
   - Verify `1`, `3`, `"3"`, and `"03"` return integer `1`, `3`, `3`, `3` respectively for both `"MARESTAIL_JOBS"` and `"[run] jobs"`.
   - Verify non-integers, booleans, and values below 1 raise `SystemExit` with message `<name> must be a whole number 1 or more, got <repr(raw)>`:
     - `jobs.parse(0, "MARESTAIL_JOBS")` -> `MARESTAIL_JOBS must be a whole number 1 or more, got 0`
     - `jobs.parse("0", "MARESTAIL_JOBS")` -> `MARESTAIL_JOBS must be a whole number 1 or more, got '0'`
     - `jobs.parse(-1, "MARESTAIL_JOBS")` -> `MARESTAIL_JOBS must be a whole number 1 or more, got -1`
     - `jobs.parse("-1", "MARESTAIL_JOBS")` -> `MARESTAIL_JOBS must be a whole number 1 or more, got '-1'`
     - `jobs.parse(True, "MARESTAIL_JOBS")` -> `MARESTAIL_JOBS must be a whole number 1 or more, got True`
     - `jobs.parse(False, "MARESTAIL_JOBS")` -> `MARESTAIL_JOBS must be a whole number 1 or more, got False`
     - `jobs.parse(2.5, "MARESTAIL_JOBS")` -> `MARESTAIL_JOBS must be a whole number 1 or more, got 2.5`
     - `jobs.parse("2.5", "MARESTAIL_JOBS")` -> `MARESTAIL_JOBS must be a whole number 1 or more, got '2.5'`
     - `jobs.parse("four", "MARESTAIL_JOBS")` -> `MARESTAIL_JOBS must be a whole number 1 or more, got 'four'`
     - `jobs.parse(" 3", "MARESTAIL_JOBS")` -> `MARESTAIL_JOBS must be a whole number 1 or more, got ' 3'`
     - `jobs.parse([3], "MARESTAIL_JOBS")` -> `MARESTAIL_JOBS must be a whole number 1 or more, got [3]`
   - Verify identical error messages for name `"[run] jobs"` (e.g. `[run] jobs must be a whole number 1 or more, got 0`).

2. `run_jobs ok`.
   - When neither `MARESTAIL_JOBS` nor `[run] jobs` is present: `run_jobs(config)` returns `None`.
   - When `[run] jobs = 3` and env is unset: returns `3`.
   - When `MARESTAIL_JOBS=5` and `[run] jobs = 3`: returns `5` (env beats config).
   - When `MARESTAIL_JOBS=""` and `[run] jobs = 3`: returns `3` (empty env falls back to config).
   - CPU cap: With `os.cpu_count()` mocked to `2`, `MARESTAIL_JOBS=16` returns `2`.
   - CPU cap fallback: With `os.cpu_count()` returning `None`, `MARESTAIL_JOBS=16` returns `1`.
   - Capping produces no output on stdout or stderr.
   - Validation: `MARESTAIL_JOBS=0` raises `SystemExit: MARESTAIL_JOBS must be a whole number 1 or more, got '0'`.
   - Validation: `[run] jobs = 0` raises `SystemExit: [run] jobs must be a whole number 1 or more, got 0`.
   - Dual validation: With `MARESTAIL_JOBS=5` and `[run] jobs = 0`, `run_jobs` raises `SystemExit: [run] jobs must be a whole number 1 or more, got 0` (broken tracked key is reported even when overridden).

3. `pick ok`.
   Invoke `jobs.pick(config, section, key, default)`.
   - `MARESTAIL_JOBS=3`, section `"python"`, key `"mutation_workers"=8`, `[run] jobs = 3` -> returns `3`.
   - `MARESTAIL_JOBS` unset, section `"python"`, key `"mutation_workers"=8`, `[run] jobs = 3` -> returns `8`.
   - `MARESTAIL_JOBS` unset, key missing, `[run] jobs = 3`, default `4` -> returns `3`.
   - `MARESTAIL_JOBS` unset, key missing, `[run] jobs` unset, default `4` -> returns `4`.
   - Raw language key preserved: section `"elixir"`, key `"muex_concurrency"=0` -> returns `0`.
   - No CPU cap on language keys: With `os.cpu_count()` returning `2`, section `"rust"`, key `"mutation_jobs"=16` -> returns `16`.

4. `runner_refusal ok`.
   - In a test repository with `tasks/t.md`, run `MARESTAIL_JOBS=0 bin/marestail run tasks/t.md --auto`.
     Expected: exit 1, stdout empty, stderr exactly `MARESTAIL_JOBS must be a whole number 1 or more, got '0'\n`, and no agent started.
   - With `[run] jobs = 0` in `marestail.toml` and env unset, run `bin/marestail run tasks/t.md --auto`.
     Expected: exit 1, stdout empty, stderr exactly `[run] jobs must be a whole number 1 or more, got 0\n`.

5. `gate_crash ok`.
   - In `marestail gate`, simulate a mutation gate where `jobs.pick` or `jobs.run_jobs` raises `SystemExit("MARESTAIL_JOBS must be a whole number 1 or more, got '0'")`.
   - Expected: Gate returns `Result` with `ok=False`, summary containing `crashed: SystemExit MARESTAIL_JOBS must be a whole number 1 or more, got '0'`, and remaining gates continue running.

6. `gate_commands ok`.
   Verify command-line construction across all 8 mutation gates:
   - `py.mutation`:
     - jobs unset: `mutmut run <patterns> --max-children 4`
     - N = 3: `mutmut run <patterns> --max-children 3`
     - `[python] mutation_workers = 8` and `[run] jobs = 3`: `--max-children 8`
     - `[python] mutation_workers = 8` and `MARESTAIL_JOBS=3`: `--max-children 3`
   - `ts.mutation`:
     - jobs unset: today's command without `--concurrency`
     - N = 3: `--concurrency 3` placed immediately after `--cleanTempDir always`, before `--mutate`
   - `ex.mutation`:
     - jobs unset: `--concurrency 4`
     - N = 3: `--concurrency 3`
     - `[elixir] muex_concurrency = 0`: `--concurrency` flag omitted
   - `rb.mutation`:
     - jobs unset: `bundle exec mutant run <subjects>` (no `--jobs`)
     - N = 3: `bundle exec mutant run --jobs 3 <subjects>`
   - `cs.mutation`:
     - jobs unset: today's command without `--concurrency`
     - N = 3: `--concurrency 3` placed immediately after `-r progress`, before `-m` patterns
   - `er.mutation`:
     - jobs unset: `ThreadPoolExecutor(max_workers=1)`
     - N = 3: `ThreadPoolExecutor(max_workers=3)`
   - `rs.mutation`:
     - jobs unset: `cargo mutants ... --jobs 2`
     - N = 3: `cargo mutants ... --jobs 3`
   - `java.mutation`:
     - jobs unset: `-Dthreads=2`
     - N = 3: `-Dthreads=3`

7. `rust_in_place ok`.
   - For `rs.mutation`, configure `mutation_args = ["--in-place"]`.
   - With jobs unset: command line contains `--in-place` and omits `--jobs`.
   - With `MARESTAIL_JOBS=3`: command line contains `--in-place` and omits `--jobs`.

8. `erlang_concurrency ok`.
   - Run `er.mutation` over 5 test mutants with 3 workers.
   - Verify that mutants run concurrently in threads while `write_report` outputs in manifest order.
   - The resulting `Result` and `.marestail/er-mutation.json` are byte-identical to a run with jobs unset (1 worker).

9. `findings_determinism ok`.
   - For `rs.mutation`, provide 70 surviving mutant outcomes across multiple files in shuffled order.
     Verify that the returned findings list is capped at 60 and strictly sorted by `(repo_relative_path, line_as_int, finding_text)`.
   - For `rb.mutation`, feed session outcomes with subjects arriving in different orders.
     Verify that findings are sorted by `(repo_relative_path, line_as_int, finding_text)` before the 60-finding cut.

10. `hermetic_and_imports ok`.
    - Inspect `tests/conftest.py`: autouse `hermetic` fixture includes `monkeypatch.delenv("MARESTAIL_JOBS", raising=False)`.
    - Inspect `.importlinter`: `marestail.jobs` is placed in the layer containing `marestail.context` and `marestail.ran_against`, and imports only `os`, `re`, and `marestail.config`.

11. `docs_and_templates ok`.
    - `templates/marestail.toml` contains `[run]` with `# jobs = 4  # parallel workers for every mutation gate; MARESTAIL_JOBS wins over it`.
    - Shipped per-language worker keys are commented out: `# mutation_workers = 4`, `# mutation_jobs = 2`, `# mutation_threads = 2`.
    - `README.md` documents the worker knob, tool flags, precedence, CPU capping, `--in-place`, and Maven `<threads>`.
    - `README.md` environment table contains the `MARESTAIL_JOBS` entry.
    - `.agents/skills/add-language/SKILL.md` and `.claude/skills/add-language/SKILL.md` document the mutation parallel flag contract bullet.

12. `pytest ok`.
    - Run `python -m pytest tests/test_jobs.py tests/test_gates_*_mutation.py tests/test_runner_flow.py -q`.
    - Expected: All tests pass with 100% coverage and zero CRAP regressions.
