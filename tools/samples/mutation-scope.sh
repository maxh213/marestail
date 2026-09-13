#!/usr/bin/env bash
set -euo pipefail

MARESTAIL="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
REPO="/tmp/mutation-scope-fixture"

fail() {
    echo "mutation-scope.sh: FAIL: $1" >&2
    exit 1
}

rm -rf "$REPO"
mkdir -p "$REPO"

MARESTAIL="$MARESTAIL" REPO="$REPO" python3 - <<'PYEOF'
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.environ["MARESTAIL"])

from marestail.config import Config
from marestail.context import Context
from marestail import dotnet
from marestail import erlang
from marestail.gates import cs_mutation, er_mutation, ex_mutation, py_mutation, rb_mutation, ts_mutation

FAILED = []


def check(label, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + label + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(label)


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def ctx_for(root, raw, changed=None):
    return Context(config=Config(root=root, raw=raw), scope_changed=changed is not None, changed=set(changed or ()))


RAW = {"git": {"base": "base"}, "elixir": {}, "python": {}, "ruby": {}, "ts": {}, "dotnet": {}, "erlang": {}}
SPECS = [
    ("elixir", (".ex", ".exs"), ["lib/fresh.ex"]),
    ("python", (".py",), ["fresh.py", "tests/test_x.py"]),
    ("ruby", (".rb",), ["fresh.rb"]),
    ("ts", (".ts", ".tsx"), ["src/fresh.spec.ts", "src/fresh.ts"]),
    ("dotnet", (".cs",), ["src/Fresh.cs"]),
    ("erlang", (".erl",), ["src/fresh.erl"]),
]


def build_repo(root):
    (root / ".marestail").mkdir(parents=True)
    (root / "lib").mkdir()
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "lib" / "fresh.ex").write_text("defmodule Fresh do\n  def f, do: 1\nend\n")
    (root / "lib" / "dirty.ex").write_text("defmodule Dirty do\n  def f, do: 1\nend\n")
    (root / "fresh.py").write_text("def f():\n    return 1\n")
    (root / "dirty.py").write_text("def g():\n    return 1\n")
    (root / "tests" / "test_x.py").write_text("def test_f():\n    assert True\n")
    (root / "fresh.rb").write_text("class Fresh\nend\n")
    (root / "dirty.rb").write_text("class Dirty\nend\n")
    (root / "src" / "fresh.ts").write_text("export const f = 1\n")
    (root / "src" / "fresh.spec.ts").write_text("export const s = 1\n")
    (root / "src" / "dirty.ts").write_text("export const g = 1\n")
    (root / "src" / "Fresh.cs").write_text("class Fresh {\n    void M() {}\n}\n")
    (root / "src" / "Dirty.cs").write_text("class Dirty {\n    void M() {}\n}\n")
    (root / "src" / "fresh.erl").write_text("-module(fresh).\n-export([f/0]).\nf() -> 1.\n")
    (root / "src" / "dirty.erl").write_text("-module(dirty).\n-export([f/0]).\nf() -> 1.\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "base")
    git(root, "branch", "base")
    (root / "lib" / "fresh.ex").write_text("defmodule Fresh do\n  def f, do: 2\nend\n")
    (root / "fresh.py").write_text("def f():\n    return 2\n")
    (root / "tests" / "test_x.py").write_text("def test_f():\n    assert 1 == 1\n")
    (root / "fresh.rb").write_text("class Fresh\n  def f\n    2\n  end\nend\n")
    (root / "src" / "fresh.ts").write_text("export const f = 2\n")
    (root / "src" / "fresh.spec.ts").write_text("export const s = 2\n")
    (root / "src" / "Fresh.cs").write_text("class Fresh {\n    void M() { int x = 2; }\n}\n")
    (root / "src" / "fresh.erl").write_text("-module(fresh).\n-export([f/0]).\nf() -> 2.\n")


def check_default(root):
    ctx = ctx_for(root, RAW)
    scopes = {lang: ctx.mutation_files(lang, root, suffixes) for lang, suffixes, _ in SPECS}
    for lang, _suffixes, expected in SPECS:
        scope = scopes[lang]
        check(f"{lang}: default resolves scoped to the diff", scope.mode == "scoped" and scope.files == expected, str(scope))
    ex_args = ex_mutation.command(ctx, ex_mutation.scoped_sources(ctx, root, scopes["elixir"].files))
    check("ex: --files carries the changed file", "--files" in ex_args and "lib/fresh.ex" in ex_args[ex_args.index("--files") + 1], str(ex_args))
    check("ex: --files excludes unchanged", "dirty" not in ",".join(ex_args), str(ex_args))
    patterns = py_mutation.mutant_patterns(ctx, scopes["python"].files)
    check("py: mutant patterns from changed module only, tests excluded", patterns == ["fresh.*"], str(patterns))
    subjects = rb_mutation.changed_subjects(ctx, scopes["ruby"].files)
    check("rb: subjects from changed file", subjects == ["Fresh*"], str(subjects))
    mutate = ts_mutation.changed_sources(ctx, scopes["ts"].files)
    ts_args = ts_mutation.mutation_command(mutate)
    check("ts: --mutate carries changed source, excludes spec", "--mutate" in ts_args and ts_args[ts_args.index("--mutate") + 1] == "src/fresh.ts", str(ts_args))
    targets = cs_mutation.mutation_targets(ctx, scopes["dotnet"].files)
    cs_args = cs_mutation.command(ctx, root / "App.csproj", root / "App.Tests" / "App.Tests.csproj", root / "out", targets)
    check("cs: -m targets changed source only", "-m" in cs_args and "**/src/Fresh.cs" in cs_args and not any("Dirty" in a for a in cs_args), str(cs_args))
    sources = erlang.source_files(ctx)
    mutate_er = er_mutation.mutate_files(ctx, sources, scopes["erlang"].files)
    check("er: mutate set is the changed source", [p.name for p in mutate_er] == ["fresh.erl"], str(mutate_er))


def check_all_config(root):
    raw = {**RAW, "elixir": {"mutation_scope": "all"}, "python": {"mutation_scope": "all"}, "ruby": {"mutation_scope": "all"},
           "ts": {"mutation_scope": "all"}, "dotnet": {"mutation_scope": "all"}, "erlang": {"mutation_scope": "all"}}
    ctx = ctx_for(root, raw)
    for lang, suffixes, _ in SPECS:
        scope = ctx.mutation_files(lang, root, suffixes)
        check(f"{lang}: mutation_scope=all resolves full", scope.mode == "full" and scope.files is None and not scope.note, str(scope))
    check("ex: full run has no --files", "--files" not in ex_mutation.command(ctx, []))
    check("ts: full run has no --mutate", "--mutate" not in ts_mutation.mutation_command([]))
    sources = erlang.source_files(ctx)
    check("er: full run mutates every source", len(er_mutation.mutate_files(ctx, sources, None)) == 2)


def check_cli_scoped_wins(root):
    raw = {**RAW, "python": {"mutation_scope": "all"}}
    ctx = ctx_for(root, raw, changed={"dirty.py"})
    scope = ctx.mutation_files("python", root, (".py",))
    check("CLI scope beats mutation_scope=all", scope.mode == "scoped" and scope.files == ["dirty.py"], str(scope))
    check("CLI scope ignores the working diff", py_mutation.mutant_patterns(ctx, scope.files) == ["dirty.*"])


def check_empty_diff(root):
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "changes")
    git(root, "branch", "-f", "base", "HEAD")
    ctx = ctx_for(root, RAW)
    for lang, suffixes, _ in SPECS:
        scope = ctx.mutation_files(lang, root, suffixes)
        check(f"{lang}: clean diff resolves skip", scope.mode == "skip" and scope.files == [], str(scope))
    skipped = ex_mutation.run_gate(ctx)
    check("ex: run_gate skips on empty diff", skipped.ok and skipped.summary == "skipped: no changed elixir sources", skipped.summary)
    skipped = py_mutation.run_gate(ctx)
    check("py: run_gate skips on empty diff", skipped.ok and skipped.summary == "skipped: no changed python sources", skipped.summary)
    skipped = rb_mutation.run_gate(ctx)
    check("rb: run_gate skips on empty diff", skipped.ok and skipped.summary == "skipped: no changed ruby sources", skipped.summary)
    skipped = ts_mutation.run_gate(ctx)
    check("ts: run_gate skips on empty diff", skipped.ok and skipped.summary == "skipped: no changed typescript sources", skipped.summary)
    real_projects = dotnet.projects
    dotnet.projects = lambda c: (root / "App.csproj", root / "App.Tests" / "App.Tests.csproj", None)
    (root / "App.csproj").write_text("<Project></Project>")
    try:
        skipped = cs_mutation.run_gate(ctx)
        check("cs: run_gate skips on empty diff", skipped.ok and skipped.summary == "skipped: no changed C# sources", skipped.summary)
    finally:
        dotnet.projects = real_projects
    skipped = er_mutation.run_gate(ctx)
    check("er: run_gate skips on empty diff", skipped.ok and skipped.summary == "skipped: no changed erlang sources", skipped.summary)


def check_missing_base(root):
    raw = {**RAW, "git": {"base": "origin/missing"}}
    ctx = ctx_for(root, raw)
    scope = ctx.mutation_files("elixir", root, (".ex", ".exs"))
    check("missing base resolves full with note", scope.mode == "full" and scope.note == "(no base origin/missing; full run)", str(scope))
    real_run = ts_mutation.run
    captured = {}

    def fake_run(command, cwd, **kw):
        captured["command"] = command
        report = Path(cwd) / "reports" / "mutation" / "mutation.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({"files": {}}))
        return 0, ""

    ts_mutation.run = fake_run
    try:
        result = ts_mutation.run_gate(ctx)
        check("missing base: full run without --mutate", "--mutate" not in captured.get("command", []), str(captured.get("command")))
        check("missing base: note in summary", result.ok and result.summary == "all mutants killed (no base origin/missing; full run)", result.summary)
    finally:
        ts_mutation.run = real_run


def check_invalid_value(root):
    raw = {**RAW, "elixir": {"mutation_scope": "weekly"}}
    ctx = ctx_for(root, raw)
    scope = ctx.mutation_files("elixir", root, (".ex", ".exs"))
    check("invalid mutation_scope resolves error", scope.mode == "error" and 'mutation_scope must be "changed" or "all"' in scope.note, str(scope))
    result = ex_mutation.run_gate(ctx)
    check("invalid mutation_scope fails the gate", not result.ok and "mutation_scope" in result.summary, result.summary)
    scoped_ctx = ctx_for(root, raw, changed={"lib/fresh.ex"})
    result = ex_mutation.run_gate(scoped_ctx)
    check("invalid mutation_scope errors even when CLI-scoped", not result.ok and "mutation_scope" in result.summary, result.summary)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        build_repo(root)
        check_default(root)
        check_all_config(root)
        check_cli_scoped_wins(root)
        check_missing_base(root)
        check_invalid_value(root)
        check_empty_diff(root)
    if FAILED:
        print(f"{len(FAILED)} assertions failed")
        sys.exit(1)
    print("all mutation-scope unit assertions passed")


main()
PYEOF

echo "unit checks OK"

EX_REPO="/tmp/mutation-scope-ex"
rm -rf "$EX_REPO"
mkdir -p "$EX_REPO/lib" "$EX_REPO/spybin"
cd "$EX_REPO"
git init -q
cat > marestail.toml <<'EOF'
[git]
base = "base"

[elixir]
EOF
cat > mix.exs <<'EOF'
defmodule Tiny.MixProject do
end
EOF
printf 'defmodule Fresh do\n  def f, do: 1\nend\n' > lib/fresh.ex
printf 'defmodule Dirty do\n  def f, do: 1\nend\n' > lib/dirty.ex
git add . && git commit -q -m base
git branch base
printf 'defmodule Fresh do\n  def f, do: 2\nend\n' > lib/fresh.ex

cat > spybin/mix <<'EOF'
#!/usr/bin/env bash
echo "mix $*" >> "$SPY_LOG"
if [ "${1:-}" = "help" ]; then
    exit 0
fi
echo '{"mutations": [{"status": "killed", "location": {"file": "lib/fresh.ex", "line": 2}, "mutator": "Mux", "description": "spy"}]}'
exit 0
EOF
chmod +x spybin/mix
export SPY_LOG="$EX_REPO/spy.log"

OUTPUT="$(SPY_LOG="$SPY_LOG" PATH="$EX_REPO/spybin:$PATH" python3 "$MARESTAIL/marestail/cli.py" gate --tier full --only ex.mutation 2>&1)" || fail "ex e2e gate failed: $OUTPUT"
MUEX="$(grep '^mix muex' "$SPY_LOG")"
echo "$MUEX" | grep -q -- '--files lib/fresh.ex' || fail "ex e2e: muex missing --files lib/fresh.ex: $MUEX"
echo "$MUEX" | grep -q 'dirty' && fail "ex e2e: muex saw the unchanged file: $MUEX"
echo "e2e verdict 1: default config invokes muex with --files <changed> (spy mix; a real muex run compiles the project per mutant — too slow for a fixture)"

: > "$SPY_LOG"
printf '\nmutation_scope = "all"\n' >> marestail.toml
OUTPUT="$(SPY_LOG="$SPY_LOG" PATH="$EX_REPO/spybin:$PATH" python3 "$MARESTAIL/marestail/cli.py" gate --tier full --only ex.mutation 2>&1)" || fail "ex e2e (all) failed: $OUTPUT"
MUEX="$(grep '^mix muex' "$SPY_LOG")"
echo "$MUEX" | grep -q -- '--files' && fail "ex e2e (all): unexpected --files: $MUEX"
echo "e2e verdict 2: mutation_scope=all invokes muex without --files"

git add . && git commit -q -m second
git branch -f base HEAD
printf '[git]\nbase = "base"\n\n[elixir]\n' > marestail.toml
OUTPUT="$(SPY_LOG="$SPY_LOG" PATH="$EX_REPO/spybin:$PATH" python3 "$MARESTAIL/marestail/cli.py" gate --tier full --only ex.mutation 2>&1)" || fail "ex e2e (clean) failed: $OUTPUT"
echo "$OUTPUT" | grep -q 'skipped: no changed elixir sources' || fail "ex e2e (clean): expected skip, got: $OUTPUT"
echo "e2e verdict 3: clean diff skips ex.mutation"

echo "mutation-scope.sh: OK"
