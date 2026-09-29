from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

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
    write(root / ".marestail" / "handoffs" / "t" / "02-critic.md", " bounced \n")
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


def test_qa_app_note(repo: Path) -> None:
    write(repo.parent / "roles" / "qa.md", "You are QA.\n")
    bare = prompts.qa_app_note(config(repo), cast(Worker, find("qa")))
    assert bare == []
    note = prompts.qa_app_note(config(repo, {"qa": {"start": "python3 app.py"}}), cast(Worker, find("qa")))
    assert note == ["The app is started for the qa gate; its address is in MARESTAIL_APP_URL."]
    assert prompts.qa_app_note(config(repo, {"qa": {"start": "x"}}), cast(Worker, find("coder"))) == []


def test_worker_prompt_includes_qa_app_note(repo: Path) -> None:
    write(repo.parent / "roles" / "qa.md", "You are QA.\n")
    text = prompts.worker_prompt(
        config(repo, {"qa": {"start": "python3 app.py", "env": {"CMS_URL": "https://example.test/g", "LOCALE": "en-gb"}}}),
        cast(Worker, find("qa")),
        repo / "tasks" / "t.md",
        "t",
        report(repo, "09-qa"),
        "",
    )
    assert "The app is started for the qa gate; its address is in MARESTAIL_APP_URL." in text
    assert "example.test" not in text
    assert "en-gb" not in text


def test_worker_prompt(repo: Path) -> None:
    text = prompts.worker_prompt(
        config(repo), cast(Worker, find("coder")), repo / "tasks" / "t.md", "t", report(repo, "03-coder"), "fix it", "lbl", " --x"
    )
    assert text.split("\n\n") == [
        "You are the coder.",
        "# Task\nDo the thing.",
        "# Specification files\nfeatures/t.feature\nqa/t.md",
        "# Handoffs so far\n## 01-specifier\nwrote spec",
        "## 02-critic\nbounced",
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
    text = prompts.worker_prompt(
        config(repo), cast(Worker, find("specifier")), repo / "tasks" / "t.md", "t", report(repo, "01"), "", hard_focus={"src"}
    )
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


def test_worker_prompt_defaults_omit_label_and_flags(repo: Path) -> None:
    text = prompts.worker_prompt(
        config(repo), cast(Worker, find("coder")), repo / "tasks" / "t.md", "t", report(repo, "03-coder"), "fix it"
    )
    assert "XXXX" not in text
    assert "starting with" not in text
    assert "marestail gate --tier fast`" in text


def test_judge_prompt_defaults_omit_trees_and_feedback(repo: Path) -> None:
    text = prompts.judge_prompt(config(repo), cast(Judge, find("critic")), repo / "tasks" / "t.md", "t", report(repo, "02-critic"), "gate")
    assert "XXXX" not in text
    assert "# Trees" not in text
    assert "# Why your verdict was rejected" not in text


def test_finishing_without_a_label_has_no_stamp(repo: Path) -> None:
    text = prompts.finishing(config(repo), Worker("w", None), "t", report(repo, "x"))
    assert "starting with" not in text
    assert "XXXX" not in text


def test_judge_prompt_for_critic(repo: Path) -> None:
    text = prompts.judge_prompt(
        config(repo), cast(Judge, find("critic")), repo / "tasks" / "t.md", "t", report(repo, "02-critic"), "", "", ""
    )
    assert text == "\n\n".join(
        [
            "You are the critic.",
            "# Task\nDo the thing.",
            "# Specification\n## features/t.feature\nScenario: one\n\n## qa/t.md\nprocedure",
            "# Handoffs so far\n## 01-specifier\nwrote spec",
            "## 02-critic\nbounced",
            f"# Verdict\nWrite your verdict to {report(repo, '02-critic')} and nothing else. Do not edit any other file; "
            "the runner discards other edits.\n"
            + prompts.bounce_choices(cast(Judge, find("critic")))
            + " Then numbered findings, each naming "
            "the file, scenario or step concerned and the fix required. Under 40 lines.",
        ]
    )


def test_judge_prompt_for_perf_with_everything(repo: Path) -> None:
    judge = cast(Judge, find("perf"))
    text = prompts.judge_prompt(config(repo), judge, repo / "tasks" / "t.md", "t", report(repo, "p"), "gates", "trees", "bad", {"a"})
    assert text.split("\n\n") == [
        "You are the perf.",
        "# Task\nDo the thing.",
        "# Scope\n" + prompts.JUDGE_SCOPE.format(paths="`a`"),
        "# Specification\nfeatures/t.feature\nqa/t.md",
        "# Gate report\ngates",
        "# Trees\ntrees",
        "# Handoffs so far\n## 01-specifier\nwrote spec",
        "## 02-critic\nbounced",
        "# Benches frozen\n" + prompts.BENCHES_FROZEN,
        "# Verdict\n" + prompts.verdict_instructions(report(repo, "p"), judge),
        "# Why your verdict was rejected\nbad",
    ]


def test_judge_specification(repo: Path) -> None:
    assert prompts.judge_specification(config(repo), cast(Judge, find("hardener")), "t") == "features/t.feature\nqa/t.md"


def test_verdict_instructions_for_writing_judge() -> None:
    text = prompts.verdict_instructions(Path("/r/v.md"), cast(Judge, find("perf")))
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
        "## 02-critic\nbounced",
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
        "## 02-critic",
        "# Authoring",
    ]


HUNKS = (
    "Add a `## Hunks` section to your handoff: one line per hunk outside the tests, `path:start-end — why`, where why is "
    "`the fix needs it` or `boy scout: <what got better> in <the touched function>`."
)
GROW = "Do not bounce for a reason that would grow the diff beyond the fix; if you believe the fix is wrong, bounce to the specifier."
TESTS_RULE = (
    "Tests must run with a command the repository already supports, in the style its existing tests use; find that out first. "
    "If the repository has a runner, use it. If it has tests but no runner, write the same kind of script. If it has no tests "
    "at all, write dependency-free tests for the language's standard runtime and say so in your handoff. A test that loads "
    "code with vm must pass process into the sandbox, so mutation testing can switch mutants."
)
OLD_TESTS_SENTENCE = "Write the tests the repository can already run, in the style it already uses."
HYPER_ROLE_SENTENCES = {
    "specifier": "Write one scenario for the behaviour the task asks for, and regression scenarios only for behaviour the "
    "changed lines can reach.",
    "critic": "Bounce a scenario that would force a change outside the fix.",
    "coder": "Change as few lines as the fix needs. Prefer a small, well-named function over a longer inline condition. "
    + TESTS_RULE
    + " Write as many as you need. "
    + HUNKS,
    "architect": "Apply the boy scout rule to the code this change touches, and only that code. If the function the fix "
    "lands in is long, split it. If the changed condition is hard to read, give it a name. Do not reshape, move or rename "
    "anything the change does not touch. Leave the dependency contracts as they are unless the change itself adds a "
    "dependency. " + HUNKS,
    "hardener": "Judge the changed lines and their tests. Do not ask for clean-up, renames, or coverage of lines that did not change. "
    + GROW
    + " "
    + TESTS_RULE
    + " Rule on every changed line the gate reports as not provable by mutation.",
}
HYPER_ALL = (
    "This run is hyper-scoped. Make the smallest change that does what the task asks. Leave the code you touch a little "
    "better than you found it. Leave code the change does not touch exactly as it is, including code you would like to "
    "improve. The gates measure only the lines that change."
)


def add_role(role: str) -> None:
    write(prompts.ROLES_DIR / f"{role}.md", f"You are the {role}.\n")


def hyper_prompt(repo: Path, role: str) -> str:
    add_role(role)
    step = find(role, "hyper")
    task = repo / "tasks" / "t.md"
    if isinstance(step, Judge):
        return prompts.judge_prompt(config(repo), step, task, "t", report(repo, "05"), "", hyper=True)
    return prompts.worker_prompt(config(repo), step, task, "t", report(repo, "03"), "", "", " --scope hyper", hyper=True)


@pytest.mark.parametrize("role", ["specifier", "critic", "coder", "architect", "blast", "hardener", "qa"])
def test_hyper_prompt_carries_its_scope_section(repo: Path, role: str) -> None:
    text = hyper_prompt(repo, role)
    scope = next(part for part in text.split("\n\n") if part.startswith("# Scope\n"))
    own = HYPER_ROLE_SENTENCES.get(role)
    assert scope == "# Scope\n" + " ".join(filter(None, [HYPER_ALL, own]))
    assert [sentence in text for name, sentence in HYPER_ROLE_SENTENCES.items() if name != role] == [False] * (4 + (own is None))
    assert "This run has a hard scope" not in text
    assert OLD_TESTS_SENTENCE not in text


@pytest.mark.parametrize("role", ["coder", "architect"])
def test_hyper_coder_and_architect_run_the_full_tier(repo: Path, role: str) -> None:
    text = hyper_prompt(repo, role)
    assert "Run `marestail gate --tier full --scope hyper` and keep working until it prints GATE PASSED." in text
    assert "--tier fast" not in text
    assert "--tier sonar" not in text


def test_hyper_architect_keeps_to_the_code_the_change_touches(repo: Path) -> None:
    text = hyper_prompt(repo, "architect")
    assert "Apply the boy scout rule to the code this change touches, and only that code." in text
    assert "Do not reshape, move or rename anything the change does not touch." in text


def test_scope_section(repo: Path) -> None:
    assert prompts.scope_section("coder", {"src"}, "{paths}", True) == ["# Scope\n" + prompts.hyper_text("coder")]
    assert prompts.scope_section("coder", {"src"}, "in {paths}", False) == ["# Scope\nin `src`"]
    assert prompts.scope_section("coder", None, "{paths}", False) == []


def test_hyper_text() -> None:
    assert prompts.hyper_text("qa") == HYPER_ALL
    assert prompts.hyper_text("critic") == HYPER_ALL + " Bounce a scenario that would force a change outside the fix."


@pytest.mark.parametrize(("role", "tier"), [("coder", "fast"), ("architect", "sonar")])
def test_prompts_outside_hyper_keep_the_hard_scope(repo: Path, role: str, tier: str) -> None:
    add_role(role)
    text = prompts.worker_prompt(
        config(repo), cast(Worker, find(role)), repo / "tasks" / "t.md", "t", report(repo, "03"), "", "", " --scope hard", {"src"}
    )
    assert f"Run `marestail gate --tier {tier} --scope hard`" in text
    assert "This run is hyper-scoped" not in text
    assert "# Scope\n" + prompts.WORKER_SCOPE.format(paths="`src`") in text


@pytest.mark.parametrize(
    ("role", "hunks", "grow"), [("coder", True, False), ("architect", True, False), ("hardener", False, True), ("blast", False, False)]
)
def test_hyper_prompt_asks_for_hunks_and_forbids_growing_the_diff(repo: Path, role: str, hunks: bool, grow: bool) -> None:
    text = hyper_prompt(repo, role)
    assert (HUNKS in text, GROW in text) == (hunks, grow)


def test_judge_prompt_shows_the_review_sections_with_none_for_empty_ones(repo: Path) -> None:
    add_role("blast")
    review = {"Diff stat": " src.py | 2 +", "Diff": "", "Hunks": "## 01-coder\n- src.py:1-2 — why"}
    text = prompts.judge_prompt(
        config(repo), cast(Judge, find("blast", "hyper")), repo / "tasks" / "t.md", "t", report(repo, "05"), "", review=review
    )
    assert "\n\n# Diff stat\nsrc.py | 2 +\n\n# Diff\nnone\n\n# Hunks\n## 01-coder\n- src.py:1-2 — why\n\n" in text
    assert "# Gate report" not in text


def test_judge_prompt_without_a_review_has_no_diff_section(repo: Path) -> None:
    add_role("hardener")
    text = prompts.judge_prompt(config(repo), cast(Judge, find("hardener")), repo / "tasks" / "t.md", "t", report(repo, "05"), "")
    assert "# Diff" not in text
