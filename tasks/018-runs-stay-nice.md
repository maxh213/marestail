# 018 — runs stay nice: `marestail run` yields the machine to you again

After this task, `marestail run` drops itself to nice 19 with idle I/O and an OOM score of 500 before it starts any agent, so a fleet of pipelines gives the CPU and disk to the person at the keyboard and is first in line if the kernel runs out of memory. Every agent CLI, gate and hook the run spawns inherits it. Today green runs at the shell's priority: nothing in `marestail/` calls `setpriority`, `ionice` or `oom_score_adj`.

This already ships on main as `marestail/nice.py`, called once at main's `marestail/runner.py:113` (`nice.apply(config)`, the line after `config_module.load`), with `tools/test-nice.py` and a README paragraph at main's `README.md:72`. This task ports the same behavior into the rewritten runner with full pytest coverage, so merging green into main cannot lose it.

## Rule

Add `marestail/nice.py` with main's behavior, value for value:

- `DEFAULT = 19`, `OOM_SCORE = 500`, `ENV = "MARESTAIL_NICE"`.
- Where the level comes from, first match wins:
  1. `MARESTAIL_NICE`, when it is set at all, even to the empty string.
  2. `[run] nice` in `marestail.toml`, read with `config.get("run", "nice", DEFAULT)`.
  3. `DEFAULT` (19), when there is no config or no key.
- How a value is read:

| value | level |
|---|---|
| `true` | 19 |
| `false`, `None`, `0`, `"0"`, `""` | off |
| integer or integer string 1–19 | that level |
| integer or integer string above 19 | 19 (capped, not rejected) |
| negative, non-integer string (`"high"`, `"1.5"`), float or any other type | `SystemExit` |

- The error messages are main's, word for word: `nice must be an integer 0-19, got {raw!r}` for a string that is not an integer and for a value of any other type, and `nice must be an integer 0-19, got {value}` for a negative integer (so `"-4"` reads `got -4`, `"high"` reads `got 'high'`).
- When the level is off, `apply` does nothing else. Otherwise, in this order:
  1. `os.setpriority(os.PRIO_PROCESS, 0, level)`; `AttributeError` and `OSError` are ignored.
  2. `ionice -c 3 -p <own pid>`, with `check=False` and captured output; a missing `ionice` (`OSError`) is ignored, and so is a non-zero exit.
  3. Write `500` to `/proc/<own pid>/oom_score_adj`; `OSError` is ignored.

A machine where any of the three fails still runs the pipeline, as on main. A bad value stops the run before any agent starts.

## Where it applies

- `run_pipeline` in green's `marestail/runner.py` calls `nice.apply(config)` straight after `config = config_module.load(Path.cwd())` (line 203 today) and before `pick_model`. Children inherit priority, I/O class and OOM score, so every agent CLI, every gate the runner runs, the perf sample filling and every Stop hook an agent starts run at the same level. Nothing else needs to call it.
- `marestail gate`, `marestail watch` and `marestail perf run` stay at the shell's priority. main does the same: `nice.apply` has one caller, and main's README says `marestail watch` and `marestail gate` stay at the shell's priority. A hook started by an agent inside a run is niced only because its parent is; `marestail hook` run by hand is not.
- `.importlinter`: `marestail.nice` imports only `marestail.config`, so add it to the layer that holds `marestail.ran_against`.

## README

Green's README documents every variable marestail reads in its `## Environment variables` table, and documents config keys inline. Add:

- main's `README.md:72` paragraph to green's `## Use` section, after the code block of example commands, word for word.
- a row to the environment table: `` | `MARESTAIL_NICE` | priority for `marestail run`: 0 turns it off, 1–19 is the nice level (default 19); wins over `[run] nice` | ``.

## Must not break

- Every existing `run_pipeline` test. The `pipeline_env` fixture in `tests/test_runner_flow.py` must stub `nice.apply`, or the test suite renices and idles its own pytest process, which cannot be undone without root and would slow every later test and the mutation gate.
- Order of work in `run_pipeline`: config load, then nice, then `pick_model`, `pick_scope`, `share_scope`, `MARESTAIL_TASK`, `make_run`, `record_start`, steps. A bad `--model`/`--agent` combination still raises its own message; a bad nice value raises first.
- `marestail gate` output, timings and exit codes, and every hook reply.
- stdout of a run: the port prints nothing.

## Tests

`tests/test_nice.py`, in-process, with no real agent session and without changing the pytest process's own priority. Stub `os.setpriority`, `subprocess.run` (or whatever `nice.py` calls) and the `/proc` path with `monkeypatch` and `tmp_path`, and build `Config(root=tmp_path, raw=...)` directly, as `tests/test_runner_flow.py` does.

- `parse`: one test per row of the table above, including `True`, `False`, `None`, `""`, `0`, `"0"`, `10`, `"10"`, `99`, `"high"`, `-1`, `"-4"`, `"1.5"` and `1.5`; each `SystemExit` asserts the full message.
- `level`: no config gives 19; empty config gives 19; `[run] nice = 0` is off; `5` gives 5; `99` gives 19; env `0` beats a config of 19; env `7` beats a config of 19; env `""` is off; env `"high"` raises.
- `apply`: at the default it calls `setpriority(PRIO_PROCESS, 0, 19)`, runs `ionice -c 3 -p <pid>` and writes `500` to the oom path; when off it calls none of them; each of `setpriority` raising `OSError`, `setpriority` raising `AttributeError`, `ionice` raising `OSError` and the oom write raising `OSError` is swallowed and the later steps still run.
- `run_pipeline`: extend `pipeline_env` so it records `nice.apply` calls, and assert it is called once, with the loaded config, before `pick_model`; a bad nice value raises before `run_steps`.
- `gate`: a test in `tests/test_cli.py` that `marestail gate` never calls `nice.apply`.

Do not port `tools/test-nice.py`. Its parse and level checks become the tests above. Its subprocess probes (a child reads nice 19, a stub agent in a real `marestail run` reads nice 19) test the kernel's inheritance, not marestail's code, so the `run_pipeline` test above stands in for them.

## Done when

`tests/test_nice.py` passes with every branch of `nice.py` covered, the existing tests still pass, `run_pipeline` calls `nice.apply(config)` once after loading config, `.importlinter` lists `marestail.nice`, and the README carries main's paragraph and the `MARESTAIL_NICE` row.
