# TypeScript pattern rulebooks

Read by the architect and the `design` judge for `*.ts`/`*.tsx` changes. Each pattern names the trigger that calls for it, the form the practices rulebook accepts, and when to leave it alone; a library named here is only ever already a dependency, never a reason to add one.

- **TS-P1 — Strategy.**
  pattern: Strategy — vary an algorithm.
  trigger: behaviour must vary by composition, and the choice arrives as data or config.
  form: pass a function, as `arr.sort(comparator)` does; an interface only when strategies carry state or config.
  not when: one algorithm exists — a method is enough.

- **TS-P2 — Command.**
  pattern: Command — an action as a value, so it can be queued, logged, undone or retried.
  trigger: work must outlive the call or be recorded.
  form: a function, or `{ execute, undo }` for undo stacks and job queues.
  not when: the action runs immediately in the caller.

- **TS-P3 — Observer.**
  pattern: Observer — decoupled change propagation.
  trigger: several listeners must react to a change the publisher does not know by name.
  form: `EventTarget`, an RxJS `Observable`, signals, or a store's `subscribe`.
  not when: exactly one listener exists — call it.

- **TS-P4 — Iterator.**
  pattern: Iterator — custom traversal or a lazy sequence.
  trigger: callers need a traversal the collection does not offer, and a hand-written iterator object is the alternative.
  form: built in — `Symbol.iterator`, generators (`function*`), `for await` over async iterables; never hand-write it.
  not when: arrays and `for...of` already express the traversal.

- **TS-P5 — Decorator.**
  pattern: Decorator — cross-cutting behaviour around a function or class.
  trigger: logging, retry, memoisation or auth must wrap a unit without changing it.
  form: higher-order functions (`withRetry(fn)`, `memoize(fn)`), React HOCs, or the TC39 `@decorator` syntax in the frameworks that support it.
  not when: the behaviour belongs inside the function.

- **TS-P6 — Adapter.**
  pattern: Adapter — isolate a vendor SDK or a mismatched API shape.
  trigger: a third party's names and shapes would otherwise spread through the app.
  form: a wrapper module exposing your own interface, so tests swap the module.
  not when: the app owns both sides — change the interface.

- **TS-P7 — Facade.**
  pattern: Facade — one small surface over a wide subsystem.
  trigger: callers must assemble several modules for one job.
  form: a module's `index.ts` exporting a small API, or a hook that wraps several hooks.
  not when: the subsystem is already one call.

- **TS-P8 — Proxy.**
  pattern: Proxy — intercept property access for reactivity, validation or lazy loading.
  trigger: reads or writes to an object must be observed or deferred.
  form: `new Proxy(target, handler)`, as reactivity libraries build on.
  not when: nothing intercepts the access.

- **TS-P9 — Factory.**
  pattern: Factory — construction with logic, or hiding which concrete type the caller gets.
  trigger: building an object needs validation or a choice between implementations.
  form: a plain function returning an object, preferred over classes and `new`; a named static factory (TS-15) only for a class that has earned its place (TS-11), and a plain function whenever there is no invariant to protect.
  not when: the caller knows the type and construction has no invariants.

- **TS-P10 — Builder.**
  pattern: Builder — fluent, stepwise configuration.
  trigger: construction has many optional steps, and the order or presence of a step carries meaning.
  form: a chained API, as Zod schemas, Kysely or Knex queries and Prisma offer; the type system can require the steps.
  not when: an object literal with the fields reads well.

- **TS-P11 — Singleton.**
  pattern: Singleton — one shared instance.
  trigger: a service must exist once, and a hand-written instance is the alternative.
  form: an ES module already is one (the module cache): export the instance from the module.
  not when: the value is cheap and per-use — export the factory instead.

- **TS-P12 — Composite.**
  pattern: Composite — recursive trees where leaves and branches share a shape.
  trigger: the structure nests to arbitrary depth: React children, AST nodes.
  form: a recursive union type (`type Node = Leaf | Branch`), with recursion at the branch.
  not when: the nesting is one level deep.

- **TS-P13 — State.**
  pattern: State — finite states with distinct behaviour.
  trigger: a value moves through named states, and each state behaves differently.
  form: a discriminated union plus an exhaustive `switch`; a state-machine library (XState, say) only when it is already a dependency and the transitions and guards are real.
  not when: a boolean is the whole machine.

- **TS-P14 — Chain of Responsibility.**
  pattern: Chain of Responsibility — a request passes through handlers, each handling it or passing it on.
  trigger: cross-cutting steps must wrap a request in a defined order.
  form: middleware — Express and Koa `next()`, Next.js middleware, Redux and tRPC middleware.
  not when: one handler does the whole job.

- **TS-P15 — Visitor.**
  pattern: Visitor — operations over a heterogeneous tree without touching its types.
  trigger: operations on a syntax tree or document model multiply.
  form: a `switch` on the discriminant, or the compiler visitor APIs for ASTs.
  not when: there are two variants and one operation.

- **TS-P16 — Memento.**
  pattern: Memento — snapshot and restore state for undo and time travel.
  trigger: earlier states must be kept without mutating the present.
  form: immutable snapshots, as Immer patches and Redux DevTools keep.
  not when: recomputing the old state is cheaper.

- **TS-P17 — Template Method.**
  pattern: Template Method — a fixed skeleton with variable steps.
  trigger: an algorithm's shape is stable and callers supply the steps.
  form: mostly replaced by callbacks; abstract base classes where a framework expects them.
  not when: a callback parameter expresses the variation.

- **TS-P18 — Module.**
  pattern: Module — encapsulation with an explicit surface.
  trigger: code needs a private interior and a public API.
  form: ES modules; the IIFE and revealing-module forms are history.
  not when: a function with no interior state is enough.

- **TS-P19 — Function composition.**
  pattern: Function composition — build a transformation from small functions.
  trigger: data passes through several transformations in a fixed order.
  form: `pipe` and `compose`, as Ramda, fp-ts and Effect provide; avoid long method chains on mutable objects.
  not when: two steps inline read better.

- **TS-P20 — Async coordination.**
  pattern: Async coordination — combine several async operations deliberately.
  trigger: more than one async operation must run together, and the failure and cancellation behaviour must be chosen.
  form: `Promise.all` (fail fast), `allSettled` (collect all), `race` and `any`; `AbortController` for cancellation.
  not when: one request is the whole operation.

- **TS-P21 — Result.**
  pattern: Result — expected failures as values, not throws.
  trigger: a caller must handle failure as part of normal flow.
  form: `{ ok: true, value }` and `{ ok: false, error }`; a Result library such as neverthrow only when it is already a dependency.
  not when: the failure is exceptional and should unwind.

- **TS-P22 — Dependency injection.**
  pattern: Dependency injection — hand a unit its collaborators.
  trigger: a module needs something a test must replace.
  form: pass dependencies as function or factory arguments; a container library only when the app wants one.
  not when: the module has no collaborators.

- **TS-P23 — Immutability.**
  pattern: Immutability — update by creating new values.
  trigger: shared state changes and readers must see the change by reference.
  form: spread and `structuredClone`; Immer for deep updates; `readonly` and `as const` for the type.
  not when: the data is local and thrown away.

- **TS-P24 — Custom hooks.**
  pattern: Custom hooks — reusable stateful logic.
  trigger: React code repeats stateful logic between components, or one component grows too long.
  form: `useThing()` returning state and actions, composed from other hooks.
  not when: the logic has no state or effects.

- **TS-P25 — Compound components.**
  pattern: Compound components — a component family with shared implicit state.
  trigger: React code needs a flexible API where parts cooperate: tabs, menus, accordions.
  form: context plus `<Tabs><Tabs.List/><Tabs.Panel/></Tabs>`, as Radix and Headless UI shape it.
  not when: a couple of props already express the API.

- **TS-P26 — Provider.**
  pattern: Provider — cross-cutting context for a subtree.
  trigger: React code shares low-frequency data: theme, auth, locale.
  form: `createContext` plus a provider; not for high-frequency state, because every consumer re-renders.
  not when: one subtree needs the value — pass it down.

- **TS-P27 — Controlled and uncontrolled.**
  pattern: Controlled and uncontrolled — decide who owns an input's value.
  trigger: React code handles forms and inputs and must choose where the value lives.
  form: controlled when the parent needs each keystroke; uncontrolled with refs or a form library otherwise.
  not when: the input is static.

- **TS-P28 — Reducer.**
  pattern: Reducer — UI state with many transitions in one place.
  trigger: React code has state that changes through named events, and several setters are drifting apart.
  form: `useReducer` with an action union; a state-machine library only when it is already a dependency and the guards and side effects grow.
  not when: one `useState` covers it.

- **TS-P29 — Render props.**
  pattern: Render props — a component hands rendering to its caller.
  trigger: React code needs the caller to decide part of the output, or must wrap a child with extra data.
  form: a function prop, or a hook; mostly replaced by hooks, still seen in virtualised lists.
  not when: a hook can express the inversion.

- **TS-P30 — Composition over configuration.**
  pattern: Composition over configuration — children and slots instead of flags.
  trigger: React code grows a component's prop list with booleans that select behaviour.
  form: accept `children` and slots, and let callers compose the parts.
  not when: two props are the whole API.

- **TS-P31 — Client data caching.**
  pattern: Client data caching — server state on the client with a cache.
  trigger: React code fetches the same server data from several components, or needs revalidation.
  form: TanStack Query or SWR: stale-while-revalidate, optimistic updates, cache keys.
  not when: the data is fetched once and stays put.

- **TS-P32 — Discriminated union.**
  pattern: Discriminated union — model states or variants as a union with a tag.
  trigger: a value can be one of several shapes, and code must handle each.
  form: a `kind` field per variant, and a `never` check in the default branch so a new variant fails the build.
  not when: one shape is all there is.

- **TS-P33 — Branded types.**
  pattern: Branded types — ids and units that must not be mixed.
  trigger: two strings or numbers are structurally equal but semantically different: a user id and an order id.
  form: `type UserId = string & { readonly __brand: 'UserId' }`, built at the boundary.
  not when: the plain type is unambiguous at every call site.

- **TS-P34 — Const assertion.**
  pattern: Const assertion — one source of truth for a runtime value and its type.
  trigger: a literal list must be both a value and a type.
  form: `const ROLES = ['admin', 'user'] as const; type Role = typeof ROLES[number]`.
  not when: the value is not a literal set.

- **TS-P35 — satisfies.**
  pattern: satisfies — check a literal against a type without widening it.
  trigger: a literal must conform to a type and keep its narrow inferred type.
  form: `const config = { ... } satisfies Config`.
  not when: the annotation `: Config` is what the code wants.

- **TS-P36 — Template literal types.**
  pattern: Template literal types — typed route or event strings.
  trigger: strings follow a pattern the compiler could check: routes, event names, css units.
  form: a pattern type such as `/users/${string}`, or a union built from `as const` parts.
  not when: the strings are free-form.

- **TS-P37 — Overloads.**
  pattern: Overloads — distinct call signatures instead of one parametric one.
  trigger: the return type depends on the shape of the arguments.
  form: overload signatures with one implementation; generics when one signature covers every case.
  not when: one signature reads clearly.

- **TS-P38 — Mapped types.**
  pattern: Mapped types — derive types instead of duplicating them.
  trigger: a type mirrors another with fields added, removed or transformed.
  form: `Pick`, `Omit`, `Partial`, `Record`, conditional and mapped types.
  not when: writing the type out is clearer than the derivation.

Canonical: Osmani, Learning JavaScript Design Patterns; patterns.dev; Vanderkam, Effective TypeScript.
