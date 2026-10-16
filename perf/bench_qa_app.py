#!/usr/bin/env python3
import os
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import harness

root = harness.use_tree()

from marestail.config import Config
from marestail.context import Context, build
from marestail.gates import py_lint, qa
from marestail.pipeline import Worker

TARGETS_SERVE = (
    "_serve._chosen_port",
    "_serve._can_bind",
    "_serve._free_port",
    "_serve._answers",
    "_serve.ready_app",
)

APP = (
    "import os\n"
    "from http.server import HTTPServer, SimpleHTTPRequestHandler\n"
    'HTTPServer(("127.0.0.1", int(os.environ["PORT"])), SimpleHTTPRequestHandler).serve_forever()\n'
)


class OkHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_args: object) -> None:
        return None


def with_qa(raw: dict) -> Context:
    return Context(config=Config(root=root, raw=raw))


folder = Path(tempfile.mkdtemp())
log_path = folder / "qa-app.log"
(folder / "app.py").write_text(APP)
os.environ.pop("MARESTAIL_TASK", None)
os.environ.pop("MARESTAIL_QA_CMD_TIMEOUT", None)

try:
    ctx = with_qa({"qa": {"cmd": "true", "cwd": "."}})
    harness.emit("qa.run_gate", harness.measure(lambda: qa.run_gate(ctx)))

    if hasattr(qa, "_qa_cwd"):
        harness.emit("qa._qa_cwd", harness.measure(lambda: qa._qa_cwd(ctx)))
        harness.emit("qa._cmd_timeout", harness.measure(qa._cmd_timeout))
        harness.emit("qa._qa_env", harness.measure(lambda: qa._qa_env(with_qa({"qa": {"env": {"A": "1"}}}))))
        harness.emit("qa._app_log_path", harness.measure(lambda: qa._app_log_path(folder)))
        sample_log = folder / "sample.log"
        sample_log.write_text("\n".join(f"l{i}" for i in range(12)) + "\n")
        harness.emit("qa._log_tail", harness.measure(lambda: qa._log_tail(sample_log)))
    else:
        for target in ("qa._qa_cwd", "qa._cmd_timeout", "qa._qa_env", "qa._app_log_path", "qa._log_tail"):
            harness.absent(target)

    try:
        from marestail.gates import _serve
    except ImportError:
        _serve = None

    if _serve is None:
        for target in TARGETS_SERVE:
            harness.absent(target)
    else:
        port = _serve._free_port()
        harness.emit("_serve._chosen_port", harness.measure(lambda: _serve._chosen_port(port)))
        harness.emit("_serve._can_bind", harness.measure(lambda: _serve._can_bind(port)))
        harness.emit("_serve._free_port", harness.measure(_serve._free_port))

        server = HTTPServer(("127.0.0.1", 0), OkHandler)
        alive = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{alive}/"
        try:
            harness.emit("_serve._answers", harness.measure(lambda: _serve._answers(url)))
        finally:
            server.shutdown()
            thread.join(timeout=5)

        def ready_once() -> None:
            preferred = _serve._free_port()
            with _serve.ready_app("python3 app.py", folder, preferred, "/", 30, {}, log_path) as (failure, _chosen):
                if failure:
                    raise RuntimeError(failure)

        harness.emit("_serve.ready_app", harness.measure(ready_once))

    try:
        from marestail import prompts
    except ImportError:
        prompts = None

    if prompts is not None and hasattr(prompts, "qa_app_note"):
        note_config = Config(root=root, raw={"qa": {"start": "python3 app.py"}})
        worker = Worker("qa", "qa")
        harness.emit("prompts.qa_app_note", harness.measure(lambda: prompts.qa_app_note(note_config, worker)))
    else:
        harness.absent("prompts.qa_app_note")

    exclusions = getattr(py_lint, "path_exclusions", None) or getattr(py_lint, "benchmark_exclusion", None)
    lint_ctx = build(harness.make_config(root), False)
    if exclusions is not None:
        harness.emit("py_lint.path_exclusions", harness.measure(lambda: exclusions(lint_ctx)))
    else:
        harness.absent("py_lint.path_exclusions")
finally:
    shutil.rmtree(folder, ignore_errors=True)
