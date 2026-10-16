# 006 — marestail starts the target's app, so QA has something real to run against

After this task, a target repo can say once, in `marestail.toml`, how to start its app, and the `qa` gate runs with that app up. Today marestail never starts anything: the `qa` gate runs `[qa] cmd` and reads the exit code. On next-boilerplate the command was `npx playwright test`, nothing was listening, `playwright.config.*` is frozen so QA could not add a `webServer`, and QA wrote "No local app or CMS is running, so I'll add a Playwright e2e that serves a harness". Task 005 makes that outcome visible. This task makes the honest outcome reachable.

`~/workspace/animus-harness/animus_harness/serve.py` already does the hard part (own process group, a PORT variable, poll until ready, kill the group). Port that; do not import it.

## What changes

New keys under `[qa]`, all optional:

```toml
[qa]
cmd = "npx playwright test"
cwd = "."
start = "yarn dev -p $PORT"     # how to start the app; $PORT is supplied
ready = "/"                      # path polled until it answers below 500
port = 3400                      # first port tried; the next free port is used if it is taken
ready_timeout = 180              # seconds
env = { CMS_URL = "https://care.panel-ai.ok.k8s.dance/graphql", LOCALE = "en-gb" }
```

- With `start` set, the `qa` gate starts the app in `[qa] cwd` in its own process group, with `env` and `PORT` added to the environment, waits for `ready`, runs `cmd` with `MARESTAIL_APP_URL=http://localhost:<port>` and `PORT` exported, then stops the whole process group, also when `cmd` fails or times out.
- The app's output goes to `.marestail/runs/<task>/qa-app.log`, or `.marestail/qa-app.log` outside a run.
- If the app does not become ready in time, the gate fails with `qa: app did not answer on <url> within <n>s` and the last ten lines of the log.
- The QA prompt gains one line when `start` is set: the app is started for the gate and its address is in `MARESTAIL_APP_URL`.
- `marestail install` writes the new keys, commented out, into the template `marestail.toml`.
- `env` values are configuration a human wrote. They are never echoed into prompts, handoffs or commits.

## What must not change

- A target with no `[qa] start` behaves exactly as today.
- `[qa] cmd` and `cwd`, the 3600 s timeout, the skip when `cmd` is empty.
- The fast, sonar and full tiers never start the app.
- QA's freeze rules: `qa/**` and `**/playwright.config.*` stay frozen for it.
- No second app is left running after the gate, on success, failure, timeout or Ctrl-C.

## Tests

`tools/test-qa-app.py`, with a tiny Python HTTP server as the "app":

- `start` plus `ready` runs `cmd` only after the server answers, and `cmd` sees `MARESTAIL_APP_URL`
- the server's process group is gone after the gate, when `cmd` passes and when it fails
- a `start` command that never listens fails the gate with the `did not answer` message and the log tail
- a taken `port` moves to the next free one and `MARESTAIL_APP_URL` reflects it
- `env` reaches the app and does not appear in the QA prompt
- with no `start`, the gate behaves as today

## Done when

The new test passes, the existing `tools/test-*.py` still pass, and README's `[qa]` section documents the keys and says the app is started only for the `qa` tier.
