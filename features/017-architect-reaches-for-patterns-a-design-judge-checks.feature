Feature: Architect applies pattern rules; a design judge checks them

  After this task, install ships language rulebooks and `guidance/patterns/`,
  the architect applies a pattern only when its trigger is in code this task
  touched, and a design judge between architect and practices bounces back for
  a missing trigger, a wrong form, or an unexplained trigger. Practices still
  reads only top-level `guidance/*.md`.

  Background:
    Given this marestail-green checkout with its venv active

  Scenario: the architect role gains one pattern paragraph
    Given `roles/architect.md` still contains "Prefer a few deep modules over many shallow ones"
    Then it also contains one new paragraph, and no other edit, with all of these:
      | text |
      | Read `guidance/patterns/*.md`. |
      | apply a pattern only when its trigger is present, and only in the rule's form |
      | Never add a dependency. |
      | `## Proposals` |
      | `## Patterns` |
      | `- <rule id> <file:line>: <what changed>` |
      | one line saying why |
      | A factory or wrapper that hides nothing is the shallow module the role already rejects. |
    And the paragraph limits patterns to code this task touched

  Scenario: design is this judge step and no other fields
    Then `find("design")` equals `Judge("design", None, bounce_to="architect", optional=True)`
    And `bounces` is 0, `pause_after` is false, `writes` is empty, `pinned_bounce` is false, and `targets` is empty
    And `marestail/tui/theme.py` `JUDGE_ROLES` contains `design`
    And `theme.role_attr(mono, "design")` equals `mono.judge`
    And `templates/marestail.toml` gains only this commented block beside `[practices]`:
      """
      # [design]
      # enabled = true  # the design judge runs between architect and practices unless false; skips repos with no guidance/patterns/*.md
      """

  Scenario: design skips when no pattern rulebook exists
    Given a repo whose `guidance/patterns/` is missing or has no `*.md`
    When `run_step` runs on `find("design")`
    Then it returns success and stdout is exactly `design: no pattern rulebooks; skipping`
    And `run_judge_loop` is not called

  Scenario: design disabled wins over a missing rulebook
    Given `[design] enabled = false` and `guidance/patterns/` has no `*.md`
    When `run_step` runs on `find("design")`
    Then it returns success and stdout is exactly `design disabled in marestail.toml; skipping`
    And stdout does not contain `no pattern rulebooks`

  Scenario: design runs when a pattern rulebook exists and it is enabled
    Given `guidance/patterns/ts.md` exists and `[design] enabled` is not false
    When `run_step` runs on `find("design")`
    Then stdout contains neither skip line and `run_judge_loop` is called

  Scenario: a pattern applied with no trigger bounces to the architect
    Given `guidance/patterns/ts.md` has rule TS-P9 Factory whose trigger is "the same construction sequence at 3 or more call sites" and whose form is "a plain function"
    And the architect handoff contains the line `- TS-P9 src/order.ts:4: replaced new Order() with order()`
    And the task diff touches only `src/order.ts` line 4, `return new Order(id)`, one construction
    When the design judge runs
    Then the verdict file starts `VERDICT: BOUNCE` and a numbered finding contains `TS-P9 src/order.ts:4`
    And the runner re-enters the architect (`bounce_to`, no other target)
    And `roles/design.md` states this fixture and that verdict

  Scenario: a pattern in the wrong form bounces to the architect
    Given the Iterator rule's form is `IEnumerable<T>` and `yield return`, not a hand-written cursor
    And the handoff contains `- CS-P16 src/Walk.cs:10: added a class with Current and MoveNext`
    And `src/Walk.cs` line 10 is that hand-written `Current` / `MoveNext` class
    When the design judge runs
    Then the verdict file starts `VERDICT: BOUNCE` and a numbered finding contains `CS-P16 src/Walk.cs:10`
    And the runner re-enters the architect
    And `roles/design.md` states this fixture and that verdict

  Scenario: an unexplained trigger in touched code bounces to the architect
    Given the WaitGroup rule's trigger is `Add(1)` / `go func` / `defer Done()` and its form is `sync.WaitGroup.Go` when `go.mod` says `go 1.25` or newer
    And `go.mod` contains `go 1.25` and the diff adds at `src/pool.go:18`:
      """
      wg.Add(1)
      go func() {
          defer wg.Done()
      }()
      """
    And `## Patterns` does not cite that rule or that line
    When the design judge runs
    Then the verdict file starts `VERDICT: BOUNCE` and a numbered finding contains `GO-P27` and `src/pool.go:18`
    And the runner re-enters the architect
    And `roles/design.md` states this fixture and that verdict

  Scenario: an explained skip passes
    Given the same `src/pool.go:18` trigger and `go.mod` contains `go 1.22`
    And `## Patterns` contains `- GO-P27 src/pool.go:18: not applied because go.mod says go 1.22, which is below 1.25`
    When the design judge runs
    Then the verdict file starts `VERDICT: PASS` and does not bounce for `src/pool.go:18`
    And `roles/design.md` states this fixture

  Scenario: a pass lists pre-existing triggers and omits an empty section
    Given the only `Add(1)` / `go func` / `defer Done()` sequence is `src/old.go:3`, a file the task diff does not touch
    And `## Patterns` explains every trigger in the diff
    When the design judge passes
    Then the verdict contains `## Pre-existing` and the line `- GO-P27 src/old.go:3: Add(1) / go func / defer Done()`
    And a pass with no such line omits `## Pre-existing`
    And the judge does not bounce for `src/old.go:3`, for taste outside the rulebooks, or for a trigger the architect explained
    And `roles/design.md` states this fixture

  Scenario: the same design findings twice stop for a human
    Given the previous design bounce's numbered findings equal this bounce's
    When design bounces again
    Then stdout is `design repeated the same findings twice; the worker is not making progress, stopping for a human`
    And the run stops without re-entering the architect
    And the first bounce did re-enter the architect

  Scenario: full install copies rulebooks by marker and does not overwrite
    When `marestail install` runs on a repo with a root `Gemfile`, a root `mix.exs`, a root `go.mod`, `src/nested/a.erl`, and `src/App.csproj`
    Then the target has `guidance/ts.md`, `cs.md`, `rb.md`, `ex.md`, `er.md`, and `go.md`
    And it has `guidance/patterns/ts.md`, `cs.md`, `rb.md`, `ex.md`, `er.md`, and `go.md`
    When `marestail install` runs on a repo whose only marker is `src/nested/a.erl`
    Then it has `guidance/er.md` and `guidance/patterns/er.md`, and not `rb.md`, `ex.md`, `go.md`, or `cs.md`
    When `marestail install` runs on a repo whose only extra files are `nested/Gemfile`, `nested/mix.exs`, and `nested/go.mod`
    Then it does not gain `guidance/rb.md`, `ex.md`, or `go.md`, nor their `guidance/patterns/` twins
    When `marestail install` runs on a bare repo
    Then among guidance markdown it has only `guidance/ts.md` and `guidance/patterns/ts.md`
    And a second install leaves an edited `guidance/patterns/ts.md` and an edited `guidance/go.md` byte-for-byte
    And `uses_erlang` is any `*.erl` under the target, like `uses_csharp` is any `*.csproj`
    And `uses_elixir`, `uses_ruby`, and `uses_go` are true only for `mix.exs`, `Gemfile`, and `go.mod` at the repo root

  Scenario: hyper install keeps today's file set
    Then `hyper_files` on a bare repo is exactly `PERFORMANCE.md`, `guidance/ts.md`, `marestail.toml`, `tasks/README.md`
    And with a `.csproj` anywhere it also has `guidance/cs.md` and no other key
    And `--scope hyper` does not add `guidance/er.md`, `ex.md`, `rb.md`, `go.md`, or any `guidance/patterns/*`
    And `tests/test_install_hyper.py` keeps that key set

  Scenario: practices never sees pattern rulebooks
    Given `guidance/ts.md` and `guidance/patterns/ts.md` both exist
    When `practices.files` lists rulebooks
    Then every path is a top-level `guidance/*.md` and none is under `guidance/patterns/`
    And `practices.files` still uses `guidance/*.md`, not a recursive glob

  Scenario: ported rulebooks stay byte-identical and Ruby stops at RB-48
    Then `templates/guidance/ts.md` and `cs.md` match `git show origin/main:templates/guidance/ts.md` and `cs.md`
    And `templates/guidance/er.md` and `ex.md` match `git show dfdb550:templates/guidance/er.md` and `ex.md`
    And `templates/guidance/rb.md` matches `git show dfdb550:templates/guidance/rb.md`, whose last rule is RB-48
    And `4148a1e` is not the source, and no file invents RB-49 through RB-73
    And `patterns/rb.md` cites only RB ids that exist in that blob

  Scenario: pipeline order, window, hyper, and the dry run
    Then `names()` is `specifier, critic, coder, cleaner, architect, design, practices, perf, hardener, qa`
    And `window("design", "design")` is `design`
    And `window("architect", "practices")` is `architect, design, practices`
    And `window("coder", "architect")` is `coder, cleaner, architect`
    And `window("coder", None)` is `coder, cleaner, architect, design, practices, perf, hardener, qa`
    And a disabled design step stays in `window("architect", "practices")` because `window` does not read config
    And `names("hyper")` is `specifier, critic, coder, architect, blast, hardener, qa`
    And `names(None, visual=True)` inserts `design` between architect and practices, and `names("hyper", visual=True)` does not include `design`
    And `find("design", "hyper")` exits with `unknown role design; choose from specifier, critic, coder, architect, blast, hardener, qa`
    And the default-mode unknown-role text in `tests/test_pipeline.py`, `tests/test_runner_flow.py`, `tools/test-practices.py`, `tools/test-perf.py` `pipeline_order`, `tools/test-run-hyper.py` `ALL_ROLES` only, and `tools/test-visual-judge.py` gains `design` between architect and practices
    And no hyper role list gains `design`, and `_HYPER_DROPPED` gains `design` beside cleaner, practices, and perf
    And `tests/test_pipeline.py` `test_visual_follows_the_hardener_only_when_asked` follows those lists
    And outside hyper, tiers of steps that have one stay `fast, sonar, sonar, full, qa` because design's tier is none
    And `tools/test-run-hyper.py` `TOML` gains `[design]\nenabled = false`, leaving `HARD_PLAN` unchanged
    And `tools/test-run-hyper.py` `readme_documents` expects hyper cells `none, none, full, —, full, —, —, —, none, full, visual, qa`, blast checks `rows[8:11]` for perf, blast, hardener, and finds blast at `rows[9]` as Kind `judge`, Gate `—`, hyper `none`
    And `tools/dryrun-plan.txt` has `judge design` immediately after `worker architect`, and the next line is still `judge BOUNCE`
    And `tools/stub-claude` writes `VERDICT: PASS` for the plan line `judge design`, and still writes `VERDICT: BOUNCE` for `judge BOUNCE`
    And `tools/dryrun.sh` seeds `guidance/ts.md` and a `guidance/patterns/*.md` before the run, exits 0, and prints `remaining plan lines: 0`
    And that `judge BOUNCE` is still practices

  Scenario: pattern rulebooks and the Go practices rulebook match the sheet
    When I run `python3 tools/test-pattern-rulebooks.py`
    Then the exit code is 0 and the last line is `patterns ok`
    And `tests/test_green_repo.py` `PASSING_SCRIPTS` includes `test-pattern-rulebooks.py`
    And the only files in `templates/guidance/patterns/` are `cs.md`, `ts.md`, `rb.md`, `ex.md`, `er.md`, and `go.md`
    And there is no `patterns/py.md` and no `templates/guidance/py.md`
    And each pattern rule is `- **<LANG>-P<n> — <name>.**` plus non-empty `pattern:`, `trigger:`, `form:`, and `not when:` lines
    And the table order in each file is the rule id order sequential from 1, and the `pattern:` names are exactly the set below, once each
    And `templates/guidance/go.md` is `# Go best practices`, one line that the practices judge applies it to `*.go` changes, then exactly `GO-1` through `GO-10`, each non-empty, with no doc-comment requirement (`doc comment` and `godoc` appear in neither Go file)
    And `GO-1` through `GO-10` cover, in order: return `error` last, handle it or return it, wrap with `%w`, match with `errors.Is` and `errors.As`; `panic` only for programmer errors and `recover` only at goroutine and handler boundaries; `context.Context` first, never stored in a struct, values for request metadata only; no goroutine without a known stop, tied to a context and waited for, tickers stopped; accept interfaces and return structs, interfaces declared by the consumer, kept small; a useful zero value; table-driven tests with `t.Run` and tests run with `-race`; `log/slog` passed explicitly; nothing in `init()`; generics only for containers and algorithms
    And the `log/slog` rule names Go 1.21 and applies only when `go.mod`'s `go` line is at least 1.21
    And any rule that names `errors.Join` or `WithCancelCause` also says 1.20 and `go.mod`; `errors.AsType` says 1.26; `sync.OnceValue`, `sync.OnceFunc`, `slices.Clone`, `maps.Clone`, or `context.AfterFunc` says 1.21; `iter.Seq` or the `unique` package says 1.23; `sync.WaitGroup.Go` or `testing/synctest` says 1.25
    And a library token `MediatR`, `Polly`, `XState`, `Oban`, `Broadway`, `Scrutor`, `Draper`, `Stateless`, `neverthrow`, `errgroup`, `singleflight`, `cobra`, `viper`, `envconfig`, `sqlc`, `pgx`, `participle`, `gproc`, `syn`, `PropEr`, `recon`, `wire`, or `fx` (whole word), or the path `golang.org/x/sync` or `golang.org/x/time/rate`, appears only inside a rule that also says `already a dependency`
    And these strings do not appear in any pattern file: `singleflight`, `graceful shutdown`, `golang.org/x/sync`, `state_functions`, `doc comment`
    And `patterns/er.md` contains `simple_one_for_one` only in a rule that also says `not allowed` and `ER-18`
    And `patterns/cs.md` Iterator trigger contains `hand-written` and its form contains `IEnumerable`
    And `patterns/ts.md` Iterator trigger contains `hand-written` and its form contains `Symbol.iterator`
    And `patterns/ts.md` Singleton trigger contains `hand-written` and its form contains `ES module`
    And `patterns/ts.md` Factory form contains `plain function`, `TS-11`, and `TS-15`
    And `patterns/ex.md` Memento trigger contains `hand-written` and its form contains `immutable`
    And `patterns/ex.md` and `patterns/er.md` gen_statem triggers contain `status` and `handle_`
    And `patterns/er.md` gen_statem form contains `handle_event_function`, and `patterns/ex.md` gen_statem form contains `gen_statem`
    And `patterns/go.md` WaitGroup trigger contains `Add(1)`, `go func`, and `Done`, and its form contains `sync.WaitGroup.Go`, `1.25`, and `go.mod`
    And `patterns/go.md` Iterator form contains `iter.Seq`, `1.23`, and `go.mod`
    And `patterns/er.md` Iolist trigger contains `<<Acc/binary, X/binary>>` and its form contains `iolist`
    And `patterns/er.md` Send after trigger contains `timer:send_interval` and its form contains `erlang:send_after`
    And `patterns/rb.md` contains `RB-4`, does not contain `method_missing` or `define_method`, has no pattern named Metaprogramming, cites `RB-12` for Adapter without restating its form, cites `RB-8` for Concern without restating its form, cites `RB-9` for Value object without restating its form, cites `RB-11` for Refinement without restating its form, and cites `RB-16` for Service object without restating its form with not when citing `RB-16`
    And `patterns/ex.md` contains `EX-9`, `EX-10`, and `EX-11`, and does not contain `holds a list you could pass`, `LiveView already gives a process`, or `one named GenServer`
    And `patterns/er.md` contains `ER-1`, `ER-2`, `ER-4`, `ER-6`, `ER-9`, `ER-10`, `ER-13`, `ER-14`, `ER-15`, `ER-16`, `ER-17`, `ER-18`, `ER-19`, `ER-20`, `ER-22`, `ER-28`, `ER-29`, `ER-30`, and `ER-33`
    And `patterns/er.md` does not contain `no swallowing a DB crash`, `error threading is a finding`, `no sys/debug`, `pg2 is gone`, or `gen_fsm`
    And `patterns/er.md` does not contain `a case on a shape this module owns` (ER-4), `#mod_state{}` (ER-6), `heavy startup` (ER-10), `tightly coupled` (ER-14), `pg:get_members` (ER-20), `OpenTelemetry context` (ER-22), `ensure_all_started` (ER-28), or `is_binary(Id)` (ER-33), unless the only mention is `see ER-n`
    And each React pattern's own rule contains `React code`: Custom hooks, Compound components, Provider, Controlled and uncontrolled, Reducer, Render props, Composition over configuration, Client data caching
    And each Rails pattern's own rule contains `Rails app`: Value object, Service object, Form object, Query object, Presenter, Policy, Concern
    And `patterns/go.md` does not contain `%w`, `errors.Is`, `log/slog`, or `-race`, and `guidance/go.md` does not contain `WaitGroup.Go` or `iter.Seq`
    And the last non-empty line of each pattern file is exactly:
      | file | line |
      | cs.md | Canonical: GoF (1994); Refactoring.Guru; Fowler's PoEAA. |
      | ts.md | Canonical: Osmani, Learning JavaScript Design Patterns; patterns.dev; Vanderkam, Effective TypeScript. |
      | rb.md | Canonical: Olsen, Design Patterns in Ruby; Metz, POODR; Helmkamp, 7 Patterns to Refactor Fat ActiveRecord Models. |
      | ex.md | Canonical: Jurić, Elixir in Action; OTP Design Principles; Gospodinov, Concurrent Data Processing in Elixir. |
      | er.md | Canonical: Armstrong's thesis; OTP Design Principles; Hébert, Learn You Some Erlang; Hébert, Erlang in Anger. |
      | go.md | Canonical: Effective Go; Go Proverbs; Cheney, Practical Go; Harsanyi, 100 Go Mistakes. |
    And the pattern names are exactly:
      | file | names |
      | cs.md | Factory Method, Abstract Factory, Builder, Prototype, Singleton, Adapter, Bridge, Composite, Decorator, Facade, Flyweight, Proxy, Chain of Responsibility, Command, Interpreter, Iterator, Memento, Observer, State, Strategy, Template Method, Visitor, Dependency injection, Options, Repository, Result, Middleware, Producer/consumer |
      | ts.md | Strategy, Command, Observer, Iterator, Decorator, Adapter, Facade, Proxy, Factory, Builder, Singleton, Composite, State, Chain of Responsibility, Visitor, Memento, Template Method, Module, Function composition, Dependency injection, Immutability, Custom hooks, Compound components, Provider, Controlled and uncontrolled, Reducer, Render props, Composition over configuration, Client data caching, Discriminated union, Branded types, Const assertion, Template literal types, Overloads, Mapped types |
      | rb.md | Template Method, Strategy, Observer, Composite, Iterator, Command, Adapter, Proxy, Decorator, Singleton, Factory, Builder, Interpreter, Block, Mixin, Delegation, Null Object, Internal DSL, Guard clause, Refinement, Value object, Service object, Form object, Query object, Presenter, Policy, Concern |
      | ex.md | Process or module, Async stream, Unlinked task, Linked task, GenServer, Agent, DynamicSupervisor, handle_continue, Rescheduled tick, gen_statem, Pool, Monitor, Supervisor strategy, Behaviour, Protocol, Functional core, Token, Stream, Macro, Runtime config, Context, Ecto.Multi, Query object, Plug, Telemetry, Memento |
      | er.md | gen_statem, gen_event, Application, Error kernel, Monitor, Error class, Iolist, Send after |
      | go.md | Factory, Functional options, Prototype, Singleton, Adapter, Bridge, Composite, Decorator, Facade, Flyweight, Proxy, Chain of Responsibility, Command, Interpreter, Iterator, Mediator, Memento, Observer, State, Strategy, Template Method, Visitor, Embedding, Constructor injection, Enum, Bounded parallel work, WaitGroup, Mutex, Confinement, Timeouts, Ticker, Synctest, Handler struct, HTTP status mapping, Repository |

  Scenario: README documents the design row and the pattern paragraph
    Then the Pipeline table has this row directly between architect and practices:
      | Step | Kind | Gate | hyper |
      | design | judge | none | — |
    And that row's Does names the three bounces (no trigger, wrong form, unexplained trigger under `## Patterns`), the citation `<rule id> <file:line>`, and the skip line `design: no pattern rulebooks; skipping`
    And the sentence `Other languages get no shipped rulebook; write your own `guidance/<lang>.md` with one numbered rule per line.` is gone
    And the Best practices section keeps the practices paragraph, then one new paragraph that says pattern rulebooks live in `guidance/patterns/`, the architect and the design judge read them, practices does not, install also ships `er.md` (any `*.erl`), `ex.md` (root `mix.exs`), `rb.md` (root `Gemfile`), `go.md` (root `go.mod`), and `guidance/patterns/<lang>.md` beside each installed language rulebook including `patterns/ts.md`, and design bounces only for those three cases
    And that paragraph states `[design]` key `enabled` defaults to true

  Scenario: other checks still pass
    Then `pytest tests/test_pipeline.py tests/test_runner_flow.py tests/test_install.py tests/test_install_hyper.py tests/test_tui_theme.py` passes and covers every new runner and install branch
    And every `tools/test-*.py` that exits 0 today still exits 0 with a last line containing `ok`
    And `tools/test-perf.py` still exits 1 with last line `verdict-commit-files: '' != 'perf/bench_x.py'`
