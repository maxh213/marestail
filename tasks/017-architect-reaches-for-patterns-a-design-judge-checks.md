# 017 — the architect reaches for a design pattern when the code calls for one, and a design judge checks it

After this task, when the code a task touched has a real use for a pattern, the architect introduces it: for example, the same `switch` on a kind in three places becomes a discriminated union, or repeated construction becomes a factory function. It uses the form that is idiomatic in that language and cites the rule it applied. A new `design` judge runs straight after the architect. It bounces back to the architect when a pattern was added without its trigger being there, or when a clear trigger in the touched code was missed.

## Source

`.marestail/phases/sources/design-patterns-cheat-sheet.md` is Max's cheat sheet for C#, TypeScript, Ruby, Elixir, Python, Erlang and Go. It is untracked input: read it, transform it, and never copy it into the repo whole. Use every section except Python, which gets no rulebook in this task.

## First: bring main's rulebooks across

The conflicts below cite RB-4, EX-9..11 and ER-n, but green ships only `templates/guidance/ts.md` and `cs.md` (`marestail/_install.py:81-83`). main added `er.md`, `ex.md` and `rb.md` in commit `dfdb550`. Copy those three files from `main` unchanged, rule ids included (`git show dfdb550:templates/guidance/er.md`, and the same for the others). Take `rb.md` from main's later commit `4148a1e` instead (`git show 4148a1e:templates/guidance/rb.md`): it adds RB-49…RB-73, and RB-52…RB-73 are already the sheet's Ruby section as practices rules. `patterns/rb.md` keeps the architect's trigger/form/not-when shape but must not restate those rules; where a pattern is covered, cite the RB id instead of writing a second rule. Port main's install detection into green's `_install.py` beside `uses_csharp`:

- `uses_erlang`: any `*.erl` under the target installs `guidance/er.md`.
- `uses_elixir`: a `mix.exs` at the root installs `guidance/ex.md`.
- `uses_ruby`: a `Gemfile` at the root installs `guidance/rb.md`.

This is a port. Change no wording.

## Go practices rulebook

Create `templates/guidance/go.md` in the format of `cs.md` and `er.md`: `# Go best practices`, one line saying it is applied by the practices judge to `*.go` changes, then numbered rules `GO-<n>` as `- **GO-n — <rule>.** <explanation>`. Take them from the sheet's Go rows that are always-or-never rules, checkable on any diff with no trigger:

- errors are values: return `error` last, handle it or return it, wrap with `%w`, match with `errors.Is` / `errors.As`;
- `panic` only for programmer errors, and `recover` only at goroutine and handler boundaries;
- `context.Context` as the first parameter, never stored in a struct, and its values for request metadata only;
- no goroutine without a known way to stop, tied to a context and waited for; tickers stopped;
- accept interfaces and return structs; interfaces declared by the consumer, kept small;
- useful zero values;
- table-driven tests with `t.Run`, and tests run with `-race`;
- `log/slog` loggers passed explicitly;
- nothing done in `init()`;
- generics only for containers and algorithms.

No rule may require a doc comment. Go's doc-comment convention clashes with the no-comments rule, and 025 turns off the linters that enforce it. A helper added in a given Go release (the sheet marks 1.20, 1.21, 1.23, 1.25 and 1.26) is named with that release, and the rule applies only when the module's `go.mod` `go` directive is at least that release.

`marestail install` copies `guidance/go.md` when the target has a `go.mod` at its root (`uses_go`). That is a file check, so it needs none of 025's Go gates.

## Pattern rulebooks

Create `templates/guidance/patterns/cs.md`, `ts.md`, `rb.md`, `ex.md`, `er.md` and `go.md` from the sheet. Each file is a list of numbered rules, `<LANG>-P<n>` (`TS-P1`, `CS-P4`, `RB-P2`, `EX-P7`, `ER-P3`, `GO-P5`), and each rule has four parts:

- **pattern**: the name.
- **trigger**: what must be visible in the code to justify the pattern. It has to be something a reader can point at with a file:line: "the same `switch` on a type discriminant in 2 or more functions", "the same construction sequence at 3 or more call sites", "a vendor SDK imported from more than one module". Rewrite vague rows ("construction has logic") into triggers like these, or drop them.
- **form**: the idiomatic shape in that language, taken from the sheet.
- **not when**: the case where the pattern is wrong.

Also transform:

- The sheet's "built in, never hand-write" rows (Iterator, Singleton as an ES module, Memento in Elixir) become rules whose trigger is a hand-written version.
- The Elixir "should this be a process at all?" table becomes rules with triggers. So do Go's "Concurrency: which tool when" table and Erlang's OTP behaviours table. For example, a `gen_server` whose state has a status field that several `handle_*` clauses switch on is the trigger for `gen_statem`. A hand-written `Add(1)` / `go func` / `defer Done()` sequence is the trigger for `sync.WaitGroup.Go` when `go.mod` allows 1.25.
- The Go rows that `go.md` (practices) already states become no pattern rule. Each Go row goes in exactly one of the two files: a rule that needs a trigger is a pattern, and an always-or-never rule is a practice.
- A Go pattern rule's form that uses a helper from a given Go release carries the same `go.mod` condition as the practices rulebook.
- Erlang idioms the sheet gives as a form can become patterns with triggers where main's `er.md` doesn't already cover them. Examples: repeated `<<Acc/binary, X/binary>>` in a loop becomes an iolist, and `timer:send_interval` inside a process becomes `erlang:send_after` rescheduled in `handle_info`.
- React/Next.js rules say they apply only to React code, and Rails rules say they apply only in a Rails app, as `ts.md` and `rb.md` already do.

Leave these out:

- Rows that change behaviour: `cast` to `call`, a GenServer moved to ETS or sharded, a rendering strategy per route. Behaviour is frozen for the architect.
- Rows whose form needs a library the target does not already declare (MediatR, Polly, XState, Oban, Broadway, Scrutor, Draper, Stateless, neverthrow, and for Go and Erlang: `golang.org/x/sync` (errgroup, singleflight), `golang.org/x/time/rate`, wire, fx, cobra, viper, envconfig, sqlc, pgx, participle, gproc, syn, PropEr, recon). A rule may name such a library only as "when it is already a dependency".
- Erlang's distribution patterns table, hot code loading, appups and releases, and the Mnesia, DETS and ETS-`heir` storage choices. They change behaviour or deployment, not code shape.
- Go rows that change what the program does at run time: making work concurrent or sequential, adding rate limiting, coalescing calls with singleflight, and graceful shutdown.
- The reference lists. One line at the bottom of each file names the sheet's canonical books.

Resolve these conflicts with main's rulebooks in favour of the rulebook:

- RB-4 forbids `method_missing` and `define_method` from interpolated strings. Drop the Ruby Proxy row's `method_missing` form and the Metaprogramming row.
- TS-15 wants named static factories on a class with a private constructor. The TS factory rule says: a plain function when there is no invariant to protect, and TS-15's form when the object is a class that has earned its place (TS-11).
- EX-9, EX-10 and EX-11 already cover "you probably don't need a GenServer". Refer to them by id and do not restate them.
- `er.md` already covers much of the sheet's Erlang section. The overlaps are: let it crash and tagged tuples (ER-1), `maybe` (ER-2), function heads and guards (ER-4, ER-33), records vs maps (ER-6), behaviours over hand-rolled loops, which covers `proc_lib` (ER-9), `handle_continue` (ER-10), supervisors and child specs (ER-14..18), names and `pg` (ER-19, ER-20), the process dictionary (ER-22), and EUnit, Common Test and PropEr (ER-28..30). Refer to these by id and do not restate them.
- Where the sheet and `er.md` disagree, `er.md` wins. The sheet lists `simple_one_for_one` as a strategy, and ER-18 forbids it. The sheet allows `gen_statem`'s `state_functions` mode, and ER-13 requires `handle_event_function`.

`marestail install` copies `patterns/<lang>.md` into the target's `guidance/patterns/` whenever it would install that language's rulebook (`uses_csharp`, `uses_erlang`, `uses_elixir`, `uses_ruby`, `uses_go`), and never overwrites an existing file. The `ts.md` that is always installed brings `patterns/ts.md` with it.

## The architect

Add one paragraph to `roles/architect.md`:

- Read `guidance/patterns/*.md`.
- For code this task touched, apply a pattern only when its trigger is present, in the rule's form.
- Never add a dependency. A pattern that needs one goes in the handoff under `## Proposals`.
- Under `## Patterns` in the handoff, list each pattern applied as `- <rule id> <file:line of the trigger>: <what changed>`, and each trigger seen but deliberately not acted on, with one line saying why.

Patterns must serve the architect's existing aim, deeper modules. A factory or wrapper that hides nothing is the shallow module the role already rejects.

## The design judge

- `Judge("design", None, bounce_to="architect", optional=True)` sits between `architect` and `practices` in `PIPELINE`.
- It is skipped with `design: no pattern rulebooks; skipping` when the target has no `guidance/patterns/*.md`, the same way practices skips.
- `[design] enabled = false` turns it off.
- `roles/design.md`: the judge reads the pattern rulebooks, the architect's handoff and the task's diff.
- It bounces for exactly three things, each cited as `<rule id> <file:line>`:
  1. a pattern the architect applied without its trigger present;
  2. a pattern in a form other than the rule's;
  3. a trigger plainly present in code this task touched that the architect neither applied nor explained under `## Patterns`.
- It never bounces on pre-existing code the task did not touch, on taste outside the rulebooks, or on a trigger the architect explained.
- It lists pre-existing triggers under `## Pre-existing` on a pass, as practices does.
- It follows the hardener's rule: when a bounce repeats the previous bounce's findings, the run stops for a human instead of looping.

## Must not break

- Practices still reads only `guidance/*.md` and never sees `guidance/patterns/`.
- Existing `guidance/*.md` files and their rule ids are unchanged.
- Pipeline order for every other role, `--from`/`--to` over the new role, and `window()` for ranges that skip it.
- Hyper scope's reduced pipeline has no architect, so it has no design judge either.
- The stub dry run passes with `judge design` added to `tools/dryrun-plan.txt` after `worker architect`.

## Tests

- `tools/test-pattern-rulebooks.py` is a deterministic check of every file under `templates/guidance/patterns/`:
  - every rule has an id of the form `<LANG>-P<n>`, and the ids are unique and sequential;
  - every rule has non-empty trigger, form and not-when parts;
  - no rule names a banned library unless the rule's text also says "already a dependency";
  - no rule restates an EX-9..11, RB-4 or listed ER-n form;
  - every Go rule that names a helper from a given release states that release.
- Install: on a sample with a `.csproj`, a `Gemfile`, a `mix.exs`, an `*.erl` file, a `go.mod`, and on a bare repo, the right `guidance/*.md` and `guidance/patterns/*.md` appear; an edited existing file survives a second install.
- `tools/test-pattern-rulebooks.py`'s id and part checks also run over `templates/guidance/go.md`'s `GO-n` rules: ids unique and sequential, every rule non-empty.
- Runner: design is skipped without pattern rulebooks, runs with them, bounces to the architect, and `[design] enabled = false` skips it; `window("architect", "practices")` includes design.
- Pytest coverage of every new branch in the runner and install.

## Done when

The new tests pass, the other `tools/test-*.py` still pass, and README has a pipeline table row for `design` and one short paragraph under Best practices saying that pattern rulebooks live in `guidance/patterns/`, who reads them, and what the judge bounces for.
