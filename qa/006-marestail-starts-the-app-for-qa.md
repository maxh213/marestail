# QA procedure: marestail starts the app for the qa gate

1. `cd` to the marestail-green repo root with `.venv` active.
2. `python3 tools/test-qa-app.py`
   Expected: exit `0`, last line contains `ok` (covers ready-before-cmd, `MARESTAIL_APP_URL`, process-group cleanup on pass/fail, did-not-answer + log tail, next free port, env to app / not in prompt, no-`start` unchanged).
3. In a temp repo whose `marestail.toml` has `[qa] cmd` that prints `$MARESTAIL_APP_URL`, `start` serving HTTP on `$PORT`, `ready = "/"`, `port = 3400`: run `marestail gate --tier qa --only qa`.
   Expected: exit `0`; stdout has a `qa` ok line; `.marestail/qa-app.log` exists; nothing still listening on the chosen port (`ss -ltn` / `lsof` shows no listener).
4. Same temp repo; change `cmd` to `exit 1`; run the same gate.
   Expected: gate fails; still no listener left on that port.
5. Same temp repo; set `start = "sleep 60"`, `ready_timeout = 2`; run the gate.
   Expected: fail summary exactly `qa: app did not answer on http://localhost:3400/ within 2s`; findings show the last lines of `.marestail/qa-app.log`; `cmd` did not run.
6. Bind something on 3400 (`python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",3400));s.listen();input()'` in another terminal), restore a working `start`/`cmd`, run the gate.
   Expected: `cmd` output / log shows `MARESTAIL_APP_URL=http://localhost:<p>` with `p` > 3400; gate still passes; kill the binder afterwards.
7. Set `[qa] env = { CMS_URL = "https://example.test/g", LOCALE = "en-gb" }` and `start`; run the gate with a `start` that prints those env vars into the app log.
   Expected: log contains both values; `python3 -c "from marestail import config,prompts,pipeline; from pathlib import Path; c=config.load(Path('.')); print(prompts.worker_prompt(c, pipeline.find('qa'), Path('tasks/t.md'), 't', Path('/tmp/r.md'), ''))"` does not contain `example.test` or `en-gb`.
8. Remove `start` (leave `cmd = "true"`); run `marestail gate --tier qa --only qa`.
   Expected: passes as today; no `.marestail/qa-app.log` created by an app start (or log unchanged / absent for this run).
9. With `start` set, run `marestail gate --tier fast --only docs` (or any non-qa-only fast gate).
   Expected: no app listener started.
10. `grep -n 'start\|ready_timeout\|MARESTAIL_APP_URL\|qa tier' README.md templates/marestail.toml | head -40`
    Expected: README names the new `[qa]` keys and says the app starts only for the `qa` tier; `templates/marestail.toml` has those keys commented under `[qa]`.
11. `python3 tools/test-qa-ran-against.py; python3 tools/test-timeline.py; python3 tools/test-perf.py; echo exit=$?`
    Expected: ran-against and timeline exit 0; test-perf still exit 1 with `verdict-commit-files: '' != 'perf/bench_x.py'`.
