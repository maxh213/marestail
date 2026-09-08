import os
import subprocess
import time
import urllib.request
from pathlib import Path

from marestail.sonar.client import Client, credentials, save_credentials

COMPOSE = Path(__file__).resolve().parent / "docker-compose.yml"
DEFAULT_URL = "http://localhost:9000"
DEFAULT_PASSWORD = "Marestail-Admin-2026!"


def up() -> None:
    subprocess.run(["docker", "compose", "-f", str(COMPOSE), "up", "-d"], check=True)
    wait_until_up(DEFAULT_URL)


def down() -> None:
    subprocess.run(["docker", "compose", "-f", str(COMPOSE), "down"], check=True)


def wait_until_up(url: str, attempts: int = 120) -> None:
    for _ in range(attempts):
        if status(url) == "UP":
            return
        time.sleep(5)
    raise SystemExit("sonarqube did not come up")


def status(url: str) -> str:
    try:
        with urllib.request.urlopen(f"{url}/api/system/status", timeout=5) as response:
            import json

            return json.load(response).get("status", "")
    except Exception:
        return "DOWN"


def setup(project_key: str, project_name: str) -> None:
    up()
    password = os.environ.get("MARESTAIL_SONAR_PASSWORD", DEFAULT_PASSWORD)
    admin = admin_client(password)
    ensure_project(admin, project_key, project_name)
    ensure_credentials(admin, project_key)
    print(f"sonar ready: {DEFAULT_URL}/dashboard?id={project_key}")


def admin_client(password: str) -> Client:
    fresh = Client(DEFAULT_URL, "admin", "admin")
    try:
        fresh.get("api/authentication/validate")
        fresh.post("api/users/change_password", login="admin", previousPassword="admin", password=password)
    except SystemExit:
        pass
    return Client(DEFAULT_URL, "admin", password)


def ensure_project(admin: Client, key: str, name: str) -> None:
    existing = admin.get("api/projects/search", projects=key)["components"]
    if not existing:
        admin.post("api/projects/create", project=key, name=name)


def ensure_credentials(admin: Client, key: str) -> None:
    if credentials() is not None:
        return
    token = admin.post("api/user_tokens/generate", name=f"marestail-{int(time.time())}", type="USER_TOKEN")["token"]
    save_credentials(DEFAULT_URL, token)
