#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if command -v ruby >/dev/null 2>&1 && command -v bundle >/dev/null 2>&1; then
    echo "note: ruby toolchain present; fixture still runs unit-level assertions (rspec/simplecov integration is not exercised)"
else
    echo "skip: ruby/bundler unavailable on this machine; running unit-level scope assertions on fabricated SimpleCov data"
fi

python3 - "$ROOT" <<'PY'
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, sys.argv[1])

from marestail.changes import changed_files, changed_lines
from marestail.config import Config
from marestail.context import Context
from marestail.gates import rb_crap, rb_deps, rb_lint, rb_mutation, rb_tests

FAILED = []


def check(name, condition, detail=""):
    if condition:
        print(f"ok   {name}")
    else:
        FAILED.append(name)
        print(f"FAIL {name} {detail}")


def git(root, *args):
    subprocess.run(
        ["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.com", *args],
        cwd=root, check=True, capture_output=True, text=True,
    )


def make_ctx(root, changed=(), lines_map=None, focus=(), scoped=True):
    config = Config(root=root, raw={})
    return Context(
        config=config,
        scope_changed=scoped,
        changed=set(changed),
        focus=set(focus),
        changed_lines_map={path: set(lines) for path, lines in (lines_map or {}).items()},
    )


BASE_DIRTY = """class Dirty
  def touched(x)
    if x > 1
      branch_taken
    else
      old_call
    end
    helper
  end

  def untouched(x)
    if x
      old_covered
    else
      old_missed
    end
    old_uncovered_line
  end
end
"""

NEW_DIRTY = """class Dirty
  def touched(x)
    if x > 1
      branch_taken
    else
      branch_missed
    end
    helper
  end

  def hunk_line
    uncovered_new_line
  end

  def untouched(x)
    if x
      old_covered
    else
      old_missed
    end
    old_uncovered_line
  end
end
"""

OLD_RB = """class Old
  def legacy
    uncovered_pre_existing
  end
end
"""

FOCUSED_RB = """class Focused
  def focused_method
    uncovered_call
  end
end
"""

BASE_MOVED = """class Moved
  def staying
    1
  end
end
"""

NEW_MOVED = """class Moved
  def staying
    1
  end

  def arrived(x)
    if x
      1
    else
      2
    end
  end
end
"""


def build_repo(root):
    (root / "app/models").mkdir(parents=True)
    (root / "app/services").mkdir(parents=True)
    (root / "lib").mkdir(parents=True)
    (root / "coverage").mkdir()
    (root / "app/models/dirty.rb").write_text(BASE_DIRTY)
    (root / "app/models/old.rb").write_text(OLD_RB)
    (root / "app/services/focused.rb").write_text(FOCUSED_RB)
    (root / "lib/moved.rb").write_text(BASE_MOVED)
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "base")
    (root / "app/models/dirty.rb").write_text(NEW_DIRTY)
    (root / "lib/moved.rb").write_text(NEW_MOVED)


def write_resultset(root):
    dirty_lines = [None, 1, 1, 1, None, 0, None, 1, None, None, 1, 0, None, None, 1, 1, 1, None, 0, None, 0, None, None]
    resultset = {
        "RSpec": {
            "coverage": {
                str(root / "app/models/dirty.rb"): {
                    "lines": dirty_lines,
                    "branches": {
                        "[:if, 0, 3, 4, 7, 7]": {"[:then, 1, 4, 6, 4, 16]": 1, "[:else, 2, 6, 6, 6, 18]": 0},
                        "[:if, 3, 16, 4, 20, 7]": {"[:then, 4, 17, 6, 17, 17]": 1, "[:else, 5, 19, 6, 19, 18]": 0},
                    },
                },
                str(root / "app/models/old.rb"): {"lines": [1, 0, None]},
                str(root / "app/services/focused.rb"): {"lines": [1, 0, 1, 0]},
            }
        }
    }
    (root / "coverage/.resultset.json").write_text(json.dumps(resultset))
    return root / "coverage/.resultset.json"


TRICKY = """class T
  def a(x)
    return x if x > 1
    [1, 2].each do |n|
      puts n
    end
    x
  end

  def b; 42; end

  def c
    while_ready = false
    1
  end

  def d(list)
    while list.any? do
      list.pop
    end
  end
end
"""


def check_method_ranges():
    ends = rb_crap.method_ranges(TRICKY, [2, 10, 12, 17])
    check("crap ranges: modifier-if/do-block method", ends.get(2) == 8, str(ends))
    check("crap ranges: one-line def", ends.get(10) == 10, str(ends))
    check("crap ranges: identifier starting with keyword", ends.get(12) == 15, str(ends))
    check("crap ranges: while...do counts once", ends.get(17) == 21, str(ends))
    check("crap delta: string masked", rb_crap.line_delta('    puts "end"') == 0)
    check("crap delta: comment masked", rb_crap.line_delta("    # end") == 0)
    check("crap delta: embdoc marker", rb_crap.line_delta("=end") == 0)


def scanner_functions(root):
    return [
        {"file": str(root / "app/models/dirty.rb"), "line": 2, "name": "touched", "complexity": 2},
        {"file": str(root / "app/models/dirty.rb"), "line": 11, "name": "hunk_line", "complexity": 1},
        {"file": str(root / "app/models/dirty.rb"), "line": 15, "name": "untouched", "complexity": 2},
        {"file": str(root / "app/models/old.rb"), "line": 2, "name": "legacy", "complexity": 5},
        {"file": str(root / "app/services/focused.rb"), "line": 2, "name": "focused_method", "complexity": 1},
        {"file": str(root / "lib/moved.rb"), "line": 2, "name": "staying", "complexity": 1},
        {"file": str(root / "lib/moved.rb"), "line": 7, "name": "arrived", "complexity": 2},
    ]


def names(functions):
    return sorted(fn["name"] for fn in functions)


def check_crap(root, lines_map):
    functions = scanner_functions(root)
    unscoped = make_ctx(root, scoped=False)
    check("crap unscoped keeps every method", names(rb_crap.functions_in_scope(functions, unscoped)) == names(functions))
    scoped = make_ctx(root, changed={"app/models/dirty.rb", "lib/moved.rb"}, lines_map=lines_map)
    kept = names(rb_crap.functions_in_scope(functions, scoped))
    check("crap scoped gates hunk-intersecting methods", kept == ["arrived", "hunk_line", "touched"], kept)
    focused = make_ctx(root, focus={"app/services"})
    kept = names(rb_crap.functions_in_scope(functions, focused))
    check("crap focus gates the whole focused file", kept == ["focused_method"], kept)


def check_tests(root, lines_map):
    resultset = write_resultset(root)
    unscoped = make_ctx(root, scoped=False)
    coverage = rb_tests.load_resultset(resultset, unscoped)
    expected = [
        "app/models/dirty.rb:6 not covered",
        "app/models/dirty.rb:12 not covered",
        "app/models/dirty.rb:19 not covered",
        "app/models/dirty.rb:21 not covered",
        "app/models/dirty.rb branch [:else, 2, 6, 6, 6, 18] not taken",
        "app/models/dirty.rb branch [:else, 5, 19, 6, 19, 18] not taken",
        "app/models/old.rb:2 not covered",
        "app/services/focused.rb:2 not covered",
        "app/services/focused.rb:4 not covered",
    ]
    check("tests unscoped reports every gap", rb_tests.coverage_findings(coverage, unscoped) == expected, str(rb_tests.coverage_findings(coverage, unscoped)))
    check("tests unscoped artifact has no scoped keys", "branch_lines" not in coverage["files"]["app/models/dirty.rb"])
    check("tests unscoped global percent", abs(coverage["totals"]["percent_covered"] - 1100 / 18) < 0.01, str(coverage["totals"]))
    scoped = make_ctx(root, changed={"app/models/dirty.rb", "lib/moved.rb"}, lines_map=lines_map)
    coverage = rb_tests.load_resultset(resultset, scoped)
    findings = rb_tests.coverage_findings(coverage, scoped)
    expected_scoped = [
        "app/models/dirty.rb:6 not covered",
        "app/models/dirty.rb:12 not covered",
        "app/models/dirty.rb branch [:else, 2, 6, 6, 6, 18] not taken",
    ]
    check("tests scoped keeps only changed-line gaps", findings == expected_scoped, str(findings))
    check("tests scoped artifact carries branch lines", "branch_lines" in coverage["files"]["app/models/dirty.rb"])
    check("tests scoped percent over gated lines", abs(rb_tests.percent_covered(coverage, scoped) - 100 / 3) < 0.01, str(rb_tests.percent_covered(coverage, scoped)))
    clean = make_ctx(root, changed={"app/models/dirty.rb"}, lines_map={"app/models/dirty.rb": {4, 8}})
    check("tests scoped passes with covered changed lines despite legacy gaps", rb_tests.coverage_findings(rb_tests.load_resultset(resultset, clean), clean) == [])
    check("tests clean-scope percent is 100", rb_tests.percent_covered(rb_tests.load_resultset(resultset, clean), clean) == 100.0)
    condition = make_ctx(root, changed={"app/models/dirty.rb"}, lines_map={"app/models/dirty.rb": {3}})
    findings = rb_tests.coverage_findings(rb_tests.load_resultset(resultset, condition), condition)
    check("tests changed condition line gates its arms", findings == ["app/models/dirty.rb branch [:else, 2, 6, 6, 6, 18] not taken"], str(findings))
    untracked = make_ctx(root, changed={"app/models/dirty.rb"}, lines_map={"app/models/dirty.rb": set(range(1, 24))})
    findings = rb_tests.coverage_findings(rb_tests.load_resultset(resultset, untracked), untracked)
    check("tests untracked file is gated everywhere", findings == expected[:6], str(findings))
    focused = make_ctx(root, focus={"app/services"})
    findings = rb_tests.coverage_findings(rb_tests.load_resultset(resultset, focused), focused)
    check("tests focus gates the whole file", findings == ["app/services/focused.rb:2 not covered", "app/services/focused.rb:4 not covered"], str(findings))


def check_lint(root, lines_map):
    report = {
        "files": [
            {"path": str(root / "app/models/dirty.rb"), "offenses": [
                {"location": {"line": 6}, "cop_name": "Style/Foo", "message": "bad"},
                {"location": {"line": 12}, "cop_name": "Style/Bar", "message": "worse"},
            ]},
            {"path": str(root / "app/models/old.rb"), "offenses": [
                {"location": {"line": 3}, "cop_name": "Style/Old", "message": "legacy"},
            ]},
            {"path": str(root / "app/services/focused.rb"), "offenses": [
                {"location": {"line": 3}, "cop_name": "Style/Focus", "message": "focused bad"},
            ]},
        ]
    }
    output = json.dumps(report)
    unscoped = make_ctx(root, scoped=False)
    check("lint unscoped keeps all offenses", len(rb_lint.parse(output, unscoped)) == 4)
    scoped = make_ctx(root, changed={"app/models/dirty.rb", "lib/moved.rb"}, lines_map=lines_map)
    findings = rb_lint.parse(output, scoped)
    check("lint scoped keeps in-scope files only", findings == [
        "app/models/dirty.rb:6 Style/Foo: bad",
        "app/models/dirty.rb:12 Style/Bar: worse",
    ], str(findings))
    focused = make_ctx(root, focus={"app/services"})
    check("lint focus includes focused file", rb_lint.parse(output, focused) == ["app/services/focused.rb:3 Style/Focus: focused bad"])


def check_deps(root, lines_map):
    layers = [
        {"from": "app/models", "forbid": ["app/controllers"]},
        {"from": "app/services", "forbid": ["app/controllers"]},
    ]
    edges = [
        {"from": str(root / "app/models/dirty.rb"), "to": str(root / "app/controllers/thing.rb"), "constant": "Thing", "line": 3},
        {"from": str(root / "app/models/old.rb"), "to": str(root / "app/controllers/other.rb"), "constant": "Other", "line": 5},
        {"from": str(root / "app/services/focused.rb"), "to": str(root / "app/controllers/focus.rb"), "constant": "Focus", "line": 2},
    ]
    unscoped = make_ctx(root, scoped=False)
    check("deps unscoped reports all breaks", len(rb_deps.violations(edges, layers, unscoped)) == 3)
    scoped = make_ctx(root, changed={"app/models/dirty.rb", "lib/moved.rb"}, lines_map=lines_map)
    findings = rb_deps.violations(edges, layers, scoped)
    check("deps scoped keeps changed sources only", len(findings) == 1 and findings[0].startswith("app/models/dirty.rb:3"), str(findings))
    focused = make_ctx(root, focus={"app/services"})
    findings = rb_deps.violations(edges, layers, focused)
    check("deps focus includes focused source", len(findings) == 1 and findings[0].startswith("app/services/focused.rb:2"), str(findings))


def check_mutation(root, lines_map):
    unscoped = make_ctx(root, scoped=False)
    check("mutation unscoped mutates everything", rb_mutation.changed_subjects(unscoped) == [])
    scoped = make_ctx(root, changed={"app/models/dirty.rb", "lib/moved.rb"}, lines_map=lines_map)
    check("mutation scoped subjects from changed files", rb_mutation.changed_subjects(scoped) == ["Dirty*", "Moved*"])
    focused = make_ctx(root, focus={"app/services"})
    check("mutation focus adds focused subjects", rb_mutation.changed_subjects(focused) == ["Focused*"])


def main():
    check_method_ranges()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        build_repo(root)
        lines_map = changed_lines(root, "HEAD")
        dirty = lines_map.get("app/models/dirty.rb", set())
        check("git hunks: edited line tracked", 6 in dirty, str(sorted(dirty)))
        check("git hunks: inserted method tracked", {11, 12} <= dirty, str(sorted(dirty)))
        check("git hunks: untouched lines excluded", not dirty & {19, 21}, str(sorted(dirty)))
        check("git changed set", changed_files(root, "HEAD") == {"app/models/dirty.rb", "lib/moved.rb"}, str(changed_files(root, "HEAD")))
        check_tests(root, lines_map)
        check_crap(root, lines_map)
        check_lint(root, lines_map)
        check_deps(root, lines_map)
        check_mutation(root, lines_map)
    if FAILED:
        print(f"{len(FAILED)} assertions failed")
        sys.exit(1)
    print("all rb scope assertions passed")


main()
PY

echo "scope-rb OK"
