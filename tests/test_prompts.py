from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from marestail import audit, prompts, shell
from marestail.config import Config
from marestail.pipeline import Judge, Worker, find
from tests.conftest import FakeRun, make_context

COMMIT = (
    "Do not use `git add -f` or `--force`. Paths ignored by `.gitignore` stay untracked on disk; "
    "the next role still reads them. The runner drops any gitignored path you force-add."
)
REPORT = "what you did, what is left, what the next role must know. Under 40 lines, plus the audit section if one is required."
CONFIG_STEP = (
    "Do not change gate configuration, the feature files, or the QA procedure; the runner reverts such changes. "
    "If you believe one is needed, say so under `## Config change` in the handoff with the reason; a human "
    "sees it after the run. Then find a way within the current configuration."
)
CSPROJ = (
    ' The one frozen edit that is kept: adding `<PackageReference Include="..." Version="..." />` or '
    '`<InternalsVisibleTo Include="..." />` lines to a `.csproj`. Any other csproj change, including removing or '
    "changing a line, is reverted."
)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    roles = tmp_path / "roles"
    for name in ("coder", "critic", "hardener", "perf", "specifier"):
        write(roles / f"{name}.md", f"  You are the {name}.  \n")
    monkeypatch.setattr(prompts, "ROLES_DIR", roles)
    root = tmp_path / "repo"
    write(root / "features" / "t.feature", "Scenario: one\n")
    write(root / "qa" / "t.md", " procedure \n")
    write(root / "tasks" / "t.md", " Do the thing. \n")
    write(root / ".marestail" / "handoffs" / "t" / "01-specifier.md", " wrote spec \n")
    return root


def config(root: Path, raw: dict[str, Any] | None = None) -> Config:
    return make_context(root, raw).config


def report(root: Path, name: str) -> Path:
    return root / ".marestail" / "handoffs" / "t" / f"{name}.md"


def test_role_text_reads_the_role_file() -> None:
    assert prompts.role_text("coder").startswith("You are the coder.")


def test_section() -> None:
    assert prompts.section("Title", "\n body \n") == "# Title\nbody"


def test_optional_section() -> None:
    assert prompts.optional_section("T", "x") == ["# T\nx"]
    assert prompts.optional_section("T", "") == []


def test_hard_scope() -> None:
    assert prompts.hard_scope(None, "{paths}") == []
    assert prompts.hard_scope(set(), "{paths}") == []
    assert prompts.hard_scope({"b.py", "a"}, "scope {paths}.") == ["# Scope\nscope `a`, `b.py`."]


def test_worker_prompt(repo: Path) -> None:
    text = prompts.worker_prompt(
        config(repo), find("coder"), repo / "tasks" / "t.md", "t", report(repo, "03-coder"), "fix it", "lbl", " --x"
    )
    assert text.split("\n\n") == [
        "You are the coder.",
        "# Task\nDo the thing.",
        "# Specification files\nfeatures/t.feature\nqa/t.md",
        "# Handoffs so far\n## 01-specifier\nwrote spec",
        "# Finishing\n1. "
        + audit.instructions(config(repo), "t")
        + "\n2. Run `marestail gate --tier fast --x` and keep working until it prints GATE PASSED."
        + "\n3. Commit tracked changes with a message starting with `[lbl] ` and ending in `By coder.` "
        + COMMIT
        + "\n4. Write .marestail/handoffs/t/03-coder.md: "
        + REPORT
        + "\n5. "
        + CONFIG_STEP,
        "# Why the work came back to you\nfix it",
    ]


def test_worker_prompt_with_hard_scope_and_no_feedback(repo: Path) -> None:
    text = prompts.worker_prompt(config(repo), find("specifier"), repo / "tasks" / "t.md", "t", report(repo, "01"), "", hard_focus={"src"})
    parts = text.split("\n\n")
    assert parts[2] == "# Scope\n" + prompts.WORKER_SCOPE.format(paths="`src`")
    assert parts[-1] == (
        "# Finishing\n1. Commit tracked changes with a message ending in `By specifier.` "
        + COMMIT
        + "\n2. Write .marestail/handoffs/t/01.md: "
        + REPORT
        + "\n3. "
        + CONFIG_STEP
    )


@pytest.mark.parametrize(
    ("worker", "expected"),
    [
        (Worker("w", None), ["Commit"]),
        (Worker("w", "sonar"), ["Run `marestail gate --tier sonar` and", "Commit"]),
        (Worker("w", None, audit=True), ["Audit before", "Commit"]),
    ],
)
def test_finishing_steps(repo: Path, worker: Worker, expected: list[str]) -> None:
    lines = prompts.finishing(config(repo), worker, "t", report(repo, "x")).split("\n")
    heads = [line.split(". ", 1)[1] for line in lines[: len(expected)]]
    assert [head.startswith(prefix) for head, prefix in zip(heads, expected, strict=True)] == [True] * len(expected)
    assert len(lines) == len(expected) + 2
    assert lines[-1] == f"{len(lines)}. {CONFIG_STEP}"


def test_finishing_mentions_csproj_for_dotnet(repo: Path) -> None:
    text = prompts.finishing(config(repo, {"dotnet": {}}), Worker("w", None), "t", report(repo, "x"))
    assert text.endswith(CONFIG_STEP + CSPROJ)


def test_csproj_note(repo: Path) -> None:
    assert prompts.csproj_note(config(repo)) == ""
    assert prompts.csproj_note(config(repo, {"dotnet": {"x": 1}})) == CSPROJ


def test_commit_opening() -> None:
    assert prompts.commit_opening("") == ""
    assert prompts.commit_opening("m") == "starting with `[m] ` and "


def test_judge_prompt_for_critic(repo: Path) -> None:
    text = prompts.judge_prompt(config(repo), find("critic"), repo / "tasks" / "t.md", "t", report(repo, "02-critic"), "", "", "")
    assert text == "\n\n".join(
        [
            "You are the critic.",
            "# Task\nDo the thing.",
            "# Specification\n## features/t.feature\nScenario: one\n\n## qa/t.md\nprocedure",
            "# Handoffs so far\n## 01-specifier\nwrote spec",
            f"# Verdict\nWrite your verdict to {report(repo, '02-critic')} and nothing else. Do not edit any other file; "
            "the runner discards other edits.\n" + prompts.bounce_choices(find("critic")) + " Then numbered findings, each naming "
            "the file, scenario or step concerned and the fix required. Under 40 lines.",
        ]
    )


def test_judge_prompt_for_perf_with_everything(repo: Path) -> None:
    judge = find("perf")
    text = prompts.judge_prompt(config(repo), judge, repo / "tasks" / "t.md", "t", report(repo, "p"), "gates", "trees", "bad", {"a"})
    assert text.split("\n\n") == [
        "You are the perf.",
        "# Task\nDo the thing.",
        "# Scope\n" + prompts.JUDGE_SCOPE.format(paths="`a`"),
        "# Specification\nfeatures/t.feature\nqa/t.md",
        "# Gate report\ngates",
        "# Trees\ntrees",
        "# Handoffs so far\n## 01-specifier\nwrote spec",
        "# Benches frozen\n" + prompts.BENCHES_FROZEN,
        "# Verdict\n" + prompts.verdict_instructions(report(repo, "p"), judge),
        "# Why your verdict was rejected\nbad",
    ]


def test_judge_specification(repo: Path) -> None:
    assert prompts.judge_specification(config(repo), find("hardener"), "t") == "features/t.feature\nqa/t.md"


def test_verdict_instructions_for_writing_judge() -> None:
    text = prompts.verdict_instructions(Path("/r/v.md"), find("perf"))
    assert text == (
        "Write your verdict to /r/v.md and edit nothing else except files matching `perf/**`; the runner discards other edits.\n"
        "First line: `VERDICT: PASS`, or `VERDICT: BOUNCE` to send the work back to the coder. Then numbered findings, each naming "
        "the file, scenario or step concerned and the fix required. Under 40 lines."
    )


def test_allowed_edits() -> None:
    assert prompts.allowed_edits(Judge("j", None, "c")) == "nothing else. Do not edit any other file"
    assert prompts.allowed_edits(Judge("j", None, "c", writes=("a/**", "b"))) == "edit nothing else except files matching `a/**`, `b`"


def test_bounce_choices_unpinned() -> None:
    assert prompts.bounce_choices(Judge("j", None, "c")) == (
        "First line: `VERDICT: PASS`, or `VERDICT: BOUNCE` to send the work back to the usual role, or "
        "`VERDICT: BOUNCE <role>` to send it to a different one, for example `VERDICT: BOUNCE specifier` when the "
        "defect is in the feature file or the QA procedure rather than the code."
    )


def test_spec_listing_and_contents_without_files(tmp_path: Path) -> None:
    assert prompts.spec_listing(config(tmp_path), "t") == "none yet"
    assert prompts.spec_contents(config(tmp_path), "t") == "none yet"


def test_qa_files_prefer_related(repo: Path) -> None:
    write(repo / "qa" / "other.md", "x")
    assert prompts.qa_files(config(repo), "t") == [repo / "qa" / "t.md"]
    assert prompts.qa_files(config(repo), "zzz") == [repo / "qa" / "other.md", repo / "qa" / "t.md"]


def history(fake_run: Callable[..., FakeRun], log: str) -> FakeRun:
    return fake_run(shell, [(0, log)])


def test_handoffs_from_history(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = history(fake_run, "## Specify. By specifier.\n\n\n## wip\n\n\n## Code. By coder.\nbody\n\n")
    assert (
        prompts.handoffs(config(tmp_path, {"git": {"base": "origin/main"}}), "t") == "## Specify. By specifier.\n## Code. By coder.\nbody"
    )
    assert fake.calls == [["git", "log", "--reverse", "--format=## %s%n%b%n", "origin/main..HEAD"]]
    assert fake.options == [{"cwd": tmp_path}]


def test_handoffs_default_base_and_none(tmp_path: Path, fake_run: Callable[..., FakeRun]) -> None:
    fake = history(fake_run, "## wip\n")
    assert prompts.handoffs(config(tmp_path), "t") == "none"
    assert fake.calls[0][-1] == "origin/master..HEAD"


def test_perf_author_prompt(repo: Path) -> None:
    note = repo / ".marestail" / "note.md"
    text = prompts.perf_author_prompt(config(repo), repo / "tasks" / "t.md", "t", "trees", note, "again")
    assert text.split("\n\n") == [
        "You are the perf.",
        "# Task\nDo the thing.",
        "# Specification\nfeatures/t.feature\nqa/t.md",
        "# Handoffs so far\n## 01-specifier\nwrote spec",
        "# Trees\ntrees",
        "# Authoring\n" + prompts.AUTHORING.format(note=note),
        "# Earlier feedback\nagain",
    ]
    assert f"changed (or `nothing`) to {note}. Edit nothing outside `perf/`." in text


def test_perf_author_prompt_minimal(repo: Path) -> None:
    text = prompts.perf_author_prompt(config(repo), repo / "tasks" / "t.md", "t", "", repo / "n.md")
    assert [part.split("\n")[0] for part in text.split("\n\n")] == [
        "You are the perf.",
        "# Task",
        "# Specification",
        "# Handoffs so far",
        "# Authoring",
    ]
