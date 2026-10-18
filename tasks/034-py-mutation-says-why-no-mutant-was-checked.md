+++
depends = []
+++
# 034 — `py.mutation` says why mutmut checked no mutant

After this task, when mutmut stops before it tests a single mutant, `py.mutation` fails with what mutmut printed, not with thousands of `not checked` mutants. The commonest cause is a test that passes in the repo but fails in mutmut's `mutants/` copy of it. That cause now shows up as the failing test, with a command to reproduce it.

On 2026-10-18 this cost task 024 on the runner VM about three hours. A coder made `test_module_imports_only_os_and_re` count `from` imports. The test parses `marestail/run_suffix.py` from disk, and in `mutants/` that file carries `from mutmut.mutation.trampoline import ...`. So mutmut's clean test run failed, mutmut printed `Failed to run clean test` and exited 1 after 50 s, and every `.meta` exit code stayed null. The gate reported `16032 of 16032 mutants not killed (on marestail-mutation-usw4, 50s)` and 30 `…: not checked` lines. Five hardeners in a row read that as a fault on the box and wrote PASS. The failed gate turned each PASS into a bounce, until the runner stopped on repeated findings. The failing test never reached a report: `mutate()` reports `mutmut failed` only when the output lacks the word "mutants", and mutmut's output always contains it.

## What changes

- **mutmut checked no mutant.** mutmut exited non-zero, and every mutant in `mutants/` has a null exit code (or there are no `.meta` files). The gate fails with summary `mutmut checked no mutant`, followed by the usual where note: `mutmut checked no mutant (on marestail-mutation-usw4, 50s)`. Its findings are the tail of mutmut's output (`shell.tail`, 30 lines), not the mutant list.
- **The clean run failed.** The output also contains `Failed to run clean test`. The summary is `the tests fail in mutmut's mutants/ tree, so no mutant was checked`, plus the where note. The first finding is `reproduce: cd <python root>/mutants && MUTANT_UNDER_TEST= <ctx.python_bin("python")> -m pytest`, with both paths absolute. The tail follows; it ends with pytest's own `FAILED …` lines and counts.

## What must not change

- If mutmut checked at least one mutant, the gate judges the run as today, even when mutmut exited non-zero. That includes survivors, `not checked` findings, timeouts and the `(n killed by timeout)` note.
- `mutmut failed` (non-zero exit, output without "mutants") and `no mutants were generated` keep their summaries and findings.
- The other mutation gates are not part of this task.

## Tests

- Exit 1, a `mutants/` tree whose `.meta` files hold only null exit codes, and output that ends with `FAILED tests/test_x.py::test_y - AssertionError`, `1 failed, 3 passed in 0.1s` and `Failed to run clean test`. The gate fails with the clean-run summary, the reproduce line first, and the `FAILED` line among the findings.
- The same with output that lacks `Failed to run clean test`. The summary is `mutmut checked no mutant`, and the findings are the tail.
- Exit 1 with one killed and one null mutant: judged as today, with one `not checked` finding.
- Both new summaries carry the where note when the job ran on the box.

## Done when

The tests pass, and README's mutation paragraph says what `py.mutation` reports when mutmut checks no mutant.
