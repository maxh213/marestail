# Erlang best practices

Applied by the marestail `practices` judge to `*.erl`/`*.hrl` changes. Verified against Erlang/OTP 29, rebar3 3.27, ELP (WhatsApp erlang-language-platform), Cowboy 2.13 (September 2026). Cowboy and `pg` fan-out rules apply only to real-time/websocket code; OTP behaviour rules apply wherever those behaviours are used.

Ground rules: expected domain outcomes are tagged tuples; unexpected failures crash and the supervisor restores known-good state. Processes are cheap — one per concurrent entity, not one giant coordinator. Style canon is OTP Design Principles plus the Inaka/EEF guidelines. `gen_fsm`, `pg2`, `simple_one_for_one`, `erlang_ls`, and eprof/cprof/fprof are gone; do not cargo-cult them.

## Errors & functional style

- **ER-1 — let it crash.** Tagged tuples (`{ok, T}` / `{error, Reason}`) for expected domain outcomes; crash on unexpected failures so the supervisor restarts into known-good state. No `catch _:_`, no fake success values, no swallowing a DB crash into `{ok, #user{}}`.
- **ER-2 — `maybe` over nested `case` ladders.** Sequential `{ok, X} ?= …` / `ok ?= …` with an `else` clause; the happy path reads top to bottom. Nested `case` for error threading is a finding.
- **ER-3 — `maybe` is a reserved keyword.** An unquoted `maybe` atom will not compile on OTP 29; quote it (`'maybe'`) only if you must keep the atom.
- **ER-4 — pattern-match in function heads.** Dispatch on maps, records, and tagged tuples in the head (`route(#{method := get} = Req) -> …`); a `case` on a shape this module owns is a finding.
- **ER-5 — thread new values.** State is a new term through arguments; accumulate with tail recursion or `lists:foldl/3` and reverse at the end. The process dictionary and ETS are controlled exceptions, not the default store.
- **ER-6 — records inside, maps at the boundary.** `#mod_state{}` for fixed internal gen_server state; maps (`#{host := _, port := _}`) for public APIs, config, and wire data.
- **ER-7 — do not export records.** No `-include` of a record definition another module depends on; a field change silently breaks callers. Maps or accessor functions for anything shared. OTP 29 native records (EEP-79) are experimental — not production practice.

## OTP & concurrency

- **ER-8 — one process per concurrent entity.** A connection, session, room, or device is a process (~2KB, limit 1_048_576). Not one gen_server juggling a list of sockets; not a process per function call.
- **ER-9 — OTP behaviours, not hand-rolled loops.** `gen_server` / `gen_statem` / `gen_event`; a `receive` loop you wrote yourself has no sys/debug, no code upgrade, no supervision visibility.
- **ER-10 — `handle_continue` for post-init work.** `init/1` returns fast so the supervisor is not blocked; heavy startup goes in `handle_continue`.
- **ER-11 — no naked `receive` in callbacks.** Unexpected messages pile up. Unknown messages hit a logged `handle_info` catch-all that does not crash.
- **ER-12 — the module owns its protocol.** Callers use `cart:add_item(Pid, Item)`, never `Pid ! {add_item, Item}`. Raw messages are a finding.
- **ER-13 — `gen_statem` in `handle_event_function` mode.** Connection/session/protocol machines use `callback_mode() -> handle_event_function`. `gen_fsm` is deprecated — a finding.
- **ER-14 — supervisor strategy matches the failure domain.** `one_for_one` for independent children (default), `rest_for_one` when later children depend on earlier ones, `one_for_all` only when they are tightly coupled.
- **ER-15 — `intensity`/`period` are designed.** A supervisor that gives up and kills the tree is a chosen failure mode; do not leave the defaults unexamined on a new supervisor.
- **ER-16 — restart type is intentional.** `transient` restarts only on abnormal exit; `temporary` never restarts; `permanent` always. Pick one in the child spec; do not copy `permanent` onto a job that should stay dead.
- **ER-17 — map child specs.** `#{id => Name, start => {M, F, A}}`, not the legacy tuple form, in new supervisors.
- **ER-18 — no `simple_one_for_one`.** Dynamic children are `one_for_one` plus `supervisor:start_child/2`.
- **ER-19 — name things the OTP way.** Registered names for singletons (`{local, ?MODULE}`); `{via, Mod, {room, Id}}` for per-entity processes; `pg` for groups and topics. `pg2` is gone.
- **ER-20 — `pg` for broadcast, not pid lists.** Do not keep a list of subscriber pids in gen_server state; `pg:join/3` + `pg:get_members/2` cleans up crashed members. `syn` only when you need net-split-aware semantics.
- **ER-21 — protect the mailbox.** Unbounded `cast`/`!` to a slow consumer is a finding. Prefer `gen_server:call` when the caller can wait; otherwise shed when `message_queue_len` is over a stated limit. OTP 27 `long_message_queue` monitoring belongs in production.
- **ER-22 — no process dictionary for application state.** `put`/`get` is invisible to `-spec`, Dialyzer, and readers. Allowed exceptions: OTP internals and OpenTelemetry context.
- **ER-23 — never `list_to_atom/1` (or `binary_to_atom/1`) on external input.** Atoms are not GC'd. Use `list_to_existing_atom/1` / `binary_to_existing_atom/1`. OTP 29 warns; the warning is not optional.
- **ER-24 — do not send funs between nodes.** They break across code versions; send `{M, F, A}` or explicit data.
- **ER-25 — `try/catch`, not old-style `catch`.** OTP 29 warns on `catch`; rewrite it.
- **ER-26 — hibernate idle gen_servers.** Return `{noreply, State, hibernate}` (or OTP 28 `erlang:hibernate/0` to keep the stack) for long-idle processes.
- **ER-27 — do not pin large binaries in long-lived state.** Refc binaries (>64B) held by a process that rarely GCs leak. Drop them, hibernate, or `erlang:garbage_collect/1`; diagnose with `recon:bin_leak/1`.

## Testing

- **ER-28 — EUnit for a module, Common Test for the system.** Pure functions and generators (`foo_test_() -> …`) in EUnit; supervision restarts, multi-node, and protocol flows in Common Test. Do not `application:ensure_all_started` from EUnit.
- **ER-29 — test what Erlang is for.** A change to a supervisor, registry, or concurrent protocol without a restart/interleaving test is a finding; do not only port xUnit habits onto pure functions.
- **ER-30 — PropEr `statem` for stateful systems.** A cache, registry, or protocol with interleavings gets a property model (`?FORALL(Cmds, commands(?MODULE), …)`), not only example tests. Concuerror for forced interleavings on race-sensitive code.

## Types & style

- **ER-31 — explicit `-export`.** Never `-compile(export_all)`.
- **ER-32 — `-spec` on every exported function.** Success typings for Dialyzer/eqWAlizer; keep the PLT in CI. OTP 28 nominal types (EEP-69) for opaques.
- **ER-33 — guards in the head.** `get_user(Id) when is_binary(Id) -> …`, not a body `if`/`case` that only checks the argument's type.
- **ER-34 — `andalso`/`orelse`, never `and`/`or`.** OTP 29 `warn_obsolete_bool_op`.
- **ER-35 — no `if`.** Pattern matching or `case`/`maybe`; Inaka forbids `if` entirely.
- **ER-36 — `snake_case` functions and atoms, `CamelCase` variables.** Spaces, not tabs.
- **ER-37 — `-compile({no_auto_import, […]})` when you shadow a BIF.** Do not silently hit `put/2` vs `erlang:put/2`.
- **ER-38 — `proc_lib:set_label/1` on unregistered processes.** OTP 27 process labels so crash logs name the connection/session, not just a pid.
- **ER-39 — built-in `json`, not jsx/jiffy, in new code.** `json:encode/1` and `json:decode/1` since OTP 27.
- **ER-40 — `tprof` for profiling.** eprof/cprof/fprof were removed in OTP 29.
- **ER-41 — ELP, not `erlang_ls`.** `erlang_ls` is archived; WhatsApp ELP is the LSP, linter, and eqWAlizer host.

## Cowboy & real-time

These apply only to websocket/HTTP handlers, room/session servers, and presence/fan-out. The connection is a process; domain state is not.

- **ER-42 — the websocket handler holds no domain state.** It upgrades, authenticates, joins `pg`, and translates frames ↔ messages. Room/session data lives in a supervised gen_server. A crashed connection must not take the room down, and vice versa.
- **ER-43 — translate at the boundary.** UI protocol (JSON frames, event names) does not leak into room/session modules; those stay UI-agnostic.
- **ER-44 — `websocket_info` has a catch-all.** Unknown messages log and return `{ok, State}`; they do not crash the connection process.
- **ER-45 — presence and fan-out go through `pg`.** Diff-based presence, not full rosters; no hand-maintained pid lists. `pg` is distributed — connections on any node see membership.
- **ER-46 — shed slow consumers.** If a client's mailbox grows, drop or disconnect at a stated `message_queue_len`; do not OOM the node for a stalled TCP window.
- **ER-47 — no Mnesia as the default high-write store.** ETS plus a real DB for messaging paths; Mnesia only where its semantics are the point.
- **ER-48 — Cowboy is the HTTP/WebSocket server.** New listeners use `cowboy_websocket`; do not introduce a second HTTP stack without a reason in the task.
