import ast
import os
from pathlib import Path
from typing import Any

import pytest

from marestail import jobs
from marestail.config import Config

ROOT = Path(__file__).resolve().parent.parent
NAMES = ["MARESTAIL_JOBS", "[run] jobs"]
ACCEPTED: list[tuple[object, int]] = [(1, 1), (3, 3), ("3", 3), ("03", 3)]
REJECTED: list[object] = [0, "0", -1, "-1", True, False, 2.5, "2.5", "four", " 3", [3]]
TEMPLATE_JOBS = "# jobs = 4  # parallel workers for every mutation gate; MARESTAIL_JOBS wins over it"
TEMPLATE_NICE = "# nice = 19  # 0 leaves the process at the shell's priority; MARESTAIL_NICE overrides; children inherit"
ENV_ROW = (
    "| `MARESTAIL_JOBS` | parallel workers for every mutation gate (1 or more, capped at the CPU count; empty means unset); "
    "wins over [run] jobs and the per-language worker keys |"
)
PICK_SNIPPET = 'jobs.pick(ctx.config, "<section>", "<key>", <today\'s default>)'
README_CLAUSE = "and names that flag in the README"
PRECEDENCE_SENTENCE = (
    "The variable wins, then `[python] mutation_workers` (mutmut `--max-children`), "
    "`[elixir] muex_concurrency` (muex `--concurrency`), `[rust] mutation_jobs` (cargo-mutants `--jobs`) and "
    "`[java] mutation_threads` (PIT `-Dthreads`) for their own language, then `[run] jobs`, then each tool's default: "
    "StrykerJS takes `--concurrency`, mutant takes `--jobs`, Stryker.NET takes `--concurrency`, and the Erlang gate "
    "sizes its own worker pool with it, and a value from the variable or `[run] jobs` is capped at the CPU count."
)
PARAGRAPH_CLAUSES = [
    "`[run] jobs` or `MARESTAIL_JOBS` sets the number of parallel mutation workers",
    "0 is an error",
    "the mutant set and the verdict",
    "cargo-mutants is passed no `--jobs` at all when `--in-place` is in `[rust] mutation_args`",
    "a pom that sets `<threads>` in the pitest plugin's `<configuration>` beats any `-Dthreads`",
    "`templates/java-pitest.xml` sets none",
]


@pytest.fixture(autouse=True)
def eight_cpus(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 8)


def configured(tmp_path: Path, raw: dict[str, Any] | None = None) -> Config:
    return Config(root=tmp_path, raw=raw or {})


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(("raw", "expected"), ACCEPTED)
def test_parse_accepts_whole_numbers_one_or_more(name: str, raw: object, expected: int) -> None:
    assert jobs.parse(raw, name) == expected


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("raw", REJECTED)
def test_parse_rejects_anything_else(name: str, raw: object, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        jobs.parse(raw, name)
    assert str(raised.value) == f"{name} must be a whole number 1 or more, got {raw!r}"
    assert capsys.readouterr() == ("", "")


def test_run_jobs_is_none_when_nothing_is_set(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert jobs.run_jobs(configured(tmp_path)) is None
    assert capsys.readouterr() == ("", "")


def test_run_jobs_reads_the_key(tmp_path: Path) -> None:
    assert jobs.run_jobs(configured(tmp_path, {"run": {"jobs": 3}})) == 3


def test_run_jobs_the_variable_beats_the_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARESTAIL_JOBS", "5")
    assert jobs.run_jobs(configured(tmp_path, {"run": {"jobs": 3}})) == 5


def test_run_jobs_an_empty_variable_falls_through_to_the_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARESTAIL_JOBS", "")
    assert jobs.run_jobs(configured(tmp_path, {"run": {"jobs": 3}})) == 3


def test_run_jobs_rejects_a_zero_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARESTAIL_JOBS", "0")
    config = configured(tmp_path)
    with pytest.raises(SystemExit) as raised:
        jobs.run_jobs(config)
    assert str(raised.value) == "MARESTAIL_JOBS must be a whole number 1 or more, got '0'"


def test_run_jobs_rejects_a_zero_key(tmp_path: Path) -> None:
    config = configured(tmp_path, {"run": {"jobs": 0}})
    with pytest.raises(SystemExit) as raised:
        jobs.run_jobs(config)
    assert str(raised.value) == "[run] jobs must be a whole number 1 or more, got 0"


def test_run_jobs_validates_the_key_even_when_the_variable_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARESTAIL_JOBS", "5")
    config = configured(tmp_path, {"run": {"jobs": 0}})
    with pytest.raises(SystemExit) as raised:
        jobs.run_jobs(config)
    assert str(raised.value) == "[run] jobs must be a whole number 1 or more, got 0"


def test_run_jobs_caps_at_the_cpu_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("MARESTAIL_JOBS", "16")
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    assert jobs.run_jobs(configured(tmp_path)) == 2
    assert capsys.readouterr() == ("", "")


def test_run_jobs_caps_at_one_when_the_cpu_count_is_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARESTAIL_JOBS", "16")
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert jobs.run_jobs(configured(tmp_path)) == 1


def test_pick_the_variable_beats_a_language_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARESTAIL_JOBS", "3")
    config = configured(tmp_path, {"run": {"jobs": 3}, "python": {"mutation_workers": 8}})
    assert jobs.pick(config, "python", "mutation_workers", 4) == 3


def test_pick_a_language_key_beats_the_run_key(tmp_path: Path) -> None:
    config = configured(tmp_path, {"run": {"jobs": 3}, "python": {"mutation_workers": 8}})
    assert jobs.pick(config, "python", "mutation_workers", 4) == 8


def test_pick_the_run_key_beats_the_default(tmp_path: Path) -> None:
    config = configured(tmp_path, {"run": {"jobs": 3}, "python": {}})
    assert jobs.pick(config, "python", "mutation_workers", 4) == 3


def test_pick_the_default_when_nothing_is_set(tmp_path: Path) -> None:
    assert jobs.pick(configured(tmp_path, {"python": {}}), "python", "mutation_workers", 4) == 4


def test_pick_a_language_key_of_zero_comes_back_raw(tmp_path: Path) -> None:
    config = configured(tmp_path, {"run": {"jobs": 3}, "elixir": {"muex_concurrency": 0}})
    assert jobs.pick(config, "elixir", "muex_concurrency", 4) == 0


def test_pick_a_language_key_is_not_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    config = configured(tmp_path, {"run": {"jobs": 3}, "rust": {"mutation_jobs": 16}})
    assert jobs.pick(config, "rust", "mutation_jobs", 2) == 16


def test_pick_caps_the_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("MARESTAIL_JOBS", "16")
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    assert jobs.pick(configured(tmp_path, {"python": {}}), "python", "mutation_workers", 4) == 2
    assert capsys.readouterr() == ("", "")


def test_pick_caps_the_run_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    config = configured(tmp_path, {"run": {"jobs": 16}, "python": {}})
    assert jobs.pick(config, "python", "mutation_workers", 4) == 2
    assert capsys.readouterr() == ("", "")


def test_pick_rejects_a_bad_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARESTAIL_JOBS", "0")
    config = configured(tmp_path, {"python": {"mutation_workers": 8}})
    with pytest.raises(SystemExit) as raised:
        jobs.pick(config, "python", "mutation_workers", 4)
    assert str(raised.value) == "MARESTAIL_JOBS must be a whole number 1 or more, got '0'"


def test_pick_rejects_a_bad_run_key_without_a_language_key(tmp_path: Path) -> None:
    config = configured(tmp_path, {"run": {"jobs": 0}, "python": {}})
    with pytest.raises(SystemExit) as raised:
        jobs.pick(config, "python", "mutation_workers", 4)
    assert str(raised.value) == "[run] jobs must be a whole number 1 or more, got 0"


@pytest.mark.parametrize("language_key", [None, 8])
def test_pick_validates_the_run_key_when_the_variable_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language_key: int | None
) -> None:
    monkeypatch.setenv("MARESTAIL_JOBS", "5")
    section = {} if language_key is None else {"mutation_workers": language_key}
    config = configured(tmp_path, {"run": {"jobs": 0}, "python": section})
    with pytest.raises(SystemExit) as raised:
        jobs.pick(config, "python", "mutation_workers", 4)
    assert str(raised.value) == "[run] jobs must be a whole number 1 or more, got 0"


def test_template_lists_jobs_under_run() -> None:
    lines = (ROOT / "templates" / "marestail.toml").read_text().splitlines()
    assert lines[lines.index("# [run]") + 1] == TEMPLATE_NICE
    assert lines[lines.index(TEMPLATE_NICE) + 1] == TEMPLATE_JOBS


def test_template_comments_out_the_language_keys() -> None:
    text = (ROOT / "templates" / "marestail.toml").read_text()
    assert "# mutation_workers = 4  # wins over [run] jobs for mutmut" in text
    assert "# mutation_jobs = 2  # wins over [run] jobs for cargo-mutants" in text
    assert "# mutation_threads = 2  # wins over [run] jobs for PIT" in text


def jobs_paragraph() -> str:
    paragraphs = (ROOT / "README.md").read_text().split("\n\n")
    index = next(i for i, part in enumerate(paragraphs) if part.startswith("Mutation testing always runs on the diff"))
    return paragraphs[index + 1]


def test_readme_paragraph_states_the_rule() -> None:
    paragraph = jobs_paragraph()
    pieces = [piece.strip() for piece in paragraph.split(". ") if piece.strip()]
    closed = [piece if piece.endswith(".") else f"{piece}." for piece in pieces]
    assert PRECEDENCE_SENTENCE in closed
    assert [clause for clause in PARAGRAPH_CLAUSES if clause not in paragraph] == []


def test_readme_env_table_lists_the_variable() -> None:
    table = (ROOT / "README.md").read_text().split("## Environment variables", 1)[1].split("\n## ", 1)[0]
    assert ENV_ROW in table.splitlines()


def test_both_add_language_skills_carry_the_rule() -> None:
    first = (ROOT / ".claude" / "skills" / "add-language" / "SKILL.md").read_text()
    second = (ROOT / ".agents" / "skills" / "add-language" / "SKILL.md").read_text()
    assert first == second
    contract = first.split("## The gate contract", 1)[1].split("\n## ", 1)[0]
    bullets = [line for line in contract.splitlines() if line.startswith("- ")]
    assert any(PICK_SNIPPET in line and "jobs.run_jobs(ctx.config)" in line and README_CLAUSE in line for line in bullets)


def test_jobs_imports_only_config() -> None:
    tree = ast.parse((ROOT / "marestail" / "jobs.py").read_text())
    plain = {
        alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names if not alias.name.startswith("mutmut")
    }
    modules = {str(node.module) for node in tree.body if isinstance(node, ast.ImportFrom) if not str(node.module).startswith("mutmut")}
    assert plain == {"os", "re"}
    assert modules == {"marestail.config"}


def test_jobs_sits_with_context_and_ran_against() -> None:
    text = (ROOT / ".importlinter").read_text()
    layer = next(line for line in text.splitlines() if "marestail.context" in line and "marestail.ran_against" in line)
    assert "marestail.jobs" in [part.strip() for part in layer.split(":")]


def test_conftest_clears_the_variable() -> None:
    assert 'monkeypatch.delenv("MARESTAIL_JOBS", raising=False)' in (ROOT / "tests" / "conftest.py").read_text()
