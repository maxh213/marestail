# 016 — commit labels name the backend: `[cursor/auto high]`, not `[auto high]`

After this task, a run whose model string does not already identify its origin stamps commits `[<backend>/<model> <effort>]`, so a cursor run on the auto model stamps `[cursor/auto high]` and a routed cursor session on K3 stamps `[cursor/kimi-k3-max]` instead of the bare `[kimi-k3-max]`. Labels that already name their origin stay byte-identical.

This already shipped on main as commit `80ed792` ("runner: stamp labels as backend/model when the model names no provider (cursor/auto)"); this task ports the same behavior into the rewritten runner with full pytest coverage, so merging green into main cannot regress it.

## Rule

In `model_name`, when `state.model` is set: return it unchanged when it contains `/` (a provider prefix, as kimi-code's and x-ai's ids carry) or when it contains the resolved backend string (claude-opus-5 names claude, grok-4.6 names grok); otherwise return `<backend>/<model>`. Every other path in `model_name` and `effort_name` is untouched.

| backend | model | effort | before | after |
|---|---|---|---|---|
| cursor | `auto` | high | `auto high` | `cursor/auto high` |
| cursor | `kimi-k3-max` | — | `kimi-k3-max` | `cursor/kimi-k3-max` |
| agy | `gemini-3.8-flash-high` | high | `gemini-3.8-flash-high high` | `agy/gemini-3.8-flash-high high` |
| junie | `gemini-3.8-flash` | high | `gemini-3.8-flash high` | `junie/gemini-3.8-flash high` |
| claude | `claude-opus-5` | high | `claude-opus-5 high` | unchanged |
| grok | `grok-4.6` | xhigh | `grok-4.6 xhigh` | unchanged |
| kimi | `kimi-code/kimi-for-coding-highspeed` | — | unchanged | unchanged |
| hermes | `x-ai/grok-4.6` | xhigh | unchanged | unchanged |

No-model paths are unchanged: the pre-routing label stays `dandelion/route`, a model-less run still labels with the bare backend name, and kilo still labels `KILO_DEFAULT_MODEL` plus variant.

## Must not break

- The re-route relabel: `routed()` still swaps `[<before>] ` for `[<after>] ` in the prompt, `state.labels` still accumulates every routed label, and `stamped()` still leaves a commit alone when its subject already starts with any earlier label. `test-route.py`'s pinned labels (`grok-4.6 xhigh`, `claude-opus-5 high`) stay valid because both name their backend.
- Commit layout otherwise: subject, body, `By <role>.` byline, verdict and handoff subjects.
- tools/test-agent-backends.py keeps passing with updated expectations, as on main: `label-model-only` → `claude/mymodel`, `label-with-effort` → `claude/mymodel high`, `label-grok` → `grok/mymodel xhigh`, `label-kimi` → `kimi/mymodel`, `label-kimi-effort` → `kimi/mymodel high`, plus the new pin `label-cursor-auto` → `cursor/auto high`; `label-no-model`, `label-kilo-default` and `label-kilo-plain` unchanged.

## Tests

Pytest coverage of every new branch in the rewritten runner's label function: prefix added, slash present, backend contained, model unset with a route, model unset without a route, kilo default. One test per row of the table above.
