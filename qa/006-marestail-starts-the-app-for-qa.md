# QA procedure: marestail starts the app for the qa gate

Pasteable app used below — write as `app.py` in the temp repo:

```python
import os, http.server as h
print(os.environ.get("CMS_URL", ""), os.environ.get("LOCALE", ""), flush=True)
h.HTTPServer(("127.0.0.1", int(os.environ["PORT"])), h.SimpleHTTPRequestHandler).serve_forever()
```

`start = "python3 app.py"`. Defaults when omitted: `ready="/"`, `port=3400`, `ready_timeout=180`.

1. `cd` to the marestail-green repo root with `.venv` active.
2. `python3 tools/test-qa-app.py`
   Expected: exit `0`, last line contains `ok` (covers ready-before-cmd, `MARESTAIL_APP_URL`, start in `[qa] cwd`, cleanup on pass/fail/timeout, did-not-answer + log tail, next free port, env to app / not in prompt, no-`start` unchanged, empty-`cmd` skip with summary `skipped: no [qa] cmd configured`, bare-gate log path, runner sets `MARESTAIL_TASK`, run log under `.marestail/runs/<stem>/qa-app.log`).
3. In a temp repo with the `app.py` above and `[qa] cmd = "true"`, `start = "python3 app.py"`, `ready = "/"`, `port = 3400`: run `marestail gate --tier qa --only qa` with `MARESTAIL_TASK` unset.
   Expected: exit `0`; stdout has a `qa` ok line; `.marestail/qa-app.log` exists; nothing still listening on the chosen port.
4. Same temp repo; change `cmd` to `exit 1`; run the same gate.
   Expected: gate fails; still no listener left on that port.
5. Same temp repo; set `start = "sleep 60"`, `ready_timeout = 2`; run the gate.
   Expected: fail summary exactly `qa: app did not answer on http://localhost:3400/ within 2s`; findings show the last lines of `.marestail/qa-app.log`; `cmd` did not run.
6. Bind 3400 (`python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",3400));s.listen();input()'` in another terminal). Restore `start = "python3 app.py"`, set `cmd = "printf '%s' \"$MARESTAIL_APP_URL\" > seen-url"`, run the gate.
   Expected: exit `0`; `cat seen-url` is exactly `http://localhost:<p>` with `p` > 3400; kill the binder afterwards.
7. Move `app.py` to `web/app.py` only (remove any root copy). Set `cwd = "web"`, `start = "python3 app.py"`, `cmd = "true"`, `ready = "/"`, `port = 3400`. Run the gate.
   Expected: exit `0`; proves start ran with cwd `web` (root has no `app.py`).
8. Set `[qa] env = { CMS_URL = "https://example.test/g", LOCALE = "en-gb" }` and `start = "python3 app.py"` with `app.py` back at repo root and `cwd = "."`; create `tasks/t.md` with one line of text; run the gate once.
   Expected: `.marestail/qa-app.log` contains `https://example.test/g` and `en-gb`. Then from that temp repo root:
   `python3 -c "from pathlib import Path; from marestail import config,prompts,pipeline; c=config.load(Path('.')); r=Path('.marestail/r.md'); r.parent.mkdir(parents=True, exist_ok=True); r.write_text(''); print(prompts.worker_prompt(c, pipeline.find('qa'), Path('tasks/t.md'), 't', r, ''))"`
   Expected: printed prompt has no `example.test` and no `en-gb`; does contain `The app is started for the qa gate; its address is in MARESTAIL_APP_URL.`
9. Note mtime of `.marestail/qa-app.log` (`stat -c %Y .marestail/qa-app.log`). Remove `start` (leave `cmd = "true"`); run `marestail gate --tier qa --only qa`.
   Expected: passes; mtime of `.marestail/qa-app.log` is unchanged.
10. Restore `start = "python3 app.py"` and `cmd = "sleep 60"`. Run `marestail gate --tier qa --only qa` in the background; once `.marestail/qa-app.log` exists and a listener is up, send SIGINT to the gate process (`kill -INT <pid>`).
    Expected: gate exits; no listener left on the chosen port.
11. With `start` set, run `marestail gate --tier fast --only docs`.
    Expected: no app listener started.
12. Same temp repo with `start` set; run `MARESTAIL_TASK=t marestail gate --tier qa --only qa`.
    Expected: exit `0`; app log is `.marestail/runs/t/qa-app.log` (not `.marestail/qa-app.log`).
13. From the marestail-green repo root (proves runner wiring, not a hand-set env):

```bash
python3 <<'PY'
from pathlib import Path
from unittest.mock import patch
import os
from marestail import runner
from marestail import config as cm
from marestail.config import Config
c = Config(root=Path("."), raw={})
with (
    patch.object(cm, "load", return_value=c),
    patch.object(runner, "run_steps", return_value=0),
    patch.object(runner.perf_trees, "record_start"),
):
    os.environ.pop("MARESTAIL_TASK", None)
    runner.run_pipeline(Path("tasks/006-marestail-starts-the-app-for-qa.md"), "specifier", "specifier", True, None, 0)
print(os.environ.get("MARESTAIL_TASK"))
PY
```

    Expected: prints exactly `006-marestail-starts-the-app-for-qa`.
14. `grep -n 'start\|ready_timeout\|MARESTAIL_APP_URL\|MARESTAIL_QA_CMD_TIMEOUT\|MARESTAIL_TASK\|qa tier' README.md templates/marestail.toml | head -40`
    Expected: README names the new `[qa]` keys and says the app starts only for the `qa` tier; Environment variables table names `MARESTAIL_TASK` and `MARESTAIL_QA_CMD_TIMEOUT`; `templates/marestail.toml` has the new keys commented under `[qa]`.
15. `python3 tools/test-qa-ran-against.py; python3 tools/test-timeline.py; python3 tools/test-perf.py; echo exit=$?`
    Expected: ran-against and timeline exit 0; test-perf still exit 1 with `verdict-commit-files: '' != 'perf/bench_x.py'`.
