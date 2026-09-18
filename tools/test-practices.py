#!/usr/bin/env python3
import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail import freeze, install, practices, runner
from marestail.config import Config
from marestail.pipeline import find, names
from marestail.runner import Run, run_judge, run_step

ROOT = Path(__file__).resolve().parent.parent


def expect(name, got, wanted):
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def new_repo(tmp: str, guidance: bool = False) -> Path:
    root = Path(tmp) / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@marestail")
    git(root, "config", "user.name", "test")
    (root / "marestail.toml").write_text('[git]\nbase = "main"\n')
    (root / ".gitignore").write_text(".marestail/\n")
    (root / "src.py").write_text("original\n")
    if guidance:
        (root / "guidance").mkdir()
        (root / "guidance" / "ts.md").write_text("# TypeScript practices\n\n- TS-1: prefer union types over enums\n")
    (root / "tasks").mkdir()
    (root / "tasks" / "t.md").write_text("# Add one\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root


def state(root: Path, raw=None, retries: int = 1) -> Run:
    return Run(config=Config(root=root, raw=raw or {"git": {"base": "main"}}), task=root / "tasks" / "t.md", model=None, retries=retries, agent="claude")


def passing_judge(verdict: str) -> str:
    return "\n".join(
        [
            "#!/usr/bin/env python3",
            "import json, re, sys",
            "from pathlib import Path",
            "prompt = sys.stdin.read()",
            'verdict = Path(re.search(r"Write your verdict to (\\S+) and", prompt).group(1))',
            f"verdict.write_text({verdict!r})",
            'print(json.dumps({"is_error": False, "num_turns": 1, "total_cost_usd": 0, "result": "ok"}))',
            "",
        ]
    )


@contextlib.contextmanager
def quiet():
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        yield


@contextlib.contextmanager
def agent_stub(folder: Path, body: str):
    stub = folder / "stub-agent"
    stub.write_text(body)
    stub.chmod(0o755)
    keys = ["MARESTAIL_CLAUDE", "MARESTAIL_AGENT"]
    previous = {key: os.environ.get(key) for key in keys}
    os.environ["MARESTAIL_CLAUDE"] = str(stub)
    os.environ.pop("MARESTAIL_AGENT", None)
    try:
        yield
    finally:
        for key in keys:
            if previous[key] is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous[key]


def pipeline_order():
    expect("pipeline", names(), ["specifier", "critic", "coder", "cleaner", "architect", "practices", "perf", "hardener", "qa"])
    judge = find("practices")
    expect("practices-bounce-to", judge.bounce_to, "coder")
    expect("practices-tier", judge.tier, None)
    expect("practices-writes", judge.writes, ())
    expect("practices-pinned", judge.pinned_bounce, True)
    expect("practices-optional", judge.optional, True)


def guidance_files():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        expect("missing-folder", practices.files(root), [])
        folder = root / "guidance"
        folder.mkdir()
        (folder / "b.md").write_text("b\n")
        (folder / "a.md").write_text("a\n")
        (folder / "c.txt").write_text("c\n")
        (folder / "sub").mkdir()
        (folder / "sub" / "x.md").write_text("x\n")
        expect("found-sorted", [path.name for path in practices.files(root)], ["a.md", "b.md"])


def no_guidance_skip():
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp)
        before = git(root, "rev-parse", "HEAD")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            passed = run_step(state(root), find("practices"))
        expect("noguidance-passes", passed, True)
        expect("noguidance-message", "practices: no guidance files; skipping" in output.getvalue(), True)
        expect("noguidance-no-commit", git(root, "rev-parse", "HEAD"), before)


def disabled_skip():
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp, guidance=True)
        before = git(root, "rev-parse", "HEAD")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            passed = run_step(state(root, {"git": {"base": "main"}, "practices": {"enabled": False}}), find("practices"))
        expect("disabled-passes", passed, True)
        expect("disabled-message", "practices disabled in marestail.toml; skipping" in output.getvalue(), True)
        expect("disabled-no-commit", git(root, "rev-parse", "HEAD"), before)


def freeze_guidance():
    config = Config(root=Path("/tmp"), raw={})
    expect("spec-has-guidance", "guidance/**" in freeze.SPEC, True)
    expect("coder-frozen", freeze.frozen_paths(config, "coder", ["guidance/ts.md", "src/app.py"]), ["guidance/ts.md"])
    expect("practices-frozen", freeze.frozen_paths(config, "practices", ["guidance/ts.md", "src/app.py"]), ["guidance/ts.md"])


def pass_commit():
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp, guidance=True)
        with agent_stub(Path(tmp), passing_judge("VERDICT: PASS\n")), quiet():
            outcome, target, _ = run_judge(state(root), find("practices"))
        expect("pass-verdict", (outcome, target), ("PASS", None))
        expect("pass-subject", "practices verdict: PASS" in git(root, "log", "-1", "--format=%s"), True)
        expect("pass-commit-empty", git(root, "show", "--name-only", "--format=", "HEAD"), "")
        expect("guidance-kept", (root / "guidance" / "ts.md").read_text(), "# TypeScript practices\n\n- TS-1: prefer union types over enums\n")
        expect("tree-clean", git(root, "status", "--porcelain"), "")


def role_file():
    role = Path(__file__).resolve().parent.parent / "roles" / "practices.md"
    expect("role-exists", role.is_file(), True)
    expect("role-mentions-guidance", "guidance" in role.read_text(), True)


def install_template():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "target"
        target.mkdir()
        previous = os.environ.get("GROK_HOME")
        os.environ["GROK_HOME"] = str(Path(tmp) / "grok")
        try:
            with quiet():
                install.install(target)
        finally:
            if previous is None:
                os.environ.pop("GROK_HOME", None)
            else:
                os.environ["GROK_HOME"] = previous
        installed = target / "guidance" / "ts.md"
        expect("install-creates-guidance", installed.is_file(), True)
        expect("install-guidance-matches", installed.read_text(), (ROOT / "templates" / "guidance" / "ts.md").read_text())
        installed.write_text("custom rules\n")
        with quiet():
            install.install(target)
        expect("install-no-overwrite", installed.read_text(), "custom rules\n")
        expect("gitignore-no-guidance", "guidance/" in (target / ".gitignore").read_text(), False)
        with quiet():
            install.install(target, gitignore_generated=True)
        expect("gitignore-generated-no-guidance", "guidance/" in (target / ".gitignore").read_text(), False)
        expect("install-no-csharp-guidance", (target / "guidance" / "cs.md").exists(), False)
        expect("install-no-erlang-guidance", (target / "guidance" / "er.md").exists(), False)
        expect("install-no-elixir-guidance", (target / "guidance" / "ex.md").exists(), False)
        expect("install-no-ruby-guidance", (target / "guidance" / "rb.md").exists(), False)
        (target / "src").mkdir()
        (target / "src" / "App.csproj").write_text("<Project Sdk=\"Microsoft.NET.Sdk\" />\n")
        with quiet():
            install.install(target)
        csharp = target / "guidance" / "cs.md"
        expect("install-creates-csharp-guidance", csharp.is_file(), True)
        expect("install-csharp-guidance-matches", csharp.read_text(), (ROOT / "templates" / "guidance" / "cs.md").read_text())
        (target / "src" / "box.erl").write_text("-module(box).\n")
        with quiet():
            install.install(target)
        erlang = target / "guidance" / "er.md"
        expect("install-creates-erlang-guidance", erlang.is_file(), True)
        expect("install-erlang-guidance-matches", erlang.read_text(), (ROOT / "templates" / "guidance" / "er.md").read_text())
        (target / "mix.exs").write_text("defmodule Box.MixProject do\nend\n")
        with quiet():
            install.install(target)
        elixir = target / "guidance" / "ex.md"
        expect("install-creates-elixir-guidance", elixir.is_file(), True)
        expect("install-elixir-guidance-matches", elixir.read_text(), (ROOT / "templates" / "guidance" / "ex.md").read_text())
        (target / "Gemfile").write_text("source \"https://rubygems.org\"\n")
        with quiet():
            install.install(target)
        ruby = target / "guidance" / "rb.md"
        expect("install-creates-ruby-guidance", ruby.is_file(), True)
        expect("install-ruby-guidance-matches", ruby.read_text(), (ROOT / "templates" / "guidance" / "rb.md").read_text())


def rulebook_content():
    text = (ROOT / "templates" / "guidance" / "ts.md").read_text()
    ids = set(re.findall(r"\*\*TS-(\d+)", text))
    expect("rule-count", len(ids) >= 40, True)
    expect("rule-lines", len(text.splitlines()) <= 250, True)
    for heading in ["## Language & types", "## Design & OOP", "## React", "## Next.js App Router"]:
        expect(f"heading {heading}", heading in text, True)
    for needle in ["useEffectEvent", "erasableSyntaxOnly", '"use cache"', "satisfies", "assertNever", "proxy.ts"]:
        expect(f"mentions {needle}", needle in text, True)


def csharp_rulebook_content():
    text = (ROOT / "templates" / "guidance" / "cs.md").read_text()
    expect("cs-rule-ids", re.findall(r"\*\*CS-(\d+)", text), ["1"])
    for needle in ["Arrange, Act, Assert", "exactly one action", "blank line", "never by comments"]:
        expect(f"cs mentions {needle}", needle in text, True)


def erlang_rulebook_content():
    text = (ROOT / "templates" / "guidance" / "er.md").read_text()
    ids = [int(n) for n in re.findall(r"\*\*ER-(\d+)", text)]
    expect("er-rule-count", len(ids) >= 40, True)
    expect("er-rule-contiguous", ids, list(range(1, len(ids) + 1)))
    expect("er-rule-lines", len(text.splitlines()) <= 250, True)
    for heading in ["## Errors & functional style", "## OTP & concurrency", "## Testing", "## Types & style", "## Cowboy & real-time"]:
        expect(f"er heading {heading}", heading in text, True)
    for needle in ["maybe", "gen_statem", "handle_event_function", "list_to_existing_atom", "handle_continue", "pg:", "cowboy_websocket", "simple_one_for_one"]:
        expect(f"er mentions {needle}", needle in text, True)


def ruby_rulebook_content():
    text = (ROOT / "templates" / "guidance" / "rb.md").read_text()
    ids = [int(n) for n in re.findall(r"\*\*RB-(\d+)", text)]
    expect("rb-rule-count", len(ids) >= 40, True)
    expect("rb-rule-contiguous", ids, list(range(1, len(ids) + 1)))
    expect("rb-rule-lines", len(text.splitlines()) <= 250, True)
    for heading in ["## Convention, Ruby, OOP", "## Models & controllers", "## Hotwire", "## Testing", "## Style, stack, ops"]:
        expect(f"rb heading {heading}", heading in text, True)
    for needle in ["resources :", "broadcasts_to", "strict_loading", "Solid Cable", "bin/ci", "rubocop-rails-omakase", "YJIT"]:
        expect(f"rb mentions {needle}", needle in text, True)


def elixir_rulebook_content():
    text = (ROOT / "templates" / "guidance" / "ex.md").read_text()
    ids = [int(n) for n in re.findall(r"\*\*EX-(\d+)", text)]
    expect("ex-rule-count", len(ids) >= 40, True)
    expect("ex-rule-contiguous", ids, list(range(1, len(ids) + 1)))
    expect("ex-rule-lines", len(text.splitlines()) <= 250, True)
    for heading in ["## Errors & functional style", "## OTP & concurrency", "## Testing", "## Types & style", "## Phoenix & LiveView"]:
        expect(f"ex heading {heading}", heading in text, True)
    for needle in ["with", "String.to_existing_atom", "assign_async", "~p", "stream/", "Phoenix.Presence", "unique_constraint", "Bandit"]:
        expect(f"ex mentions {needle}", needle in text, True)
    expect("ex-no-moduledoc-requirement", "@moduledoc" in text and "do not add them" in text, True)


if __name__ == "__main__":
    pipeline_order()
    guidance_files()
    no_guidance_skip()
    disabled_skip()
    freeze_guidance()
    pass_commit()
    role_file()
    install_template()
    rulebook_content()
    csharp_rulebook_content()
    erlang_rulebook_content()
    elixir_rulebook_content()
    ruby_rulebook_content()
    print("practices ok")
