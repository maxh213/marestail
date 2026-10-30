#!/usr/bin/env python3
import argparse
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

import harness

os.environ.pop("MARESTAIL_RUN_SUFFIX", None)
os.environ.pop("MARESTAIL_PERF_DB_PORT", None)
harness.use_tree()

from marestail import cli as cli_module
from marestail import runner as runner_module
from marestail.config import Config
from marestail.context import Context
from marestail.gates import sonar as sonar_gate
from marestail.perf import db as db_module
from marestail.sonar import setup as setup_module

try:
    from marestail import run_suffix as run_suffix_module
except ImportError:
    run_suffix_module = None

ENV = "MARESTAIL_RUN_SUFFIX" if run_suffix_module is None else run_suffix_module.ENV
PORT = "MARESTAIL_PERF_DB_PORT"
CREDS = {"url": "http://127.0.0.1:9", "token": "t"}
IMAGE = "postgres:16"
Row = Callable[[], Any] | None
scratch = Path(tempfile.mkdtemp(prefix="bench-run-suffix-"))
runner_module.run_pipeline = lambda *_args, **_kwargs: 0
cli_module.focused_gate_command = lambda *_args, **_kwargs: 0
cli_module.hook_command = lambda *_args, **_kwargs: 0
sonar_gate.scan = lambda _ctx, _creds, _key: (1, "", scratch / "missing-report")
db_module.docker = lambda _database, *_args, **_kwargs: (0, "mp-files\nmp-db-abc-head\nother\n")
kept_prune = getattr(setup_module, "prune", None)
kept_delete = getattr(setup_module, "_delete_found", None)
setup_module.up = lambda *_args, **_kwargs: None
setup_module.down = lambda *_args, **_kwargs: None
setup_module.setup = lambda *_args, **_kwargs: None
if hasattr(setup_module, "prune"):
    setup_module.prune = lambda *_args, **_kwargs: None


class Found:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def get(self, _path: str, **_params: Any) -> dict[str, Any]:
        return {"components": [{"key": "key_a1"}]}

    def post(self, _path: str, **_params: Any) -> dict[str, Any]:
        return {}


class Missing:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def get(self, _path: str, **_params: Any) -> dict[str, Any]:
        return {"components": []}

    def post(self, _path: str, **_params: Any) -> dict[str, Any]:
        return {}


class Quiet:
    def up(self) -> None:
        return None

    def down(self) -> None:
        return None


def bound(module: Any, name: str) -> Row:
    if module is None:
        return None
    found = getattr(module, name, None)
    return found if callable(found) else None


def prepare(suffix: str | None, bad: str | None, port: str | None) -> None:
    os.environ.pop(ENV, None)
    os.environ.pop(PORT, None)
    if bad is not None:
        os.environ[ENV] = bad
    elif suffix is not None:
        os.environ[ENV] = suffix
    if port is not None:
        os.environ[PORT] = port


def emit(target: str, fn: Row, suffix: str | None = None, bad: str | None = None, port: str | None = None) -> None:
    if not callable(fn):
        harness.absent(target)
        return
    prepare(suffix, bad, port)
    try:
        harness.emit(target, harness.measure(fn))
    finally:
        os.environ.pop(ENV, None)
        os.environ.pop(PORT, None)


def context(path: Path, raw: dict[str, Any], properties: str | None = None) -> Context:
    path.mkdir(parents=True)
    (path / ".marestail").mkdir()
    if properties is not None:
        (path / "sonar-project.properties").write_text(properties)
    return Context(config=Config(root=path, raw=raw))


def database(suffix: str | None) -> Any:
    if suffix is not None and "suffix" not in db_module.Database.__dataclass_fields__:
        return None
    fields: dict[str, Any] = {
        "root": scratch / "repo",
        "migrate": "m",
        "url_env": "DATABASE_URL",
        "rows": 0,
        "rows_source": "bench",
        "migrations": [],
        "skip_tables": [],
        "min_free_gb": 1.0,
        "image": IMAGE,
        "image_source": "bench",
        "port": 55432,
        "prefix": "mp-",
        "volume": "vol",
        "home": scratch / "home",
    }
    if suffix is not None:
        fields["suffix"] = suffix
    return db_module.Database(**fields)


def call(fn: Row, *args: Any) -> Row:
    if fn is None:
        return None
    return lambda: fn(*args)


def catches(fn: Row, error: type[BaseException], *args: Any) -> Row:
    if fn is None:
        return None

    def attempt() -> None:
        try:
            fn(*args)
        except error:
            return

    return attempt


def parser_call(name: str) -> Row:
    fn = bound(cli_module, name)
    if fn is None:
        return None
    return lambda: fn(argparse.ArgumentParser())


def gate_ns(flag: str | None, hook: bool = False) -> argparse.Namespace:
    return argparse.Namespace(hook=hook, run_suffix=flag, focus=[], scope=None, tier="fast", only=None, json=False)


def run_ns(flag: str | None) -> argparse.Namespace:
    return argparse.Namespace(
        task="tasks/024.md",
        start=None,
        stop=None,
        auto=False,
        model=None,
        retries=0,
        agent=None,
        effort=None,
        scope=None,
        focus=None,
        run_suffix=flag,
    )


def sonar_ns(action: str, flag: str | None) -> argparse.Namespace:
    return argparse.Namespace(action=action, run_suffix=flag)


def sonar_action(action: str, flag: str | None) -> Row:
    if action == "prune" and "prune" not in cli_module.SONAR_ACTIONS:
        return None
    return catches(cli_module.sonar_command, SystemExit, sonar_ns(action, flag))


def build_call(section: dict[str, Any]) -> Row:
    config = Config(root=scratch / "repo", raw={})
    return lambda: db_module.build_database(config, section, 0, "bench", IMAGE, "bench")


def start_call(stored: Any) -> Row:
    if stored is None:
        return None
    return lambda: db_module.start_postgres(stored, "mp-db-head", "/perf/work/head", 55432, 1)


def prune_call(kind: str) -> Row:
    if kept_prune is None:
        return None
    if kind == "unset":
        setup_module.credentials = lambda: None
        return catches(kept_prune, SystemExit, "key_a1")
    setup_module.credentials = lambda: CREDS
    setup_module.Client = Found if kind == "found" else Missing
    return lambda: kept_prune("key_a1")


def delete_call(found: bool) -> Row:
    if kept_delete is None:
        return None
    client = Found() if found else Missing()
    return lambda: kept_delete(client, "key_a1")


def measure_suffix() -> None:
    current = bound(run_suffix_module, "current")
    named = bound(run_suffix_module, "named")
    titled = bound(run_suffix_module, "titled")
    argument = bound(run_suffix_module, "argument")
    emit("run_suffix.current", call(current))
    emit("run_suffix.current set", call(current), suffix="a1")
    emit("run_suffix.current bad", catches(current, SystemExit), bad="A1")
    emit("run_suffix.named", call(named, "k"))
    emit("run_suffix.named set", call(named, "k"), suffix="a1")
    emit("run_suffix.titled", call(titled, "K"))
    emit("run_suffix.titled set", call(titled, "K"), suffix="a1")
    emit("run_suffix.argument", call(argument, "a1"))
    emit("run_suffix.argument bad", catches(argument, argparse.ArgumentTypeError, "A1"))
    emit("run_suffix._matches", call(bound(run_suffix_module, "_matches"), "a1"))
    emit("run_suffix._rejected", call(bound(run_suffix_module, "_rejected"), "A1"))
    emit("run_suffix._flag_text", call(bound(run_suffix_module, "_flag_text"), "A1"))


def measure_cli() -> None:
    publish = bound(cli_module, "_publish_suffix")
    direction = bound(cli_module, "_up_or_down")
    identity = bound(cli_module, "_sonar_identity")
    prune_key = bound(cli_module, "_prune_key")
    require = bound(cli_module, "_require_key")
    suffixed = bound(cli_module, "_suffixed_key")
    titled = bound(cli_module, "_titled_name")
    quiet = Quiet()
    emit("cli.add_perf", parser_call("add_perf"))
    emit("cli.add_gate", parser_call("add_gate"))
    emit("cli.add_run", parser_call("add_run"))
    emit("cli.add_sonar", parser_call("add_sonar"))
    emit("cli.add_run_suffix", parser_call("add_run_suffix"))
    emit("cli.gate_command", lambda: cli_module.gate_command(gate_ns(None)))
    emit("cli.gate_command hook", lambda: cli_module.gate_command(gate_ns(None, True)))
    emit("cli.gate_command suffix", lambda: cli_module.gate_command(gate_ns("a1")))
    bad_gate = None if run_suffix_module is None else catches(cli_module.gate_command, SystemExit, gate_ns(None))
    emit("cli.gate_command bad", bad_gate, bad="A1")
    emit("cli.run_command", lambda: cli_module.run_command(run_ns(None)))
    emit("cli.run_command suffix", lambda: cli_module.run_command(run_ns("a1")))
    emit("cli.sonar_command up", sonar_action("up", None))
    emit("cli.sonar_command up suffix", sonar_action("up", "a1"))
    emit("cli.sonar_command down", sonar_action("down", None))
    emit("cli.sonar_command setup", sonar_action("setup", None))
    emit("cli.sonar_command setup suffix", sonar_action("setup", "a1"))
    emit("cli.sonar_command prune", sonar_action("prune", "a1"))
    emit("cli.sonar_command prune missing", sonar_action("prune", None))
    emit("cli._publish_suffix", call(publish, argparse.Namespace(run_suffix=None)))
    emit("cli._publish_suffix set", call(publish, argparse.Namespace(run_suffix="a1")))
    emit("cli._up_or_down", call(direction, quiet, "up"))
    emit("cli._up_or_down down", call(direction, quiet, "down"))
    emit("cli._sonar_identity", call(identity))
    emit("cli._sonar_identity suffix", call(identity), suffix="a1")
    emit("cli._prune_key", call(prune_key), suffix="a1")
    emit("cli._prune_key missing", catches(prune_key, SystemExit))
    emit("cli._sonar_pair", call(bound(cli_module, "_sonar_pair")))
    emit("cli._require_key", call(require, "proj"), suffix="a1")
    emit("cli._require_key missing", catches(require, SystemExit, ""), suffix="a1")
    emit("cli._suffixed_key", call(suffixed, "proj"))
    emit("cli._suffixed_key empty", call(suffixed, ""))
    emit("cli._titled_name", call(titled, "Nice"))
    emit("cli._titled_name set", call(titled, "Nice"), suffix="a1")


def measure_sonar(named: Context, props: Context, key_only: Context, bare: Context) -> None:
    blocks = bound(sonar_gate, "_suffix_blocks")
    api_key = bound(sonar_gate, "_api_key")
    project_name = bound(sonar_gate, "_project_name_argument")
    title = bound(sonar_gate, "_project_title")
    properties = bound(sonar_gate, "_properties_name")
    begin = bound(sonar_gate, "_begin_scan")
    tools = named.work / "dotnet-tools"
    build = named.root / "App.csproj"
    emit("sonar.scanner_properties suffix", lambda: sonar_gate.scanner_properties(named, "proj"), suffix="a1")
    emit("sonar.scanner_properties properties", lambda: sonar_gate.scanner_properties(props, "proj"), suffix="a1")
    emit("sonar.write_scan_script", lambda: sonar_gate.write_scan_script(named, CREDS, "proj", tools, build))
    emit("sonar.write_scan_script suffix", lambda: sonar_gate.write_scan_script(named, CREDS, "proj", tools, build), suffix="a1")
    emit("sonar._begin_scan", call(begin, named, CREDS, "proj"))
    emit("sonar._begin_scan suffix", call(begin, named, CREDS, "proj"), suffix="a1")
    emit("sonar._project_name_argument", call(project_name, named, "proj"))
    emit("sonar._project_name_argument suffix", call(project_name, props, "proj"), suffix="a1")
    emit("sonar._project_title", call(title, named, "proj"))
    emit("sonar._project_title properties", call(title, props, "proj"))
    emit("sonar._project_title fallback", call(title, key_only, "proj"))
    emit("sonar._properties_name", call(properties, props))
    emit("sonar._properties_name miss", call(properties, named))
    emit("sonar._suffix_blocks", call(blocks, "proj"))
    emit("sonar._suffix_blocks set", call(blocks, None), suffix="a1")
    emit("sonar._api_key", call(api_key, "proj"))
    emit("sonar._api_key empty", call(api_key, None))
    emit("sonar.analyse", lambda: sonar_gate.analyse(named, CREDS, 0.0))
    emit("sonar.analyse blocked", lambda: sonar_gate.analyse(bare, CREDS, 0.0), suffix="a1")
    emit("sonar.prune found", prune_call("found"))
    emit("sonar.prune missing", prune_call("missing"))
    emit("sonar.prune unset", prune_call("unset"))
    emit("sonar._delete_found found", delete_call(True))
    emit("sonar._delete_found missing", delete_call(False))


def measure_db(plain: Any, suffixed: Any) -> None:
    host = bound(db_module, "_host_port")
    slot = bound(db_module, "_port_slot")
    labelled = bound(db_module, "_suffix_label")
    down_names = bound(db_module, "_down_names")
    emit("perf.helper", None if plain is None else lambda: plain.helper)
    emit("perf.tree_container", None if plain is None else lambda: plain.tree_container("head"))
    emit("perf.golden_container", None if plain is None else lambda: plain.golden_container("g"))
    emit("perf.work_dir", None if plain is None else lambda: plain.work_dir("head"))
    emit("perf.helper suffix", None if suffixed is None else lambda: suffixed.helper)
    emit("perf.tree_container suffix", None if suffixed is None else lambda: suffixed.tree_container("head"))
    emit("perf.golden_container suffix", None if suffixed is None else lambda: suffixed.golden_container("g"))
    emit("perf.work_dir suffix", None if suffixed is None else lambda: suffixed.work_dir("head"))
    emit("perf.build_database", build_call({}))
    emit("perf.build_database suffix", build_call({}), suffix="a1")
    emit("perf.build_database port", build_call({"port": 6000}), suffix="a1")
    emit("perf.build_database given", build_call({}), suffix="a1", port="7000")
    emit("perf._with_suffix", call(bound(db_module, "_with_suffix"), "mp-files", None))
    emit("perf._with_suffix set", call(bound(db_module, "_with_suffix"), "mp-files", "a1"))
    emit("perf._host_port", call(host, {}, None))
    emit("perf._host_port suffix", call(host, {}, "a1"))
    emit("perf._host_port given", call(host, {}, "a1"), port="7000")
    emit("perf._port_slot", call(slot, None))
    emit("perf._port_slot suffix", call(slot, "a1"))
    emit("perf._run_label", call(bound(db_module, "_run_label"), plain))
    emit("perf._run_label suffix", None if suffixed is None else call(bound(db_module, "_run_label"), suffixed))
    emit("perf._suffix_label", None if suffixed is None else call(labelled, suffixed))
    emit("perf.ensure_helper", lambda: db_module.ensure_helper(plain))
    emit("perf.ensure_helper suffix", None if suffixed is None else lambda: db_module.ensure_helper(suffixed))
    emit("perf.start_postgres", start_call(plain))
    emit("perf.start_postgres suffix", start_call(suffixed))
    emit("perf.down", lambda: db_module.down(plain))
    emit("perf.down suffix", None if suffixed is None else lambda: db_module.down(suffixed))
    emit("perf._down_names", call(down_names, plain))
    emit("perf._down_names suffix", None if suffixed is None else call(down_names, suffixed))
    emit("perf._prefixed_names", call(bound(db_module, "_prefixed_names"), plain))
    emit("perf._labelled_names", None if suffixed is None else call(bound(db_module, "_labelled_names"), suffixed))


def main() -> None:
    named = context(scratch / "named", {"sonar": {"project_key": "proj", "project_name": "Nice"}})
    props = context(scratch / "props", {"sonar": {"project_key": "proj"}}, "sonar.projectName=Props\n")
    key_only = context(scratch / "key", {"sonar": {"project_key": "proj"}})
    bare = context(scratch / "bare", {})
    (scratch / "repo").mkdir(parents=True)
    (scratch / "home").mkdir(parents=True)
    try:
        measure_suffix()
        measure_cli()
        measure_sonar(named, props, key_only, bare)
        measure_db(database(None), database("a1"))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


main()
