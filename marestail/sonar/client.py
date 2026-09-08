import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CREDENTIALS = Path.home() / ".config" / "marestail" / "sonar.json"


class Client:
    def __init__(self, url: str, token: str, password: str | None = None) -> None:
        self.url = url.rstrip("/")
        self.basic = f"{token}:" if password is None else f"{token}:{password}"

    def get(self, path: str, **params) -> dict:
        query = urllib.parse.urlencode(params)
        return self.request("GET", f"{path}?{query}")

    def post(self, path: str, **params) -> dict:
        body = urllib.parse.urlencode(params).encode()
        return self.request("POST", path, body)

    def request(self, method: str, path: str, body: bytes | None = None) -> dict:
        request = urllib.request.Request(f"{self.url}/{path}", data=body, method=method)
        request.add_header("Authorization", "Basic " + base64.b64encode(self.basic.encode()).decode())
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                text = response.read().decode()
        except urllib.error.HTTPError as error:
            raise SystemExit(f"sonar {method} {path}: {error.code} {error.read().decode()[:300]}") from error
        return json.loads(text) if text.strip() else {}


def credentials() -> dict | None:
    if not CREDENTIALS.exists():
        return None
    return json.loads(CREDENTIALS.read_text())


def save_credentials(url: str, token: str) -> None:
    CREDENTIALS.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS.write_text(json.dumps({"url": url, "token": token}))
    CREDENTIALS.chmod(0o600)
