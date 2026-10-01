# Go pattern rulebooks

Read by the architect and the `design` judge for `*.go` changes. Each pattern names the trigger that calls for it, the form the practices rulebook accepts, and when to leave it alone; release-gated helpers name the Go release and the `go.mod` line that unlocks them.

- **GO-P1 — Factory.**
  pattern: Factory — construct with validation, or hide which concrete type the caller gets.
  trigger: a type needs invariant-checking construction, or several implementations sit behind one shape.
  form: a `NewThing(...)` constructor returning a concrete struct, and an interface only when several implementations exist; a func value for pluggable factories.
  not when: a struct literal with exported fields is all the construction there is.

- **GO-P2 — Functional options.**
  pattern: Functional options — optional settings on a constructor without an argument list of flags.
  trigger: a constructor gains optional settings and callers must keep reading well.
  form: `type Option func(*Server)`, a variadic `opts ...Option`, defaults set before the options are applied.
  not when: two or three fields — a config struct argument is plainer.

- **GO-P3 — Prototype.**
  pattern: Prototype — copy a configured value instead of building it again.
  trigger: the same configured value is reused with small variations.
  form: struct copy by value, `slices.Clone` and `maps.Clone` (Go 1.21, when `go.mod`'s `go` line is at least 1.21); watch shared pointers, slices and maps inside.
  not when: construction is cheap and stateless.

- **GO-P4 — Singleton.**
  pattern: Singleton — one instance for the process.
  trigger: a shared value must exist once: a pool, a registry, a configured client.
  form: a package-level `var` initialised at load, or `sync.Once` for lazy init; `sync.OnceValue` and `sync.OnceFunc` (Go 1.21, when `go.mod`'s `go` line is at least 1.21) wrap a function; inject it rather than reaching for the global.
  not when: global state would make tests order-dependent — pass the value in.

- **GO-P5 — Adapter.**
  pattern: Adapter — make an existing type or vendor SDK satisfy the interface the app wants.
  trigger: a third party's shapes would leak into the code that uses it.
  form: `http.HandlerFunc` is the canonical example — a func type with a method, so a plain function satisfies `http.Handler`; wrapper structs for SDKs.
  not when: the app owns both sides — change the interface instead.

- **GO-P6 — Bridge.**
  pattern: Bridge — separate an abstraction from its implementation so both vary.
  trigger: two independent axes vary, and embedding one in the other would freeze them together.
  form: an interface field on a struct, injected by the constructor.
  not when: one axis varies — composition on that axis is enough.

- **GO-P7 — Composite.**
  pattern: Composite — trees where leaves and branches share one shape.
  trigger: the structure nests to arbitrary depth: file trees, syntax trees, document models.
  form: recursive structs, as `io/fs` and `ast.Node` do.
  not when: the nesting is one level deep.

- **GO-P8 — Decorator.**
  pattern: Decorator — wrap behaviour around an interface value.
  trigger: logging, retry or caching must surround an implementation without changing it.
  form: `io.Reader` chains, middleware `func(http.Handler) http.Handler`, `http.RoundTripper` wrappers.
  not when: the behaviour belongs on the type itself.

- **GO-P9 — Facade.**
  pattern: Facade — one small surface over a wide subsystem.
  trigger: callers must assemble several types and steps for one job.
  form: a package with a small exported API, as `http.Get` sits over `http.Client`.
  not when: the subsystem is already one call.

- **GO-P10 — Flyweight.**
  pattern: Flyweight — share the small values that many callers hold.
  trigger: reusable buffers or canonical values dominate allocation.
  form: `sync.Pool` for reusable buffers, and the `unique` package (Go 1.23, when `go.mod`'s `go` line is at least 1.23) for canonical values.
  not when: allocation is not the measured cost.

- **GO-P11 — Proxy.**
  pattern: Proxy — stand in for another value to control how it is reached.
  trigger: access must be lazy, cached, permission-checked or remote.
  form: a struct implementing the same interface and delegating to the real one; `httputil.ReverseProxy` for the HTTP case.
  not when: the direct call is cheap.

- **GO-P12 — Chain of Responsibility.**
  pattern: Chain of Responsibility — a request passes through ordered handlers.
  trigger: cross-cutting steps must wrap a request in a defined order.
  form: middleware chains and handler wrappers around one interface.
  not when: one function does the whole job.

- **GO-P13 — Command.**
  pattern: Command — an action as a value, so it can be queued, logged, retried or undone.
  trigger: work must outlive the call or be recorded.
  form: a closure or a func type; a job struct sent on a channel for a worker.
  not when: the action runs immediately in the caller.

- **GO-P14 — Interpreter.**
  pattern: Interpreter — evaluate a small language: templates, filters, rules.
  trigger: users or config supply expressions the program must evaluate.
  form: `text/template` or `go/parser` for the language at hand; a grammar library only when it is already a dependency.
  not when: the options are a fixed set — parse them into a struct.

- **GO-P15 — Iterator.**
  pattern: Iterator — traverse a collection without exposing its structure.
  trigger: callers need a traversal or a lazy sequence the collection does not already offer.
  form: range-over-func `iter.Seq` and `iter.Seq2` (Go 1.23, when `go.mod`'s `go` line is at least 1.23) with the `slices` and `maps` helpers; a channel only when the producer is concurrent.
  not when: a plain `for range` over a slice does it.

- **GO-P16 — Mediator.**
  pattern: Mediator — one coordinator between many goroutines.
  trigger: several goroutines talk to each other and the wiring becomes a mesh.
  form: a hub goroutine that owns a channel and routes to the rest.
  not when: two goroutines can talk directly.

- **GO-P17 — Memento.**
  pattern: Memento — snapshot and restore state for undo or checkpoints.
  trigger: earlier states must be kept without exposing internals.
  form: copy the struct, or encode it with `encoding/gob` or JSON.
  not when: recomputing the old state is cheaper than storing it.

- **GO-P18 — Observer.**
  pattern: Observer — one event notifies many listeners.
  trigger: several listeners must react to a change the publisher does not know by name.
  form: channels plus a registry of subscribers, callbacks, or `context.AfterFunc` (Go 1.21, when `go.mod`'s `go` line is at least 1.21).
  not when: exactly one listener exists.

- **GO-P19 — State.**
  pattern: State — behaviour that depends on a state the code tracks.
  trigger: a `switch` over a state value grows in every method.
  form: state functions (`type stateFn func(*lexer) stateFn`, looping until nil), or an interface per state.
  not when: two states and no transitions.

- **GO-P20 — Strategy.**
  pattern: Strategy — swap an algorithm at runtime.
  trigger: behaviour must vary by composition, and the choice arrives as a value.
  form: pass a func, as `slices.SortFunc` does; a small interface when the strategy carries state.
  not when: one algorithm exists.

- **GO-P21 — Template Method.**
  pattern: Template Method — a fixed skeleton with steps that vary.
  trigger: an algorithm's shape is stable and one or two steps differ per caller.
  form: a struct with func fields for the varying steps, or an interface passed to the skeleton function.
  not when: a single callback expresses the variation.

- **GO-P22 — Visitor.**
  pattern: Visitor — operations over a heterogeneous tree without touching its types.
  trigger: operations on a syntax tree or similar model multiply.
  form: `ast.Inspect` and `ast.Walk`, or a type switch over the node types.
  not when: there are two node types and one operation.

- **GO-P23 — Embedding.**
  pattern: Embedding — reuse behaviour by composing a type into a struct.
  trigger: a type wants another's methods without inheriting its identity.
  form: embed the struct or interface and override a method by defining it on the outer type.
  not when: the embedded type's whole API would leak — keep it a named field.

- **GO-P24 — Constructor injection.**
  pattern: Constructor injection — hand a type its collaborators instead of reaching for globals.
  trigger: a type needs a database, client or clock it should not build itself.
  form: pass them to `New...`; `wire` or `fx` only when they are already a dependency.
  not when: a package-level value with no test double needed is enough.

- **GO-P25 — Enum.**
  pattern: Enum — a closed set of values with a type of its own.
  trigger: a field takes one of a fixed set of values.
  form: a defined type with `const ( A Kind = iota; B )` and a `String()` method; nothing checks exhaustiveness without a linter.
  not when: the values are data that changes without a code change.

- **GO-P26 — Bounded parallel work.**
  pattern: Bounded parallel work — the same operation over many items, at a chosen width.
  trigger: independent items can run at once, and unbounded goroutines would exhaust a resource.
  form: a semaphore channel (`make(chan struct{}, n)`), or `errgroup` with `SetLimit` when it is already a dependency.
  not when: sequential work is fast enough.

- **GO-P27 — WaitGroup.**
  pattern: WaitGroup — wait for a set of goroutines to finish.
  trigger: the `Add(1)` / `go func` / `defer Done()` sequence, only when `go.mod`'s `go` line is at least 1.25.
  form: `sync.WaitGroup.Go` (Go 1.25, when `go.mod`'s `go` line is at least 1.25), which replaces the dance.
  not when: a channel already tells the caller when the work is done.

- **GO-P28 — Mutex.**
  pattern: Mutex — guard shared mutable state directly.
  trigger: a map or counter shared by goroutines, with no lifecycle to manage.
  form: a `sync.Mutex` or `sync.RWMutex` around the state; `sync/atomic` for plain counters.
  not when: the state has a lifecycle — confine it to one goroutine instead.

- **GO-P29 — Confinement.**
  pattern: Confinement — one goroutine owns the state; others send it messages.
  trigger: shared state has a lifecycle, or several operations must be atomic together.
  form: the owning goroutine loops on a `select` over requests, ticks and `ctx.Done()`.
  not when: a mutex around the state is simpler.

- **GO-P30 — Producer/consumer.**
  pattern: Producer/consumer — stages joined by a channel, with backpressure.
  trigger: a producer outruns its consumer, and a channel carries the work between stages.
  form: `close` the downstream channel when a stage is done, and `select` on `ctx.Done()` in every stage so a cancelled caller stops the pipeline.
  not when: producer and consumer run in lockstep.

- **GO-P31 — Timeouts.**
  pattern: Timeouts — bound an operation and propagate cancellation.
  trigger: work can hang on a network or a lock, and the caller must give up.
  form: `context.WithTimeout`, `context.WithCancel` and `context.WithCancelCause` (Go 1.20, when `go.mod`'s `go` line is at least 1.20), with `select` on `ctx.Done()`.
  not when: the operation is local and instant.

- **GO-P32 — Ticker.**
  pattern: Ticker — periodic work on a `time.Ticker`.
  trigger: a goroutine must act on an interval, and `time.Ticker` is the clock.
  form: a `select` on the ticker's channel and `ctx.Done()`, and `Stop` the ticker when the goroutine ends.
  not when: the delay is one-shot — `time.AfterFunc` is the tool.

- **GO-P33 — Synctest.**
  pattern: Synctest — deterministic time in tests of concurrent code.
  trigger: a test sleeps to synchronise goroutines or waits on a clock.
  form: `testing/synctest` (Go 1.25, when `go.mod`'s `go` line is at least 1.25) for a fake clock inside a bubble, with the race detector on.
  not when: the code has no time or concurrency dependence.

- **GO-P34 — net/http handlers.**
  pattern: net/http handlers — one handler per method and pattern.
  trigger: a handler switches on `r.Method` and the branches keep growing.
  form: `http.Handler` everywhere, with method-and-pattern routing in `ServeMux` (Go 1.22, when `go.mod`'s `go` line is at least 1.22); middleware is `func(http.Handler) http.Handler`.
  not when: one method and one path are all the endpoint serves.

- **GO-P35 — Handler struct.**
  pattern: Handler struct — give handlers their dependencies without globals.
  trigger: several handlers share the same database, client or config.
  form: handlers as methods on a struct holding those dependencies, or closures over them; build the struct once in `main`.
  not when: a single handler with no dependencies.

- **GO-P36 — HTTP status mapping.**
  pattern: HTTP status mapping — typed errors become status codes in one place.
  trigger: several handlers map the same domain errors to statuses.
  form: one function at the edge that maps error types to codes, with the handler calling it.
  not when: the handler returns one status and no error.

- **GO-P37 — Repository.**
  pattern: Repository — a persistence boundary the domain owns.
  trigger: domain code would otherwise be written against SQL, or the same query appears in several places.
  form: `database/sql` behind an interface the consumer declares; `sqlc` or `pgx` only when they are already a dependency.
  not when: the queries are trivial and one package owns them.

Canonical: Effective Go; Go Proverbs; Cheney, Practical Go; Harsanyi, 100 Go Mistakes.
