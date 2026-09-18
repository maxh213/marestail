# TypeScript best practices

Applied by the marestail `practices` judge to `*.ts`/`*.tsx` changes. Verified against TypeScript 5.8+, React 19.2, Next.js 16, Node 24 LTS (September 2026). React rules apply only to React code; Next.js rules only in a Next.js app.

Ground rules: TypeScript's type system is structural and types are erased at runtime — any matching shape satisfies an interface, and no interface check exists at runtime. OOP territory shrinks toward the UI: never classes in React components, hooks, stores, or route handlers; classes shine in the domain model, data access, and the error hierarchy that crosses every layer.

## Language & types

- **TS-1 — unions over enums.** `enum` is non-erasable syntax and breaks Node's native type stripping; use a union type (`type Status = "idle" | "done"`), or an `as const` object when runtime values are needed.
- **TS-2 — `satisfies` over `as`.** `as` skips checking and widens literals; `satisfies` checks conformance and keeps literal inference. Reserve `as` for truly unavoidable cases.
- **TS-3 — validate at the edges.** Schema-parse (zod) env vars, API responses, form data, and URL params once at the boundary; never `as User` on `res.json()`. Trust the inferred types inside the app.
- **TS-4 — strict tsconfig posture.** `strict`, `noUncheckedIndexedAccess`, `noImplicitOverride`, `verbatimModuleSyntax`, `erasableSyntaxOnly`, `module: "nodenext"`, `skipLibCheck`.
- **TS-5 — erasable syntax only.** No enums, parameter properties, or namespaces, so Node 24 can strip types; keep `tsc --noEmit` in CI because stripping is not checking.
- **TS-6 — don't fight inference.** Annotate boundaries (props, public API, schema) and infer the middle; don't restate what TS already knows.
- **TS-7 — `interface` vs `type`.** `interface` for object shapes that may be implemented/extended or need declaration merging; `type` for unions, intersections, mapped/conditional types; pick one convention per codebase for plain shapes.
- **TS-8 — generics over `any`.** One type parameter is convenience, two is power; three or more single letters get named (`TEntity`) or restructured. Avoid conditional-type gymnastics in application code.
- **TS-9 — immutable updates.** New references (`[...prev, item]`, `{...prev, name}`) so React sees the change; Immer is the accepted idiom for deep updates.

## Design & OOP

- **TS-10 — anemic classes are functions.** A class with no instance state, invariants, or lifecycle is a function wearing a costume; write a function or a module of them.
- **TS-11 — classes earn their place** for domain entities with invariants, resource/lifecycle managers (pools, clients, connections), and error hierarchies — nowhere in React component code.
- **TS-12 — `#field` for real privacy.** TS `private` is compile-time only and bypassable; ECMAScript `#field` is real runtime privacy. Trade-off: `#field` makes typing nominal, so such classes can't be structurally faked in tests.
- **TS-13 — `implements` on classes** that satisfy a contract, so drift is a compile error at the definition.
- **TS-14 — interfaces don't exist at runtime.** No `instanceof` against an interface; use classes, discriminated-union tags, or schema parsing for runtime checks.
- **TS-15 — named static factories over constructor overloading.** Private constructor plus named paths (`of`, `fromCents`, `fromRow`) instead of overload signatures with type soup inside.
- **TS-16 — no parameter properties in type-stripped projects.** `constructor(private cents: bigint)` is non-erasable and banned under `erasableSyntaxOnly`; declare the field and assign in the body.
- **TS-17 — composition over inheritance.** No `BaseService` utility parents; inject capabilities as constructor parameters or close over them in factory functions — identical testability, no container.
- **TS-18 — the narrow inheritance lane.** `Error` subclasses and framework-demanded base classes; one level deep, maybe two.
- **TS-19 — explicit `override`.** Enable `noImplicitOverride` and write `override` on every overriding member.
- **TS-20 — error hierarchies, not strings.** `class DomainError extends Error` subclasses with `instanceof` filtering at the catch site; never throw strings and never string-match `e.message`.
- **TS-21 — `Result<T, E>` for expected failures.** Use it for domain outcomes the compiler should force callers to handle (validation, business rejections); throw for exceptional/infrastructure failures. Pick per layer and stay consistent.
- **TS-22 — single responsibility at module level.** The module is the unit of organization; split by reason to change. One component = one job; route handlers orchestrate, `_queries.ts` query, entities enforce rules.
- **TS-23 — small interfaces.** Three one-method interfaces over one fat one; no DI container in a small app.
- **TS-24 — unions for closed case sets.** Discriminated union + exhaustive `switch` with an `assertNever` default so adding a variant breaks every incomplete handler; interfaces or abstract classes only for open, plugin-style extensibility.
- **TS-25 — don't model a union as a class hierarchy** with a `kind` getter; use an actual discriminated union.
- **TS-26 — commit to OOP or don't.** NestJS-style DI/decorators are legitimate when fully adopted; don't half-do OOP on Express.

Decision table: pure contract → `interface`; contract + shared implementation + `instanceof` → `abstract class`; closed cases → union + `switch`; errors → `class extends Error`; config/data shapes → `type` + schema at the boundary; behavior without state → function.

## React

- **TS-27 — derive, don't duplicate.** If a value can be computed from state or props during render, it is not state; duplicated state synced by effects is the top source of React bugs.
- **TS-28 — reset state with `key`.** Remount the subtree (`<Editor key={userId} />`) instead of an effect that resets state when an id changes.
- **TS-29 — discriminated unions for async state.** One `QueryState<T>` (`loading`/`error`/`success`) with exhaustive handling, never a nullable data bag plus `isLoading`/`isError` booleans that can contradict each other.
- **TS-30 — events are not effects.** Respond where the event happens; effects exist only to synchronize with external systems (subscriptions, timers, non-React DOM APIs).
- **TS-31 — no fetching in mount effects.** Fetch in a Server Component, or use TanStack Query/SWR client-side; no hand-rolled `fetch` + `ignore` flag soup.
- **TS-32 — `useEffectEvent` for latest values in effects.** Effect events see current values without retriggering the effect; they are never added to dependency arrays, and `eslint-disable react-hooks` comments are a bug, not a fix.
- **TS-33 — no `setState` during render, no `ref.current` in render.** The react-hooks v6 compiler lint rules (`set-state-in-render`, `set-state-in-effect`, `refs`) treat these as bugs; derive instead.
- **TS-34 — React Compiler does the memoization.** No defensive `useMemo`/`useCallback` in new code; manual memoization only for precise control such as effect dependencies. Don't mass-delete existing memoization either.
- **TS-35 — `ref` is a normal prop.** No `forwardRef` in new code; take `ref?: Ref<...>` in props.
- **TS-36 — context shorthand.** `<ThemeContext value={theme}>`, not `<ThemeContext.Provider>`.
- **TS-37 — context is not state management.** Context for low-frequency values (theme, locale, auth); server data belongs in TanStack Query/RSC, hot client state in Zustand/RTK — never hand-stored API data in `useState`/Context/Redux.
- **TS-38 — composition over prop drilling.** Pass components down as children/slots instead of data through five levels.
- **TS-39 — forms use actions.** `useActionState` (the renamed `useFormState`) + `<form action>` + `useFormStatus` for pending, with schema parsing inside the action; `useOptimistic` for instant UI and `next/form` for search-style navigation.

## Next.js App Router

- **TS-40 — server by default, `"use client"` at the leaves.** The directive marks a module and its whole import subtree as client code; push the boundary down and pass Server Components through client components as `children`.
- **TS-41 — the server→client boundary is serializable.** Class instances cannot cross it; pass plain data and rehydrate client-side (`Money.fromRow(props.money)`).
- **TS-42 — async request APIs.** `await cookies()`, `await headers()`, `await params`/`searchParams`; sync access was removed in Next 16.
- **TS-43 — caching is explicit opt-in.** Everything is dynamic by default; cache with `"use cache"` + `cacheTag`/`cacheLife`, invalidate with `revalidateTag(tag, "max")` (profile required) or `updateTag()`/`refresh()` inside Server Actions.
- **TS-44 — Server Actions are public endpoints.** Schema-parse the input and check auth inside every action; action IDs are not authorization.
- **TS-45 — `proxy.ts` is never the only auth layer.** The renamed `middleware.ts` runs on the Node runtime only and was bypassable (CVE-2025-29927); re-check auth in every action and data-access function.
- **TS-46 — post-mutation side work goes in `after()`.** Emails, logging, and other non-blocking work run after the response.
- **TS-47 — feature-first colocation.** Route groups, private `_components`/`_actions.ts`/`_queries.ts` folders per route, truly shared UI outside `app/`; `app/` reads as the route map.
- **TS-48 — no barrel files in app code.** `index.ts` re-export files hurt dev-server speed, HMR, and tree-shaking; import directly. Barrels only at published package boundaries.
