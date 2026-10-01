# Elixir pattern rulebooks

Read by the architect and the `design` judge for `*.ex`/`*.exs`/`*.heex` changes. Each pattern names the trigger that calls for it, the form the practices rulebooks accept, and when to leave it alone; the cited `EX-n` rules are the authority and are not restated here.

- **EX-P1 — Process or module.**
  pattern: Process or module — a module of functions plus a struct before any process.
  trigger: code needs a home, and no runtime property is involved.
  form: a plain module; when a process is warranted, a GenServer whose shell is thin (see EX-10).
  not when: concurrency, fault isolation, shared state or background work is the reason (see EX-9, EX-10).

- **EX-P2 — Async stream.**
  pattern: Async stream — the same operation over many items, bounded and ordered.
  trigger: many independent items can be processed at once, and the caller waits for all of them.
  form: `Task.async_stream/3` with `max_concurrency`, plus `timeout` and `on_timeout: :kill_task` for stragglers.
  not when: sequential work is fast enough, or the results do not need bounding.

- **EX-P3 — Unlinked task.**
  pattern: Unlinked task — fire-and-forget work that must not take its caller down.
  trigger: a crash in the background work must not crash the process that started it.
  form: `Task.Supervisor.start_child/2` or `Task.async_nolink/2` under a supervisor.
  not when: the caller needs the result — use a linked task.

- **EX-P4 — Linked task.**
  pattern: Linked task — concurrent work whose result the caller awaits.
  trigger: one or more tasks run beside the caller and the caller needs every result.
  form: `Task.async/1` then `Task.await/2`, so a crash propagates to the caller.
  not when: the caller cannot wait, or the work outlives the request.

- **EX-P5 — GenServer.**
  pattern: GenServer — shared mutable state, or one-at-a-time access to a resource.
  trigger: counters, connections and rate limiters that several callers must share.
  form: a GenServer whose callbacks wrap a pure module; the mailbox serialises access.
  not when: a module of functions and a struct is enough (see EX-9), or unrelated work would be serialised through one process (see EX-11).

- **EX-P6 — Agent.**
  pattern: Agent — a single value with trivial reads and updates.
  trigger: one value must be shared and updated, with no lifecycle or custom messages.
  form: `Agent.get/2` and `Agent.update/3` around that value.
  not when: custom messages, timeouts or lifecycle arrive — move to a GenServer.

- **EX-P7 — DynamicSupervisor.**
  pattern: DynamicSupervisor — one process per user, session, game or room.
  trigger: processes start on demand, one per concurrent entity, and must be findable by key.
  form: a `DynamicSupervisor` plus `Registry` names via `{:via, Registry, {MyRegistry, key}}` (see EX-12).
  not when: the children are fixed at boot — a static supervisor is enough.

- **EX-P8 — handle_continue.**
  pattern: handle_continue — keep `init/1` fast and finish setup after it returns.
  trigger: a process does work in `init/1` that would block the supervisor while it boots.
  form: `init/1` returns, then `handle_continue/2` does the heavy work.
  not when: the first message cannot be served before that work is done — start it elsewhere.

- **EX-P9 — Rescheduled tick.**
  pattern: Rescheduled tick — recurring in-process work.
  trigger: a process must do something on a timer for as long as it lives.
  form: `Process.send_after(self(), :tick, ms)`, rescheduled from `handle_info/2`.
  not when: the work must survive restarts and deploys — that belongs in a durable queue.

- **EX-P10 — gen_statem.**
  pattern: gen_statem — a real state machine with timeouts.
  trigger: a protocol or session passes through named status values, and each transition gets its own `handle_` callback.
  form: `:gen_statem` (or the `gen_state_machine` wrapper), with state timeouts and postponed events built in.
  not when: the states are a couple of flags — pattern-matched function heads may be enough.

- **EX-P11 — Pool.**
  pattern: Pool — a bounded set of connections to an external resource.
  trigger: callers share a limited resource: a database, an HTTP service, a port.
  form: a pool such as NimblePool, Poolboy or DBConnection, supervised and sized on purpose.
  not when: the resource is per-process, or callers may open their own connection.

- **EX-P12 — Notify many.**
  pattern: Notify many — one event reaches every interested party.
  trigger: a broadcast must update every subscriber, and keeping a pid per subscriber in state is the alternative.
  form: `Registry.dispatch` for a single node, or `Phoenix.PubSub` for topics (see EX-12).
  not when: exactly one process must be told — a message or a monitor is enough.

- **EX-P13 — Monitor.**
  pattern: Monitor — learn that another process died without sharing its fate.
  trigger: a process watches a client or peer whose crash must not take it down.
  form: `Process.monitor/1` plus the `:DOWN` message in `handle_info/2`; link instead when the fates are tied.
  not when: the two processes live and die together — supervise them together.

- **EX-P14 — Supervisor strategy.**
  pattern: Supervisor strategy — decide what restarts together after a crash.
  trigger: a new supervised tree must choose its failure domain and know which state is rebuildable.
  form: `:one_for_one` for independent children, `:rest_for_one` when later children depend on earlier ones, `:one_for_all` when all must restart together; restart types are intentional.
  not when: there is a single child whose restart type already tells the story.

- **EX-P15 — Behaviour.**
  pattern: Behaviour — a contract several modules implement.
  trigger: implementations must be swappable: adapters, providers, and test doubles.
  form: `@callback` in the behaviour, `@behaviour` in the implementer, defaults via `__using__` and `defoverridable`.
  not when: there is one implementation and nothing swaps it — a plain module is enough.

- **EX-P16 — Protocol.**
  pattern: Protocol — polymorphism over data types, including types the app does not own.
  trigger: one operation must behave differently per type, and callers may bring their own types.
  form: `defprotocol` and `defimpl`, as `String.Chars`, `Enumerable` and `Jason.Encoder` do.
  not when: internal dispatch over shapes the module owns — a `case` is simpler.

- **EX-P17 — Functional core.**
  pattern: Functional core — pure decisions in a module, side effects in a thin shell.
  trigger: a process module mixes pure logic with effects, and tests need processes to exercise logic.
  form: a pure module (`TodoList`) plus a thin process wrapper (`TodoServer`); test the core without starting anything.
  not when: the module is already pure, or the work is all effect.

- **EX-P18 — Token.**
  pattern: Token — a struct threaded through a series of steps.
  trigger: a pipeline transforms the same structure step by step, as `%Conn{}`, `Ecto.Changeset` and `Ecto.Multi` do.
  form: each step takes the token and returns it, carrying what the next step needs.
  not when: the data is a plain value — a function pipeline is simpler.

- **EX-P19 — Stream.**
  pattern: Stream — process large or infinite data lazily.
  trigger: a collection is too large to materialise, or arrives from a file or socket.
  form: `Stream` for the pipeline, `Enum` to materialise, `File.stream!` for files.
  not when: the data is small — `Enum` reads better.

- **EX-P20 — Macro.**
  pattern: Macro — syntax a function call cannot express.
  trigger: a DSL needs new syntax, as Ecto schemas, the Phoenix router and ExUnit do.
  form: `defmacro` with quoted code and hygiene; the default answer is no.
  not when: a function, a `__using__` or a plain data structure would do.

- **EX-P21 — Runtime config.**
  pattern: Runtime config — read configuration at boot, not at build.
  trigger: a value differs per environment or must stay out of the build, such as a secret.
  form: `config/runtime.exs` and `Application.get_env/2` at boot; `Application.compile_env/3` only for build-time values.
  not when: the value never changes between environments.

- **EX-P22 — Context.**
  pattern: Context — a module with a public API that owns a domain boundary.
  trigger: callers outside the domain reach into `Repo` or schemas directly.
  form: an `Accounts` or `Catalog` module exposing what the domain offers; nothing else calls `Repo` for that domain.
  not when: one table and one query are all there is — a schema plus a function may be enough.

- **EX-P23 — Ecto.Multi.**
  pattern: Ecto.Multi — transactions built as data.
  trigger: several writes must all succeed or all fail, with the later ones depending on the earlier ones.
  form: a `Multi` built up step by step and run with `Repo.transaction/1`.
  not when: a single write is the whole unit — it is already atomic.

- **EX-P24 — Query object.**
  pattern: Query object — reusable query logic with a name.
  trigger: the same query is built in several places or has outgrown a scope.
  form: functions that take and return an `Ecto.Query`, composed by the caller.
  not when: one `where` clause in one caller is the whole query.

- **EX-P25 — Plug.**
  pattern: Plug — cross-cutting work for every request.
  trigger: a pipeline of requests all need the same step: parsing, auth, headers, tracing.
  form: a function or module plug in a router pipeline.
  not when: the work belongs to one action — keep it there.

- **EX-P26 — Telemetry.**
  pattern: Telemetry — instrumentation without coupling the domain to its consumers.
  trigger: production must observe counts and timings, and the domain must not know who listens.
  form: `:telemetry.execute/3` at the event site, handlers attached elsewhere; `telemetry_metrics` for dashboards.
  not when: a log line where the event happens is enough.

- **EX-P27 — Memento.**
  pattern: Memento — snapshot and restore state for undo and checkpoints.
  trigger: an undo stack must keep old versions, and a hand-written copy of each field is the alternative.
  form: immutable data means the old versions still exist — keep the value itself as the snapshot.
  not when: the state is large and copies are expensive — rebuild or persist instead.

Canonical: Jurić, Elixir in Action; OTP Design Principles; Gospodinov, Concurrent Data Processing in Elixir.
