import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

from marestail.sonar.client import Client, credentials, save_credentials

COMPOSE = Path(__file__).resolve().parent / "docker-compose.yml"
DEFAULT_URL = "http://localhost:9000"
DEFAULT_PASSWORD = "Marestail-Admin-2026!"
ERLANG_PLUGIN_REPO = "https://github.com/evolution-gaming/sonar-erlang.git"
ERLANG_PLUGIN_SHA = "1869ae81c3bd5213bb92241c2e90e25e46ec4287"
ERLANG_PLUGIN_JAR = "sonar-erlang-plugin.jar"
CONFIG_DIR = Path.home() / ".config" / "marestail"
PLUGINS_DIR = CONFIG_DIR / "sonar-plugins"
JAR_CACHE = CONFIG_DIR / "sonar-erlang" / ERLANG_PLUGIN_SHA / ERLANG_PLUGIN_JAR


def up() -> None:
    ensure_erlang_plugin_jar()
    compose("up", "-d")
    wait_until_up(DEFAULT_URL)


def down() -> None:
    compose("down")


def compose(*args: str) -> None:
    env = {**os.environ, "MARESTAIL_SONAR_PLUGINS": str(PLUGINS_DIR)}
    subprocess.run(["docker", "compose", "-f", str(COMPOSE), *args], env=env, check=True)


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
    if not erlang_plugin_installed(admin):
        compose("restart", "sonarqube")
        wait_until_up(DEFAULT_URL)
        admin = admin_client(password)
        if not erlang_plugin_installed(admin):
            raise SystemExit(f"erlang plugin did not load from {PLUGINS_DIR}; check: docker logs marestail-sonarqube")
    ensure_project(admin, project_key, project_name)
    ensure_credentials(admin, project_key)
    print(f"sonar ready: {DEFAULT_URL}/dashboard?id={project_key}")


def erlang_plugin_installed(admin: Client) -> bool:
    return any(plugin.get("key") == "erlang" for plugin in admin.get("api/plugins/installed").get("plugins", []))


def ensure_erlang_plugin_jar() -> None:
    jar = cached_jar()
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    target = PLUGINS_DIR / ERLANG_PLUGIN_JAR
    if not target.exists() or target.read_bytes() != jar.read_bytes():
        shutil.copyfile(jar, target)


def cached_jar() -> Path:
    if JAR_CACHE.exists():
        return JAR_CACHE
    build_jar()
    if not JAR_CACHE.exists():
        raise SystemExit(f"docker build of {ERLANG_PLUGIN_REPO}@{ERLANG_PLUGIN_SHA} produced no {ERLANG_PLUGIN_JAR}")
    return JAR_CACHE


def build_jar() -> None:
    work = JAR_CACHE.parent / "src"
    if not (work / ".git").exists():
        shutil.rmtree(work, ignore_errors=True)
        subprocess.run(["git", "clone", "--no-checkout", ERLANG_PLUGIN_REPO, str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "fetch", "--depth", "1", "origin", ERLANG_PLUGIN_SHA], check=True)
    subprocess.run(["git", "-C", str(work), "checkout", "--force", "FETCH_HEAD"], check=True)
    image = f"marestail-sonar-erlang:{ERLANG_PLUGIN_SHA[:12]}"
    print(f"building {ERLANG_PLUGIN_JAR} (sonar-erlang @{ERLANG_PLUGIN_SHA[:8]}, cached afterwards)")
    subprocess.run(["docker", "build", "-t", image, "."], cwd=work, check=True)
    container = subprocess.run(["docker", "create", image], check=True, capture_output=True, text=True).stdout.strip()
    try:
        JAR_CACHE.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["docker", "cp", f"{container}:/opt/src/sonar-erlang-plugin/target/{ERLANG_PLUGIN_JAR}", str(JAR_CACHE)], check=True)
    finally:
        subprocess.run(["docker", "rm", container], check=True)


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
