+++
depends = []
+++
# 029 — one hung mutant cannot stall a whole muex run

After this task, a muex mutant that stops the app from starting while it keeps logging is killed after a fixed time. muex scores it and the run carries on. A single mutant can no longer hold `ex.mutation` open indefinitely.

On 2026-09-29, animus-chat's muex sat for 13 minutes on one worker at 2% CPU while every other worker was idle. The worker was running `application_children_test.exs` and its neighbours. The mutant had broken application start-up, and the app logged `[Bridge] … gateway unavailable` every few seconds. muex's `--timeout` is an idle timeout. In `deps/muex/lib/muex/test_runner/port.ex`, `collect_output/3` restarts its `after timeout_ms` on every chunk of output, so it never fired. Killing the worker by hand let the run finish. muex reran the tests unmutated, saw them pass, and scored the mutant `invalid` (`worker_pool.ex`, `blame/3`).

The live marestail fixed this in commit b9b4fd1. The underlying fix, a wall-clock limit per run, belongs upstream in `github.com/Oeditus/muex`.

## What changes

- **`ex.mutation` wraps muex in a watchdog:** `bash -c <script> muex-watchdog <limit> <poll> mix muex …`.
- **The watchdog's check:** every `poll` seconds (15), it looks for any process matching `mix test --max-failures` that is a descendant of this muex and older than `limit` seconds. It kills that process's tree and prints `marestail: killed a muex test run after <n>s (limit <limit>s)` to stderr.
- **The limit:** `[elixir] muex_run_limit`, default 300.
- **The watchdog's own script must never match its pattern.** Split the literal, as in `"mix test --max""-failures"`.
- **The watchdog exits with muex's exit status.**

## What must not change

- The muex command inside the wrapper, its report parsing and its summary.
- Other beds' muex runs. The watchdog only touches descendants of its own muex.

## Tests

`tools/test-muex-watchdog.py` uses fake runs started with `exec -a "…mix test --max-failures 1…" sleep N`, building the name from pieces so the parent shell never matches:
- A run past the limit is killed within a few seconds, and the wrapped command carries on.
- A run under the limit is left alone.
- A matching process outside the tree survives.

## Done when

The test passes, the other `tools/test-*.py` still pass, and README says what `muex_run_limit` is for.
