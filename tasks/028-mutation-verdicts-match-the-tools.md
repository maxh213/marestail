+++
depends = []
+++
# 028 — a mutation gate fails only on mutants the tests could have caught

After this task, a mutant the compiler rejected, or one that made the tests run past their time limit, no longer fails a mutation gate. Every gate counts those the way its own tool does. A summary still says how many were killed by timeout, so a jump stays visible.

On 2026-09-29, virtual-adoptions' coder and hardener bounced ten rounds on an unchanged tree. That cost about $3.50 a round, over a `ts.mutation` gate nothing could pass: 3800 killed, 2016 `CompileError`, 0 survived. With `checkers: ["typescript"]`, a `CompileError` means the compiler killed the mutant, and Stryker leaves it out of its score. The same day, animus-chat's muex reported 7 survivors that were all timeouts. bindweed's Stryker reported 64 timeouts while the box was overloaded. The only test change that removes those is weakening the types, or no change at all.

The gates also disagreed with each other. `java.mutation` (PIT `TIMED_OUT`), `rs.mutation` (cargo-mutants `Timeout`), `rb.mutation` (mutant) and `er.mutation` already counted a timeout as killed. `ts`, `cs`, `py` and `ex` counted it as a survivor. The live marestail made this change in commits c67395d, 6a56fef and 0d2cb65.

## What changes

- **`ts.mutation`:** `CompileError` and `Timeout` are not findings.
- **`cs.mutation`, `py.mutation`, `ex.mutation`:** a timeout (Stryker.NET `Timeout`, mutmut's `timeout` exit codes, muex `timeout`) is not a finding.
- **Every one of those summaries** appends ` (<n> killed by timeout)` when n > 0, for example `5 of 1973 mutants not killed (7 killed by timeout)`. The wording must say they were killed. `(64 by timeout)` read as 64 of the survivors, and a hardener repeated it that way.
- **`[<lang>] mutation_timeouts = "fail"`** (`[ts]`, `[dotnet]`, `[python]`, `[elixir]`) restores the strict rule for a repo: a timeout is a finding and the note is dropped.
- **`ts.mutation` passes `--dryRunTimeoutMinutes 120`,** the gate's own 7200 s budget. Stryker's default of 5 minutes aborted whole runs on a busy laptop before any mutant was tested, and the gate then failed with no report.

## What must not change

- `Survived`, `NoCoverage` and `RuntimeError` (TS, C#) are still findings, as are mutmut's `survived`, `no tests` and `suspicious`, and muex's survivors.
- `cs.mutation` still counts `CompileError` as a finding. That is a separate decision, not taken here.
- Gates that already counted timeouts as killed are untouched.

## Tests

For each of `ts`, `cs`, `py` and `ex`, a canned report or meta with one killed, one timeout and one survived mutant:
- By default: one finding, and the summary ends `(1 killed by timeout)`.
- With `mutation_timeouts = "fail"`: two findings, and no note.

Also:
- A `ts` report with only `Killed` and `CompileError` passes.
- `ts.mutation`'s command contains `--dryRunTimeoutMinutes 120`.

## Done when

The tests pass, the other `tools/test-*.py` still pass, and README's mutation paragraph states the rule and the `mutation_timeouts` switch.
