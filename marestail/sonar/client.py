import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CREDENTIALS = Path.home() / ".config" / "marestail" / "sonar.json"
SLASH = "/"
AUTHORIZATION = "Authorization"
TIMEOUT = 60

Param = str | int


class Client:
    def __init__(self, url: str, token: str, password: str | None = None) -> None:
        self.url = url.rstrip(SLASH)
        self.basic = f"{token}:" if password is None else f"{token}:{password}"

    def get(self, path: str, **params: Param) -> dict[str, Any]:
        query = urllib.parse.urlencode(params)
        return self.request("GET", f"{path}?{query}")

    def post(self, path: str, **params: Param) -> dict[str, Any]:
        body = urllib.parse.urlencode(params).encode()
        return self.request("POST", path, body)

    def request(self, method: str, path: str, body: bytes | None = None) -> dict[str, Any]:
        request = urllib.request.Request(f"{self.url}/{path}", data=body, method=method)
        request.add_header(AUTHORIZATION, "Basic " + base64.b64encode(self.basic.encode()).decode())
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                text = response.read().decode()
        except urllib.error.HTTPError as error:
            raise SystemExit(f"sonar {method} {path}: {error.code} {error.read().decode()[:300]}") from error
        return decoded(text)


def decoded(text: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(text) if text.strip() else {}
    return data


def credentials() -> dict[str, str] | None:
    if not CREDENTIALS.exists():
        return None
    stored: dict[str, str] = json.loads(CREDENTIALS.read_text())
    return stored


def save_credentials(url: str, token: str) -> None:
    CREDENTIALS.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS.write_text(json.dumps({"url": url, "token": token}))
    CREDENTIALS.chmod(0o600)
