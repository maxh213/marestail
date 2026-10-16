# C# pattern rulebooks

Read by the architect and the `design` judge for `*.cs` changes. Each pattern names the trigger that calls for it, the form the practices rulebook accepts, and when to leave it alone; the framework forms — DI, delegates, `IEnumerable<T>`, `event` — come first and the hand-written shape only when they do not fit.

- **CS-P1 — Factory Method.**
  pattern: Factory Method — construct without the caller naming the concrete type.
  trigger: the type to build depends on input or configuration, or a constructor has validation the caller should not repeat.
  form: register with the DI container and inject `Func<T>` or an `IFooFactory`; `IHttpClientFactory` and `ILoggerFactory` are the framework examples.
  not when: the caller knows the type — a constructor call is the honest one.

- **CS-P2 — Abstract Factory.**
  pattern: Abstract Factory — one family of related objects that must match.
  trigger: a provider's connection, command and adapter must all come from the same vendor.
  form: `DbProviderFactory`, or one DI registration group per family.
  not when: one object varies, not a family — a single Factory Method is enough.

- **CS-P3 — Builder.**
  pattern: Builder — multi-step construction with many optional parts.
  trigger: the same steps must yield different representations, or the constructor grows an argument list of flags.
  form: `HostBuilder` and fluent configuration chains; records and object initialisers cover the simple case.
  not when: an object initialiser or a record already reads well.

- **CS-P4 — Prototype.**
  pattern: Prototype — copy a configured object instead of building it again.
  trigger: the same configured instance is needed many times with small variations.
  form: records with `with` expressions, or `MemberwiseClone`; avoid `ICloneable`, whose shallow-or-deep contract is unspecified.
  not when: construction is cheap and stateless.

- **CS-P5 — Singleton.**
  pattern: Singleton — exactly one instance, reachable everywhere.
  trigger: state must be shared process-wide: a cache, a registry, a connection pool.
  form: `services.AddSingleton<T>()`; `Lazy<T>` only if a static is truly unavoidable, because hand-rolled statics hurt testing.
  not when: the object is cheap and per-use — a transient registration is simpler.

- **CS-P6 — Adapter.**
  pattern: Adapter — make an existing class or third-party SDK fit the interface the app wants.
  trigger: a vendor's names, shapes or exceptions would otherwise leak into the app.
  form: a wrapper class implementing the app's interface; extension methods for the light case.
  not when: the app owns both sides — change the interface instead of wrapping it.

- **CS-P7 — Bridge.**
  pattern: Bridge — separate an abstraction from its implementation so both vary.
  trigger: two independent axes vary (shape by renderer, message by transport) and subclassing both would explode.
  form: an interface for the implementation axis, injected into the abstraction.
  not when: only one axis varies — inheritance or composition on that axis is enough.

- **CS-P8 — Composite.**
  pattern: Composite — trees where leaves and containers share one interface.
  trigger: the structure nests to arbitrary depth: UI trees, expression trees, document models.
  form: a recursive class exposing `IReadOnlyList<Node> Children`; `System.Linq.Expressions` is the framework example.
  not when: the nesting is one level deep — a plain collection is clearer.

- **CS-P9 — Decorator.**
  pattern: Decorator — add behaviour around an object without subclassing it.
  trigger: caching, logging, retry or auth must wrap an existing service and stay out of it.
  form: stream wrappers, `DelegatingHandler`, or a DI decorator from a library the app already depends on.
  not when: the behaviour belongs inside the class, or a single override would do.

- **CS-P10 — Facade.**
  pattern: Facade — one simple entry point in front of a complex subsystem.
  trigger: callers must know too many types and steps to get one thing done.
  form: an application service in front of domain objects and repositories, as `File.ReadAllText` sits over streams.
  not when: the subsystem is already a single call.

- **CS-P11 — Flyweight.**
  pattern: Flyweight — share intrinsic state across many small objects.
  trigger: millions of instances exist and memory is the limit.
  form: string interning, `ArrayPool<T>`, `MemoryPool<T>`; rarely hand-written.
  not when: object counts are small — sharing adds indirection for nothing.

- **CS-P12 — Proxy.**
  pattern: Proxy — stand in for another object to control access to it.
  trigger: loading must be lazy, remote, permission-checked or cached.
  form: `Lazy<T>`, EF Core lazy-loading proxies, or a generated client stub.
  not when: access needs no decision — call the object directly.

- **CS-P13 — Chain of Responsibility.**
  pattern: Chain of Responsibility — a request passes through handlers, each handling it or passing it on.
  trigger: cross-cutting steps must run in a defined order around a request.
  form: ASP.NET Core middleware (`app.Use`) or a `DelegatingHandler` chain.
  not when: one handler does the whole job — a middleware for it is ceremony.

- **CS-P14 — Command.**
  pattern: Command — a request as an object, so it can be queued, logged, retried or undone.
  trigger: an action must outlive the call: a job queue, an undo stack, an audit of intent.
  form: `ICommand` in WPF/MVVM, a request with its handler, or `Action`/`Func` for the trivial case.
  not when: the action runs now and nothing records it.

- **CS-P15 — Interpreter.**
  pattern: Interpreter — evaluate sentences of a small grammar: filters, rules, formulas.
  trigger: users or config supply expressions the app must evaluate.
  form: expression trees, or a parser library; do not hand-roll the classic class-per-rule version.
  not when: the "grammar" is a fixed set of options.

- **CS-P16 — Iterator.**
  pattern: Iterator — traverse a collection without exposing its structure.
  trigger: callers need a traversal the collection does not already offer, and a hand-written cursor class is the alternative.
  form: built in — `IEnumerable<T>`, `yield return`, `IAsyncEnumerable<T>`; never hand-write it.
  not when: `foreach` or a LINQ operator already expresses the traversal.

- **CS-P17 — Memento.**
  pattern: Memento — snapshot and restore state without exposing internals.
  trigger: undo or checkpoints must keep earlier state the object does not hand out.
  form: immutable records as snapshots, or serialisation of the state.
  not when: the state is cheap to recompute from the present.

- **CS-P18 — Observer.**
  pattern: Observer — one change notifies many dependents.
  trigger: several parts of the app must react to a change the publisher should not know by name.
  form: built in — `event` and `EventHandler<T>`, `INotifyPropertyChanged`, `IObservable<T>`.
  not when: exactly one listener exists — call it.

- **CS-P19 — State.**
  pattern: State — behaviour that depends on internal state.
  trigger: the same `switch` over a state field grows in every method.
  form: one class per state behind an interface; a `switch` expression over an enum when the machine is small.
  not when: two states with no transitions — a boolean is the whole machine.

- **CS-P20 — Strategy.**
  pattern: Strategy — swap an algorithm at runtime.
  trigger: behaviour must vary by composition, and the choice arrives as configuration.
  form: an interface plus DI, or just a `Func<T, TResult>`; LINQ takes strategies as lambdas.
  not when: only one algorithm exists — a method is enough.

- **CS-P21 — Template Method.**
  pattern: Template Method — a fixed skeleton with steps subclasses vary.
  trigger: an algorithm's shape is stable and one or two steps differ per subtype.
  form: an abstract base with `protected virtual` hooks, as `BackgroundService.ExecuteAsync` and `ControllerBase` do; prefer Strategy when you can.
  not when: composition can vary the step — inject it instead.

- **CS-P22 — Visitor.**
  pattern: Visitor — add operations to a stable hierarchy without touching it.
  trigger: operations on an AST or document model multiply and the hierarchy rarely changes.
  form: `ExpressionVisitor` or a Roslyn visitor; a type-pattern `switch` is the lightweight substitute.
  not when: the hierarchy changes often — put the operation on the types.

- **CS-P23 — Dependency injection.**
  pattern: Dependency injection — hand a type its collaborators through its constructor.
  trigger: always in ASP.NET Core; lifetimes follow state: singleton (stateless, shared), scoped (per request), transient (cheap, stateful).
  form: `Microsoft.Extensions.DependencyInjection` and constructor injection; no service locator.
  not when: the type is a pure value with no collaborators.

- **CS-P24 — Options.**
  pattern: Options — typed configuration instead of loose values.
  trigger: a service reads several related settings that belong together.
  form: `IOptions<T>`, `IOptionsSnapshot<T>` or `IOptionsMonitor<T>`, bound from configuration.
  not when: one value is read once at startup — pass it in.

- **CS-P25 — Repository.**
  pattern: Repository — a persistence boundary the domain can be tested against.
  trigger: domain code would otherwise depend on the ORM, or the same query is spelled out in several services.
  form: an interface the domain owns, with the ORM behind it; add one only for a genuine abstraction boundary, because `DbContext` is already a unit of work.
  not when: EF Core already provides the boundary the code needs.

- **CS-P26 — Result.**
  pattern: Result — expected failures as values, not exceptions.
  trigger: a caller must handle failure as part of normal flow: validation, a missing record, a declined payment.
  form: a `Result<T>` type from a library the app already depends on; exceptions stay for the exceptional.
  not when: the failure is exceptional and should unwind the stack.

- **CS-P27 — Middleware.**
  pattern: Middleware — cross-cutting work for every request.
  trigger: every request (or a route group) needs the same step: errors, auth, routing, headers.
  form: `app.UseX()` in the pipeline; order matters — exception handling, then auth, then routing.
  not when: the work belongs to one endpoint — keep it in the handler.

- **CS-P28 — Producer/consumer.**
  pattern: Producer/consumer — an async pipeline with backpressure.
  trigger: a producer runs faster than its consumer and work must be queued between them.
  form: `System.Threading.Channels` or `IAsyncEnumerable<T>`, with a bounded channel when memory is the limit.
  not when: producer and consumer run in lockstep — a direct call is simpler.

Canonical: GoF (1994); Refactoring.Guru; Fowler's PoEAA.
