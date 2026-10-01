# Elixir best practices

Applied by the marestail `practices` judge to `*.ex`/`*.exs`/`*.heex` changes. Verified against Elixir 1.20, Phoenix 1.8, LiveView 1.2, Ecto 3.14, OTP 27–29 (September 2026). Phoenix, LiveView, Channels, and Ecto rules apply only where that stack is present.

Ground rules: expected domain outcomes are tagged tuples; exceptional failures raise. Model the domain with pure functions; a process exists for a runtime reason (concurrency, isolation, shared state, background work), not as an object. LiveView is the default UI; Bandit is the default server. `Routes.*_path`, Cowboy-by-default, Jason-for-new-JSON, umbrellas-by-default, and `<%= %>` in LiveView are gone; do not cargo-cult them. `@moduledoc`/`@doc` are comments in this repo's gate — do not add them.

## Errors & functional style

- **EX-1 — tagged tuples for expected errors.** `{:ok, t}` / `{:error, reason}` for domain outcomes; raise for bugs and infrastructure. No fake `%User{}` on `nil`, no swallowing a Repo miss into a default struct.
- **EX-2 — `with`/`else` over nested `case`.** Sequential `{:ok, x} <- …` / `:ok <- …`; the happy path reads top to bottom. Nested `case` for error threading is a finding.
- **EX-3 — `else` clauses stay explicit.** Do not re-match every error into one catch-all that hides which step failed.
- **EX-4 — pipes transform data.** Start with a bare value, not a call; no one-step pipes; no `then/2` just to fix argument position. If the subject is not the first argument, write a normal call (`Repo.preload(users, :posts)`).
- **EX-5 — pattern-match in heads and `=`.** Dispatch in the function head; `%=`/`=` to assert a shape you own. A `case` that only opens a map this module built is a finding.
- **EX-6 — guards over body checks.** `when is_binary(id)` in the head, not an `if`/`case` that only checks the argument's type.
- **EX-7 — structs for domain, persistent updates.** `%User{}` with enforced keys; `struct!/2`, `Map.put/3`, `update_in/2`. Never mutate.
- **EX-8 — validate at the boundary.** Schemaless or embedded Ecto changesets for forms and external input, not only the DB schema.

## OTP & concurrency

- **EX-9 — you probably don't need a GenServer.** A module of functions plus a struct is the domain. A GenServer that only holds a list you could pass as an argument is a finding.
- **EX-10 — a process needs a runtime reason.** Concurrency, fault isolation, shared mutable state, or background work. LiveView already gives a process per connected user; do not add another to model the same session.
- **EX-11 — no single global process.** Do not serialize unrelated work through one named GenServer (Keathley).
- **EX-12 — Registry and PubSub, not hand-rolled.** `{:via, Registry, {Mod, id}}` for per-entity lookup; `Phoenix.PubSub` for topics. Do not keep subscriber pid lists in state.
- **EX-13 — `Task` / `Task.Supervisor` for one-off work.** LiveView uses `assign_async`/`start_async` (§EX-35); do not start an unsupervised Task from a request.
- **EX-14 — restart type is intentional.** `:transient` restarts only on abnormal exit; `:temporary` never; `:permanent` always. Do not copy `:permanent` onto a job that should stay dead.
- **EX-15 — never `String.to_atom/1` on external input.** Atoms are not GC'd. `String.to_existing_atom/1` only.
- **EX-16 — no process dictionary for application state.** `Process.put`/`get` is invisible to typespecs and readers.
- **EX-17 — assertive matching.** Prefer `=` / function heads over a `case` that only asserts a shape you control.
- **EX-18 — protect the mailbox.** Unbounded `send`/`cast` to a slow consumer is a finding. Broadcast small payloads or IDs; subscribers fetch details.

## Testing

- **EX-19 — `async: true` wherever it is safe.** ExUnit default for new tests; SQL Sandbox DataCase for concurrent DB tests.
- **EX-20 — test the public context boundary.** Not private helpers. LiveView flows go through `Phoenix.LiveViewTest` (`live`, `render_submit`, `assert_push_event`).
- **EX-21 — PubSub-driven LiveView updates are tests.** `send(view.pid, {:new_msg, msg})` then assert the re-render; do not only click through the form that produced the message.
- **EX-22 — Mox for mocks.** Behaviour-enforced, async-safe. Mimic only for code you do not control. Bypass is stale; HTTP stubs are `Plug.Test` or Req plugs.
- **EX-23 — StreamData for invariants.** Parsers, state machines, money math get properties, not only example tests.
- **EX-24 — test supervision.** Kill a process, assert the system recovers. A new supervised worker without a restart test is a finding.

## Types & style

- **EX-25 — `mix format` is non-negotiable.** `.formatter.exs` lists `import_deps`. `mix format --migrate` for deprecated idioms (1.18+).
- **EX-26 — `@spec` on public context functions.** Elixir 1.17–1.20 gradual typing checks calls and infers patterns; write typespec-shaped code. dialyxir is optional and shrinking.
- **EX-27 — built-in `JSON`, not Jason/Poison, in new code.** Elixir 1.18 `JSON.encode/1` / `JSON.decode/1`.
- **EX-28 — verified routes.** `~p"/users"` in new Phoenix code; `Routes.user_path` is headed for deprecation in 1.8.
- **EX-29 — no umbrella by default.** One app with contexts, or a poncho. Umbrella tooling is poor; do not add `apps/` without a reason in the task.
- **EX-30 — Bandit is the HTTP server.** New Phoenix listeners do not introduce Cowboy without a reason in the task.
- **EX-31 — Igniter for upgrades.** `mix igniter.upgrade …`, not hand-edited mix.exs churn for framework bumps.

## Phoenix & LiveView

These apply only to Phoenix, LiveView, Channels, PubSub, Presence, and Ecto. Contexts own the domain; LiveViews own presentation.

- **EX-32 — LiveView is the default web UI.** Channels for mobile, SPAs, and third-party clients. Do not start a JS SPA for a UI you fully control.
- **EX-33 — subscribe on connected mount.** `if connected?(socket) do PubSub.subscribe …; Presence.track … end`. Do not subscribe during the disconnected render.
- **EX-34 — streams for collections.** `stream/4` + `phx-update="stream"` + keyed `:for` when the list is large or broadcast-updated. Assigns that hold >~100 items are a finding.
- **EX-35 — `assign_async` / `start_async` for load.** No double-fetch on disconnected then connected mount; keep the LiveView responsive. Beyond request scope, Oban; pipelines, Broadway (GenStage is plumbing; Flow is dormant).
- **EX-36 — `handle_info` inserts into streams.** `stream_insert` / `stream_delete` on broadcast; do not re-query the whole collection.
- **EX-37 — `{val}` in HEEx.** LiveView 1.0 interpolation; `<%= %>` in new LiveView templates is a finding.
- **EX-38 — JS commands, then colocated hooks, then `assets/js`.** `Phoenix.LiveView.JS` for toggle/show/dispatch; LV 1.1 colocated hooks/JS and 1.2 colocated CSS next to the template; `assets/js` only for heavy third-party widgets.
- **EX-39 — granular topics, small payloads.** `PubSub.broadcast(…, "room:#{id}", {:new_message, msg})`, not a whole room struct on `"rooms"`. `broadcast_fastlane` when many subscribers need the same encode.
- **EX-40 — broadcast from the context.** The mutation site notifies; LiveViews, Channels, Oban, and other nodes all listen. Do not broadcast only from the LiveView that submitted the form.
- **EX-41 — Phoenix.Presence for online lists.** CRDT, no Redis, net-split tolerant. Do not hand-roll ETS presence.
- **EX-42 — never pass `socket` into a context.** Extract assigns first. Business rules do not live in LiveView modules.
- **EX-43 — Phoenix 1.8 scopes on every context call.** `Rooms.get_room!(socket.assigns.scope, id)`; do not query by raw id from the LiveView.
- **EX-44 — no `Repo` in LiveView** beyond a prototype. Call a scoped context function.
- **EX-45 — uniqueness is a DB constraint.** `unique_constraint/3` plus the changeset rule; changeset-only uniqueness races.
- **EX-46 — magic-link auth, not password-only generators.** Phoenix 1.8 `phx.gen.auth` includes magic links, sudo mode, and scopes; do not reintroduce password-only auth in new code.
- **EX-47 — `{.async_result}` for async assigns.** Loading and failed slots; do not `case @stats` on an unawaited async assign.
- **EX-48 — LiveView is wrong for offline-first, latency-sensitive games, and pure client widgets.** Those use JS commands/hooks or Channels; do not stretch LiveView over them.
