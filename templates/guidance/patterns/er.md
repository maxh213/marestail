# Erlang pattern rulebooks

Read by the architect and the `design` judge for `*.erl`/`*.hrl` changes. Each pattern names the trigger that calls for it, the form the practices rulebooks accept, and when to leave it alone; the cited `ER-n` rules are the authority and are not restated here.

- **ER-P1 — gen_statem.**
  pattern: `gen_statem` — an OTP state machine for protocols, connections and sessions.
  trigger: the callbacks need a different set of `handle_` clauses per status, or a `case` over a status atom grows in every callback.
  form: `callback_mode() -> handle_event_function`, with the state a record inside the module and maps at the boundary (see ER-13, ER-6).
  not when: the machine has no transitions or timeouts — a plain `gen_server` is enough; a protocol with interleavings still deserves a state-machine property, not more example tests (see ER-9, ER-4, ER-30).

- **ER-P2 — gen_event.**
  pattern: `gen_event` — one event stream with many independent handlers (alarms, audit).
  trigger: several listeners must all see the same event, and the producer must not know who listens.
  form: handlers added with `gen_event:add_handler/3`; the manager owns the stream and its handlers.
  not when: there is one listener, or the fan-out is a group of processes — `pg` is that registry (see ER-20).

- **ER-P3 — Application.**
  pattern: `application` — package a supervision tree with its config and dependencies.
  trigger: a new supervised tree must start with the node, before any request arrives.
  form: `-behaviour(application)` with `start/2` returning the top supervisor quickly, heavy work in `handle_continue` (see ER-10); the strategy matches the failure domain (see ER-14), child specs are maps (see ER-17), restart intensity is chosen (see ER-15), and dynamic children come from `supervisor:start_child/2` (see ER-18). `simple_one_for_one` is not allowed (see ER-18).
  not when: the code is a library with no processes of its own; tests start what they need rather than the whole application (see ER-28).

- **ER-P4 — Error kernel.**
  pattern: Error kernel — keep the part that must be correct tiny, and push risky work into supervised workers.
  trigger: a module mixes fragile work (parsing, IO, external calls) with invariants the rest of the system trusts.
  form: a small pure decision module plus supervised workers doing the risky work, with expected outcomes as tagged tuples rather than exceptions (see ER-1, ER-9).
  not when: the only check is the argument's type — write that guard in the function head (see ER-33).

- **ER-P5 — Monitor.**
  pattern: Monitor — observe a process you do not own instead of linking to it.
  trigger: a connection or client must learn that another process died, but that crash must not take it down.
  form: `erlang:monitor/2` plus a `'DOWN'` message in `handle_info`; supervisors use links where the lifecycles are tied, and names come from the OTP registries (see ER-16, ER-19).
  not when: the two processes live and die together — link them, or let one supervisor own both; a change here still needs a restart or interleaving test (see ER-29).

- **ER-P6 — Error class.**
  pattern: Error class — signal with the class that fits: `error` for bugs, `throw` for a non-local return, `exit` for process termination.
  trigger: a new failure path must choose a class, and a `try`/`catch` must say which classes it expects.
  form: `try ... catch Class:Reason:Stacktrace`; expected domain outcomes return tagged tuples, and sequencing uses `maybe` rather than nested `case` ladders (see ER-1, ER-2).
  not when: the failure is an expected domain outcome — return `{error, Reason}` and let the caller decide (see ER-1).

- **ER-P7 — Iolist.**
  pattern: Iolist — build output as nested lists and binaries instead of copying a binary accumulator.
  trigger: a loop appends with `<<Acc/binary, X/binary>>`, copying everything accumulated on every step.
  form: return an iolist and let the port or `unicode:characters_to_binary/1` flatten it once.
  not when: two or three small pieces are joined — one `<<A/binary, B/binary>>` is clearer than a list.

- **ER-P8 — Send after.**
  pattern: Send after — in-process timers through `erlang:send_after/3`, rescheduled from `handle_info`.
  trigger: a process re-arms itself with `timer:send_interval/2`, or a sleep loop, for recurring work.
  form: `erlang:send_after/3` scheduled again by the tick's `handle_info`, with the state in the process's own terms and never the process dictionary (see ER-22).
  not when: the work must survive restarts and deploys — that belongs in a durable queue, not a process timer.

Canonical: Armstrong's thesis; OTP Design Principles; Hébert, Learn You Some Erlang; Hébert, Erlang in Anger.
