# Python best practices

Applied by the marestail `practices` judge to `*.py` changes. Taken from the Python section of the design-patterns sheet (Rhodes' python-patterns.guide, Ramalho's *Fluent Python*, Percival and Gregory's *Architecture Patterns with Python*, Slatkin's *Effective Python*), checked against Python 3.14 (September 2026).

Ground rules: pass a callable before you write a class; the module is your singleton; a context manager is your Unit of Work. A rule that needs a newer Python than the repo runs (the version the `py.runtime` gate reads, e.g. `match` needs 3.10, `TaskGroup` and `StrEnum` 3.11) does not apply; do not bump the runtime to satisfy one. Framework rules apply only where that framework is present. Docstrings and comments are out of scope (the comments gate forbids them).

## GoF patterns, the Python way

- **PY-1 — factories are callables.** Pass the class or a function as a parameter (`make_client=StripeClient`); a subclass that exists only to override a factory method is a finding.
- **PY-2 — no abstract-factory class hierarchies.** Every class is already a factory. A class of factory methods only when the task needs to restrict what callers may pass.
- **PY-3 — builders are keyword arguments.** Defaults and keyword-only parameters (`*, currency="PLN"`) cover stepwise construction; `dataclasses.replace` for a variant. A hand-written `Builder` with setters is a finding.
- **PY-4 — prototypes are copies.** `copy.copy`, `copy.deepcopy`, `dataclasses.replace`, or a `functools.partial` factory. No `clone()` methods.
- **PY-5 — the module is the singleton.** Module-level objects (Rhodes' Global Object). Overriding `__new__`, Borg, or a `get_instance()` classmethod is a finding unless the task says why a module cannot do it.
- **PY-6 — adapters are thin.** A wrapper class or a couple of functions around the mismatched interface. The caller declares what it needs with a `typing.Protocol` of only the methods it calls.
- **PY-7 — composition over inheritance.** Inject the implementation object (Bridge); do not grow a subclass per combination of two axes of variation.
- **PY-8 — decorate callables with `@`.** Cross-cutting behaviour (retry, cache, timing, auth) is a decorator using `functools.wraps`, not a copy-pasted `try`/`finally` in each function.
- **PY-9 — facades are modules.** A subsystem is simplified by a module with a few public functions, not a `FooManager` class with only static methods.
- **PY-10 — flyweight via the standard library.** `__slots__`, `lru_cache` on a factory, `sys.intern`. No hand-rolled instance pools.
- **PY-11 — proxy only with a reason.** `__getattr__` forwarding or `functools.cached_property` for lazy loading; remember dunder lookups bypass `__getattr__`. Test doubles come from `unittest.mock`, not bespoke proxy classes.
- **PY-12 — chains are lists of callables.** Handlers in sequence are a list or the framework's middleware, not a linked `next_handler` object graph.
- **PY-13 — commands are callables.** A function, `functools.partial`, or a job-queue task. A `Command` class with only `execute()` and no state is a finding.
- **PY-14 — never `eval` or `exec` a grammar.** Small languages go through `ast`, an `operator` dispatch dict, or a parser library.
- **PY-15 — iterate with generators.** `yield`, generator expressions, `itertools`, `yield from`. No manual index counters or iterator classes where a generator does it.
- **PY-16 — observers are callback lists.** A list of callables or the framework's signals. No `Subject`/`Observer` base classes.
- **PY-17 — state is an `Enum` plus `match`.** Behaviour that depends on state dispatches on an `Enum` (with `match` on 3.10+); one class per state only when states carry different data.
- **PY-18 — strategies are functions.** `sorted(items, key=...)`, a function parameter, `functools.partial` for a configured one. A strategy class only when it holds state.
- **PY-19 — template method is an ABC.** A fixed skeleton with variable steps is an `abc.ABC` with `@abstractmethod` hooks, and only when there are at least two real implementations.
- **PY-20 — visit by type with dispatch.** `functools.singledispatch`, `ast.NodeVisitor`, or `match` with class patterns. No `isinstance` ladders over a closed set of types.

## Python-native idioms

- **PY-21 — sentinels, not magic values.** When `None` is a legal value, a module-level `_MISSING = object()` marks "not given". Empty strings, `-1`, or `"__none__"` as "not given" are findings.
- **PY-22 — constants at module scope.** Named `UPPER_CASE` constants for literals used in logic; no magic numbers or strings inline.
- **PY-23 — depend on behaviour, not class.** `typing.Protocol` (structural) or `collections.abc` (nominal) at a seam. An `isinstance` check against a concrete class to pick behaviour is a finding.
- **PY-24 — paired setup and teardown is a context manager.** Files, locks, transactions, timers, temporary state: `with`, `contextlib.contextmanager`, `ExitStack`. A manual `open()`/`close()` or `acquire()`/`release()` pair is a finding.
- **PY-25 — lazy pipelines are generators.** Stream large or unbounded data through generators instead of building intermediate lists.
- **PY-26 — plain data is a dataclass.** `@dataclass(frozen=True, slots=True)` (or `NamedTuple`) for records with equality and repr. A `dict[str, Any]` passed through three layers as a record is a finding; so is a class with hand-written `__init__`/`__eq__`/`__repr__` that a dataclass would generate.
- **PY-27 — parse, don't validate.** Untrusted input (request bodies, webhooks, env, third-party JSON) is parsed once at the boundary into a typed object; the inside of the app never re-checks `"key" in data`.
- **PY-28 — EAFP over LBYL.** `try: d[k]` / `except KeyError` rather than `if k in d` followed by `d[k]`, and the same for files and attributes. Catch the narrowest exception.
- **PY-29 — use the data model.** Make objects work with built-ins (`__len__`, `__iter__`, `__eq__`/`__hash__` together, `__call__`) instead of `get_length()`-style methods.
- **PY-30 — no metaclasses for framework tricks.** `__init_subclass__`, class decorators, and descriptors first; a metaclass only when none of those can do it.
- **PY-31 — inject collaborators as arguments.** Functions take their clients, clocks, and gateways as parameters with production defaults. Patching module globals in tests to reach a hidden dependency is a finding.
- **PY-32 — dispatch on shape with `match`.** On 3.10+, branching on the structure of a dict, tuple, or class uses `match`/`case` with mapping, sequence, and class patterns, not nested `isinstance` and `.get` chains.
- **PY-33 — closed sets are enums.** `enum.Enum`, `StrEnum` (3.11+), `Flag`. Bare string literals compared in more than one place are a finding.
- **PY-34 — prefer immutable shared data.** Frozen dataclasses, `tuple`, `frozenset`, `types.MappingProxyType` for anything shared across calls or threads. Mutable default arguments are always a finding.
- **PY-35 — expected failures raise domain exceptions.** A small custom exception hierarchy per module, raised and caught by type. Returning `{"error": ...}` dicts from domain code, or bare `except:`/`except Exception: pass`, is a finding.
- **PY-36 — name the condition.** A compound or non-obvious condition in an `if`, `while`, or conditional expression is extracted into a function or local whose name says what it means in the domain: `if payment_resolved(intent):`, not `if intent["status"] in ("succeeded", "canceled") or intent.get("last_payment_error"):`. Since the code has no comments, the name is the only explanation a reader gets. Negated names are a finding; negate a positive name at the call site.

## Concurrency

- **PY-37 — many I/O waits use `asyncio`.** `asyncio.TaskGroup` (3.11+) so siblings cancel together; `gather` only where TaskGroup is unavailable.
- **PY-38 — blocking libraries go to threads.** `ThreadPoolExecutor` or `asyncio.to_thread` for blocking HTTP, database, or file calls. A blocking call inside a coroutine is a finding.
- **PY-39 — CPU-bound work goes to processes.** `ProcessPoolExecutor` or `multiprocessing` (`InterpreterPoolExecutor` on 3.14). Threads for CPU work are a finding unless the repo runs the free-threaded build.
- **PY-40 — bound the fan-out.** `asyncio.Semaphore` or an executor's `max_workers`. Never start one task per item of an unbounded input.
- **PY-41 — queues carry backpressure.** `queue.Queue`/`asyncio.Queue` with `maxsize` between producers and consumers.
- **PY-42 — every wait has a timeout.** `asyncio.timeout()` (3.11+) or `wait_for` for async work; every `requests` call passes `timeout=`. A poll loop needs a deadline.
- **PY-43 — background and periodic work leave the web process.** A job queue (Celery, RQ, Dramatiq, arq) or a scheduler (cron, APScheduler). A `while True: sleep` loop or a thread started from a request handler is a finding.
- **PY-44 — lock only across an `await` or a thread.** In async code, `asyncio.Lock` only around a section that spans an `await`; threads share state through `threading.Lock` or a queue.

## Web & architecture

- **PY-45 — the domain does not import the framework.** Business rules live in plain modules that take and return plain data; routes and views translate HTTP to and from them. `request`/`flask.g` read deep inside domain code is a finding.
- **PY-46 — wrap external systems.** Domain code talks to a small gateway module (`payments.charge(...)`), not to `stripe.*` or raw `requests` calls scattered across services. Tests replace the gateway, not the HTTP library.
- **PY-47 — repositories and Unit of Work only when storage is real.** Use the Cosmic Python stack (Repository, Unit of Work as a context manager, service layer) where there is persistence to abstract; do not add it for a stateless proxy.
- **PY-48 — one home for business logic.** Pick fat models or a service layer per project and follow it; a new change must not open a third place.
- **PY-49 — cross-cutting request handling is middleware.** Auth, CORS, request IDs, and error mapping go in the framework's middleware or hooks (`before_request`, ASGI/WSGI middleware), not repeated in each route.
- **PY-50 — validate at the edge.** Request payloads are validated by pydantic, a form, a serializer, or one parser per route before any business logic runs.
- **PY-51 — signals for infrastructure only.** Framework signals are for cache invalidation and similar plumbing, not for domain side effects the reader cannot follow.
- **PY-52 — twelve-factor settings.** Configuration comes from the environment through one typed settings object read at startup; secrets are never committed and never read with `os.environ` deep in business code.
