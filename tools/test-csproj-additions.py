#!/usr/bin/env python3
import contextlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail import freeze, prompts
from marestail.config import Config
from marestail.pipeline import find
from marestail.runner import Run, run_worker

CSPROJ = '<Project Sdk="Microsoft.NET.Sdk">\n  <ItemGroup>\n    <PackageReference Include="xunit" Version="2.6.6" />\n  </ItemGroup>\n</Project>\n'
PACKAGE = '    <PackageReference Include="Microsoft.AspNetCore.Mvc.Testing" Version="8.0.3" />'
VISIBLE = '    <InternalsVisibleTo Include="DynamicProxyGenAssembly2" />'


def expect(name, got, wanted):
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def diff(*lines: str) -> str:
    return "diff --git a/App.csproj b/App.csproj\n--- a/App.csproj\n+++ b/App.csproj\n@@ -1,3 +1,4 @@\n" + "\n".join(lines) + "\n"


def tolerance_rules():
    expect("package-added", freeze.tolerated("App.Tests/App.Tests.csproj", diff("+" + PACKAGE)), True)
    expect("visible-added", freeze.tolerated("App.csproj", diff("+" + VISIBLE)), True)
    expect("new-itemgroup", freeze.tolerated("App.csproj", diff("+", "+  <ItemGroup>", "+" + VISIBLE, "+  </ItemGroup>")), True)
    expect(
        "version-bump",
        freeze.tolerated(
            "App.csproj",
            diff('-    <PackageReference Include="xunit" Version="2.6.6" />', '+    <PackageReference Include="xunit" Version="2.9.0" />'),
        ),
        False,
    )
    expect("removal", freeze.tolerated("App.csproj", diff('-    <PackageReference Include="coverlet.collector" Version="6.0.0" />')), False)
    expect(
        "property", freeze.tolerated("App.csproj", diff("+" + PACKAGE, "+    <TreatWarningsAsErrors>false</TreatWarningsAsErrors>")), False
    )
    expect("compile-remove", freeze.tolerated("App.csproj", diff('+    <Compile Remove="Services/Hard.cs" />')), False)
    expect(
        "extra-attribute",
        freeze.tolerated("App.csproj", diff('+    <PackageReference Include="x" Version="1" PrivateAssets="all" />')),
        False,
    )
    expect("wrappers-only", freeze.tolerated("App.csproj", diff("+  <ItemGroup>", "+  </ItemGroup>")), False)
    expect("empty-diff", freeze.tolerated("App.csproj", ""), False)
    expect("other-file", freeze.tolerated("marestail.toml", diff("+" + PACKAGE)), False)
    expect("other-config", freeze.tolerated("Cargo.toml", diff('+bar = "1"')), False)


def prompt_note():
    expect("note-dotnet", "InternalsVisibleTo" in prompts.csproj_note(Config(root=Path("."), raw={"dotnet": {}})), True)
    expect("note-other", prompts.csproj_note(Config(root=Path("."), raw={"python": {}})), "")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def new_repo(folder: Path) -> Path:
    root = folder / "repo"
    root.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@marestail"], ["config", "user.name", "t"]):
        git(root, *args)
    (root / ".gitignore").write_text(".marestail/\n")
    (root / "marestail.toml").write_text('[git]\nbase = "main"\n')
    (root / "tasks").mkdir()
    (root / "tasks" / "t.md").write_text("# Add one\n")
    (root / "App.csproj").write_text(CSPROJ)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root


def stub_agent(folder: Path, root: Path, edit: str) -> None:
    stub = folder / "agent"
    stub.write_text(
        "#!/bin/sh\ncat > /dev/null\n"
        f"cd {root}\n{edit}\ngit add -A && git commit -qm 'edit csproj. By specifier.'\n"
        f"mkdir -p .marestail/handoffs/t && echo done > .marestail/handoffs/t/01-specifier.md\n"
        'printf \'{"result": "done", "num_turns": 1, "total_cost_usd": 0}\'\n'
    )
    stub.chmod(0o755)
    os.environ["MARESTAIL_CLAUDE"] = str(stub)


def worker_keeps_additions():
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(Path(tmp))
        stub_agent(Path(tmp), root, f"sed -i 's|  </ItemGroup>|{PACKAGE}\\n  </ItemGroup>|' App.csproj")
        state = Run(
            config=Config(root=root, raw={"git": {"base": "main"}}), task=root / "tasks" / "t.md", model=None, retries=1, agent="claude"
        )
        with contextlib.redirect_stdout(io.StringIO()):
            passed = run_worker(state, find("specifier"), "")
        expect("addition-passes", passed, True)
        expect("addition-kept", "Mvc.Testing" in (root / "App.csproj").read_text(), True)
        expect("no-revert-commit", "Revert" in git(root, "log", "--format=%s"), False)


def worker_reverts_other_edits():
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(Path(tmp))
        stub_agent(Path(tmp), root, 'sed -i \'s|Version="2.6.6"|Version="2.9.0"|\' App.csproj')
        state = Run(
            config=Config(root=root, raw={"git": {"base": "main"}}), task=root / "tasks" / "t.md", model=None, retries=1, agent="claude"
        )
        with contextlib.redirect_stdout(io.StringIO()) as out:
            passed = run_worker(state, find("specifier"), "")
        expect("bump-fails", passed, False)
        expect("bump-reverted", "2.6.6" in (root / "App.csproj").read_text(), True)
        expect("bump-message", "frozen" in out.getvalue(), True)


if __name__ == "__main__":
    saved = os.environ.get("MARESTAIL_CLAUDE")
    try:
        tolerance_rules()
        prompt_note()
        worker_keeps_additions()
        worker_reverts_other_edits()
    finally:
        if saved is None:
            os.environ.pop("MARESTAIL_CLAUDE", None)
        else:
            os.environ["MARESTAIL_CLAUDE"] = saved
    print("csproj additions ok")
