import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from marestail.sonar import setup


class Completed:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout


class Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, *size: int) -> bytes:
        return self.body


class FakeClient:
    def __init__(self, replies: dict[str, Any] | None = None) -> None:
        self.replies = replies or {}
        self.gets: list[tuple[str, dict[str, Any]]] = []
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str, **params: Any) -> Any:
        self.gets.append((path, params))
        return self.replies[path]

    def post(self, path: str, **params: Any) -> Any:
        self.posts.append((path, params))
        return self.replies.get(path, {})


@pytest.fixture
def commands(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], dict[str, Any]]]:
    seen: list[tuple[list[str], dict[str, Any]]] = []

    def run(command: list[str], **options: Any) -> Completed:
        seen.append((command, options))
        return Completed("cid\n")

    monkeypatch.setattr(subprocess, "run", run)
    return seen


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    jar = tmp_path / "cache" / "sha" / "sonar-erlang-plugin.jar"
    monkeypatch.setattr(setup, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(setup, "JAR_CACHE", jar)
    return jar


def record(monkeypatch: pytest.MonkeyPatch, calls: list[str], name: str) -> None:
    monkeypatch.setattr(setup, name, lambda *args: calls.append(f"{name}{args}"))


def test_compose_passes_plugins_dir(commands: list[tuple[list[str], dict[str, Any]]], paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEEP", "1")
    setup.down()
    command, options = commands[0]
    assert command == ["docker", "compose", "-f", str(setup.COMPOSE), "down"]
    assert options["check"] is True
    assert options["env"]["MARESTAIL_SONAR_PLUGINS"] == str(paths.parent.parent.parent / "plugins")
    assert options["env"]["KEEP"] == "1"
    assert setup.COMPOSE.name == "docker-compose.yml"


def test_up(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    for name in ("ensure_erlang_plugin_jar", "compose", "wait_until_up"):
        record(monkeypatch, calls, name)
    setup.up()
    assert calls == ["ensure_erlang_plugin_jar()", "compose('up', '-d')", "wait_until_up('http://localhost:9000',)"]


def test_wait_until_up(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = ["DOWN", "STARTING", "UP"]
    slept: list[float] = []
    monkeypatch.setattr(setup, "status", lambda url: replies.pop(0))
    monkeypatch.setattr(time, "sleep", slept.append)
    setup.wait_until_up("http://x")
    assert slept == [5, 5]


def test_wait_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    urls: list[str] = []
    slept: list[float] = []

    def status(url: str) -> str:
        urls.append(url)
        return "DOWN"

    monkeypatch.setattr(setup, "status", status)
    monkeypatch.setattr(time, "sleep", slept.append)
    with pytest.raises(SystemExit, match=r"^sonarqube did not come up$"):
        setup.wait_until_up("http://x", attempts=2)
    assert (urls, slept) == (["http://x", "http://x"], [5, 5])


@pytest.mark.parametrize(("body", "expected"), [(b'{"status": "UP"}', "UP"), (b"{}", ""), (b"not json", "DOWN")])
def test_status(monkeypatch: pytest.MonkeyPatch, body: bytes, expected: str) -> None:
    seen: list[tuple[str, Any]] = []

    def urlopen(url: str, timeout: Any) -> Response:
        seen.append((url, timeout))
        return Response(body)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert setup.status("http://h") == expected
    assert seen == [("http://h/api/system/status", 5)]


def install_setup(monkeypatch: pytest.MonkeyPatch, installed: list[bool]) -> list[str]:
    calls: list[str] = []
    for name in ("up", "compose", "wait_until_up", "ensure_project", "ensure_credentials"):
        record(monkeypatch, calls, name)

    def admin_client(password: str) -> str:
        calls.append(f"admin {password}")
        return password

    monkeypatch.setattr(setup, "admin_client", admin_client)
    monkeypatch.setattr(setup, "erlang_plugin_installed", lambda admin: installed.pop(0))
    return calls


def test_setup_ready(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("MARESTAIL_SONAR_PASSWORD", raising=False)
    calls = install_setup(monkeypatch, [True])
    setup.setup("key", "Name")
    assert calls == [
        "up()",
        "admin Marestail-Admin-2026!",
        "ensure_project('Marestail-Admin-2026!', 'key', 'Name')",
        "ensure_credentials('Marestail-Admin-2026!',)",
    ]
    assert capsys.readouterr().out == "sonar ready: http://localhost:9000/dashboard?id=key\n"


def test_setup_restarts_for_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARESTAIL_SONAR_PASSWORD", "pw")
    calls = install_setup(monkeypatch, [False, True])
    setup.setup("key", "Name")
    assert calls[:5] == ["up()", "admin pw", "compose('restart', 'sonarqube')", "wait_until_up('http://localhost:9000',)", "admin pw"]
    assert calls[5] == "ensure_project('pw', 'key', 'Name')"


def test_setup_fails_without_plugin(monkeypatch: pytest.MonkeyPatch, paths: Path) -> None:
    calls = install_setup(monkeypatch, [False, False])
    with pytest.raises(SystemExit) as raised:
        setup.setup("key", "Name")
    assert str(raised.value) == f"erlang plugin did not load from {setup.PLUGINS_DIR}; check: docker logs marestail-sonarqube"
    assert not any(call.startswith("ensure") for call in calls)


@pytest.mark.parametrize(("plugins", "expected"), [([{"key": "java"}, {"key": "erlang"}], True), ([{"name": "x"}], False), (None, False)])
def test_erlang_plugin_installed(plugins: list[dict[str, str]] | None, expected: bool) -> None:
    admin: Any = FakeClient({"api/plugins/installed": {} if plugins is None else {"plugins": plugins}})
    assert setup.erlang_plugin_installed(admin) is expected


def test_plugin_jar_copied_when_missing_or_stale(paths: Path) -> None:
    paths.parent.mkdir(parents=True)
    paths.write_bytes(b"jar-v1")
    setup.ensure_erlang_plugin_jar()
    target = setup.PLUGINS_DIR / "sonar-erlang-plugin.jar"
    assert target.read_bytes() == b"jar-v1"
    paths.write_bytes(b"jar-v2")
    setup.ensure_erlang_plugin_jar()
    assert target.read_bytes() == b"jar-v2"


def test_plugin_jar_left_alone_when_current(paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths.parent.mkdir(parents=True)
    paths.write_bytes(b"same")
    setup.PLUGINS_DIR.mkdir()
    (setup.PLUGINS_DIR / "sonar-erlang-plugin.jar").write_bytes(b"same")
    monkeypatch.setattr(shutil, "copyfile", lambda *args: pytest.fail("copied"))
    setup.ensure_erlang_plugin_jar()


def test_cached_jar_builds_once(paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    builds: list[int] = []

    def build() -> None:
        builds.append(1)
        paths.parent.mkdir(parents=True)
        paths.write_bytes(b"built")

    monkeypatch.setattr(setup, "build_jar", build)
    assert setup.cached_jar() == paths
    assert setup.cached_jar() == paths
    assert builds == [1]


def test_cached_jar_build_without_output(paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup, "build_jar", lambda: None)
    with pytest.raises(SystemExit) as raised:
        setup.cached_jar()
    assert str(raised.value) == (
        "docker build of https://github.com/evolution-gaming/sonar-erlang.git@1869ae81c3bd5213bb92241c2e90e25e46ec4287 produced no sonar-erlang-plugin.jar"
    )


def test_build_jar_clones_and_copies(
    paths: Path, commands: list[tuple[list[str], dict[str, Any]]], capsys: pytest.CaptureFixture[str]
) -> None:
    work = paths.parent / "src"
    work.mkdir(parents=True)
    (work / "stale").write_text("")
    setup.build_jar()
    sha = setup.ERLANG_PLUGIN_SHA
    image = f"marestail-sonar-erlang:{sha[:12]}"
    assert [command for command, _ in commands] == [
        ["git", "clone", "--no-checkout", setup.ERLANG_PLUGIN_REPO, str(work)],
        ["git", "-C", str(work), "fetch", "--depth", "1", "origin", sha],
        ["git", "-C", str(work), "checkout", "--force", "FETCH_HEAD"],
        ["docker", "build", "-t", image, "."],
        ["docker", "create", image],
        ["docker", "cp", "cid:/opt/src/sonar-erlang-plugin/target/sonar-erlang-plugin.jar", str(paths)],
        ["docker", "rm", "cid"],
    ]
    assert not (work / "stale").exists()
    assert commands[3][1] == {"cwd": work, "check": True}
    assert commands[4][1] == {"check": True, "capture_output": True, "text": True}
    assert capsys.readouterr().out == f"building sonar-erlang-plugin.jar (sonar-erlang @{sha[:8]}, cached afterwards)\n"


def test_build_jar_reuses_checkout_and_removes_container(paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (paths.parent / "src" / ".git").mkdir(parents=True)
    seen: list[list[str]] = []

    def run(command: list[str], **options: Any) -> Completed:
        seen.append(command)
        if command[1] == "cp":
            raise subprocess.CalledProcessError(1, command)
        return Completed(" c2 ")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        setup.build_jar()
    assert seen[0][:3] == ["git", "-C", str(paths.parent / "src")]
    assert seen[-1] == ["docker", "rm", "c2"]
    assert paths.parent.is_dir()


def test_admin_client_changes_default_password(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[tuple[Any, ...]] = []
    clients: list[FakeClient] = []

    def factory(*args: Any) -> FakeClient:
        made.append(args)
        clients.append(FakeClient({"api/authentication/validate": {}}))
        return clients[-1]

    monkeypatch.setattr(setup, "Client", factory)
    setup.admin_client("pw")
    assert made == [("http://localhost:9000", "admin", "admin"), ("http://localhost:9000", "admin", "pw")]
    assert clients[0].posts == [("api/users/change_password", {"login": "admin", "previousPassword": "admin", "password": "pw"})]


def test_admin_client_tolerates_changed_password(monkeypatch: pytest.MonkeyPatch) -> None:
    class Rejecting(FakeClient):
        def get(self, path: str, **params: Any) -> Any:
            raise SystemExit("401")

    made: list[Any] = []

    def client(*args: Any) -> Rejecting:
        made.append(args)
        return Rejecting()

    monkeypatch.setattr(setup, "Client", client)
    assert isinstance(setup.admin_client("pw"), Rejecting)
    assert made[-1] == ("http://localhost:9000", "admin", "pw")


def test_ensure_project() -> None:
    admin: Any = FakeClient({"api/projects/search": {"components": []}})
    setup.ensure_project(admin, "k", "N")
    assert admin.gets == [("api/projects/search", {"projects": "k"})]
    assert admin.posts == [("api/projects/create", {"project": "k", "name": "N"})]
    existing: Any = FakeClient({"api/projects/search": {"components": [{"key": "k"}]}})
    setup.ensure_project(existing, "k", "N")
    assert existing.posts == []


def test_ensure_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[tuple[str, str]] = []
    monkeypatch.setattr(setup, "credentials", lambda: None)
    monkeypatch.setattr(setup, "save_credentials", lambda url, token: saved.append((url, token)))
    monkeypatch.setattr(time, "time", lambda: 1700.9)
    admin: Any = FakeClient({"api/user_tokens/generate": {"token": "T"}})
    setup.ensure_credentials(admin)
    assert admin.posts == [("api/user_tokens/generate", {"name": "marestail-1700", "type": "USER_TOKEN"})]
    assert saved == [("http://localhost:9000", "T")]


def test_existing_credentials_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup, "credentials", lambda: {"url": "u", "token": "t"})
    admin: Any = FakeClient()
    setup.ensure_credentials(admin)
    assert admin.posts == []
