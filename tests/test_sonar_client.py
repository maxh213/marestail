import io
import json
import stat
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from marestail.sonar import client
from marestail.sonar.client import Client


class Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def install(monkeypatch: pytest.MonkeyPatch, body: bytes) -> list[tuple[urllib.request.Request, Any]]:
    seen: list[tuple[urllib.request.Request, Any]] = []

    def urlopen(request: urllib.request.Request, timeout: Any) -> Response:
        seen.append((request, timeout))
        return Response(body)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return seen


def test_get_builds_query_and_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = install(monkeypatch, b'{"a": 1}')
    assert Client("http://hostX/", "tok").url == "http://hostX"
    assert Client("http://host:9000/", "tok").get("api/x", k="v w", ps=5) == {"a": 1}
    request, timeout = seen[0]
    assert request.full_url == "http://host:9000/api/x?k=v+w&ps=5"
    assert request.get_method() == "GET"
    assert request.method == "GET"
    assert request.data is None
    assert request.has_header("Authorization")
    assert request.get_header("Authorization") == "Basic dG9rOg=="
    assert timeout == client.TIMEOUT
    assert client.SLASH == "/"
    assert client.AUTHORIZATION == "Authorization"
    assert client.TIMEOUT == 60


def test_get_rejects_a_missing_param(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, b"{}")
    with pytest.raises(TypeError, match=r"^param$"):
        Client("http://host", "tok").get("api/x", projectKey=None)  # type: ignore[arg-type]


def test_post_sends_form_body_with_password(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = install(monkeypatch, b"  \n")
    assert Client("http://host", "admin", "pw").post("api/y", a="1", b="2") == {}
    request, timeout = seen[0]
    assert request.full_url == "http://host/api/y"
    assert request.get_method() == "POST"
    assert request.method == "POST"
    assert request.data == b"a=1&b=2"
    assert request.get_header("Authorization") == "Basic YWRtaW46cHc="
    assert timeout == 60


def test_request_adds_authorization_header(monkeypatch: pytest.MonkeyPatch) -> None:
    keys: list[str] = []
    original = urllib.request.Request.add_header

    def add_header(self: urllib.request.Request, key: str, val: str) -> None:
        keys.append(key)
        original(self, key, val)

    monkeypatch.setattr(urllib.request.Request, "add_header", add_header)
    install(monkeypatch, b"{}")
    Client("http://host", "t").get("api/z")
    assert keys == [client.AUTHORIZATION]


def test_http_error_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(request: urllib.request.Request, timeout: Any) -> Response:
        headers: Any = {}
        raise urllib.error.HTTPError(request.full_url, 403, "no", headers, io.BytesIO(b"x" * 400))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    client = Client("http://host", "t")
    with pytest.raises(SystemExit) as raised:
        client.get("api/z", q="1")
    assert str(raised.value) == "sonar GET api/z?q=1: 403 " + "x" * 300


def test_credentials_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "cfg" / "marestail" / "sonar.json"
    monkeypatch.setattr(client, "CREDENTIALS", path)
    assert client.credentials() is None
    client.save_credentials("http://u", "secret")
    path.unlink()
    client.save_credentials("http://u", "secret")
    assert json.loads(path.read_text()) == {"url": "http://u", "token": "secret"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert client.credentials() == {"url": "http://u", "token": "secret"}
