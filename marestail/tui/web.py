import json
import os
import socketserver
import threading
import time
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from marestail.tui import web_render as render
from marestail.tui import web_sources as sources

HTML = "text/html; charset=utf-8"
JSON = "application/json"
OK = "200 OK"
NOT_FOUND = "404 Not Found"
NO_SUCH_RUN = json.dumps({"error": "no such run"})
RUNS_SCRIPT = Path.home() / ".claude" / "skills" / "marestail-runs" / "scripts" / "runs.py"
CACHE = Path.home() / ".cache" / "marestail" / "dash-dandelion.json"
Reply = tuple[str, str, str]
StartResponse = Callable[..., Any]
App = Callable[[dict[str, Any], StartResponse], Iterable[bytes]]


def quiet(*_: object) -> None:
    return None


class QuietHandler(WSGIRequestHandler):
    log_message = quiet


class ThreadingServer(socketserver.ThreadingMixIn, WSGIServer):
    daemon_threads = True


def page(dash: sources.Dash) -> str:
    now = time.time()
    sections = [
        render.render_host(sources.host_or_error(dash)),
        render.render_dandelion(dash.dandelion, now),
        render.render_fleet(sources.beds(dash)),
        render.render_runs(sources.runs_table(dash), now),
    ]
    stamp = datetime.fromtimestamp(now).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    return render.document(os.uname().nodename, stamp, sections)


def first(params: dict[str, list[str]], key: str) -> str:
    return params.get(key, [""])[0]


def found_reply(found: sources.Record) -> Reply:
    return OK, json.dumps(found), JSON


def missing_reply(_found: None) -> Reply:
    return NOT_FOUND, NO_SUCH_RUN, JSON


def task_reply(dash: sources.Dash, query: str) -> Reply:
    params = parse_qs(query)
    found = sources.task_view(dash, first(params, "repo"), first(params, "task"))
    chosen = (missing_reply, found_reply)[found is not None]
    return chosen(found)


def json_reply(dash: sources.Dash, _query: str) -> Reply:
    return OK, json.dumps({"dandelion": dash.dandelion.data, "dandelion_at": dash.dandelion.at, "runs": sources.runs_table(dash)}), JSON


def page_reply(dash: sources.Dash, _query: str) -> Reply:
    return OK, page(dash), HTML


ROUTES = {"/json": json_reply, "/task": task_reply}


def answer(dash: sources.Dash, path: str, query: str) -> Reply:
    handler = ROUTES.get(path, page_reply)
    return handler(dash, query)


def reply(start_response: StartResponse, status: str, body: str, content_type: str, send_body: bool) -> list[bytes]:
    data = body.encode()
    start_response(status, [("Content-Type", content_type), ("Content-Length", str(len(data))), ("Cache-Control", "no-store")])
    return ([], [data])[send_body]


def head_response(_dash: sources.Dash, _environ: dict[str, Any], start_response: StartResponse) -> list[bytes]:
    return reply(start_response, OK, "", HTML, send_body=False)


def get_response(dash: sources.Dash, environ: dict[str, Any], start_response: StartResponse) -> list[bytes]:
    status, body, content_type = answer(dash, sources.first_text(environ.get("PATH_INFO"), "/"), environ.get("QUERY_STRING", ""))
    return reply(start_response, status, body, content_type, send_body=True)


def application(dash: sources.Dash) -> App:
    def app(environ: dict[str, Any], start_response: StartResponse) -> Iterable[bytes]:
        chosen = (get_response, head_response)[environ.get("REQUEST_METHOD") == "HEAD"]
        return chosen(dash, environ, start_response)

    return app


def start_probes(dash: sources.Dash) -> None:
    sources.recall(dash)
    threading.Thread(target=sources.probe_forever, args=(dash,), daemon=True).start()
    threading.Thread(target=sources.cpu_forever, args=(dash,), daemon=True).start()


def run(root: Path, host: str, port: int, every: float, runs_script: Path | None) -> int:
    script = next(filter(None, (runs_script, RUNS_SCRIPT)))
    dash = sources.Dash(root=root, every=every, runs_script=script, cache=CACHE)
    start_probes(dash)
    with make_server(host, port, application(dash), server_class=ThreadingServer, handler_class=QuietHandler) as server:
        server.serve_forever()
    return 0
