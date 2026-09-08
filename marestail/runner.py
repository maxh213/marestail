import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from marestail import config as config_module
from marestail.cli import run_gates
from marestail.report import render
from marestail.shell import run

ROLES_DIR = Path(__file__).resolve().parent.parent / "roles"


@dataclass(frozen=True)
class Role:
    name: str
    tier: str | None
    pause_after: bool = False


PIPELINE = [
    Role("specifier", None, pause_after=True),
    Role("coder", "fast"),
    Role("cleaner", "full"),
    Role("hardener", "full"),
    Role("qa", "qa"),
]


def run_pipeline(task: Path, start: str | None, stop: str | None, auto: bool, model: str | None, retries: int) -> int:
    config = config_module.load(Path.cwd())
    roles = window(PIPELINE, start, stop)
    task_name = task.stem
    for role in roles:
        ok = run_role(config, role, task, task_name, model, retries)
        if not ok:
            print(f"pipeline stopped at {role.name}")
            return 1
        if role.pause_after and not auto and not approve(config, task_name, role):
            return 1
    print("pipeline complete")
    return 0


def window(roles: list[Role], start: str | None, stop: str | None) -> list[Role]:
    names = [role.name for role in roles]
    first = names.index(start) if start else 0
    last = names.index(stop) if stop else len(roles) - 1
    return roles[first : last + 1]


def run_role(config, role: Role, task: Path, task_name: str, model: str | None, retries: int) -> bool:
    feedback = ""
    for attempt in range(1, retries + 1):
        print(f"== {role.name} attempt {attempt}")
        invoke(config, role, task, task_name, model, feedback, attempt)
        problems = verify(config, role, task_name)
        if not problems:
            return True
        feedback = problems
        print(problems)
    return False


def invoke(config, role: Role, task: Path, task_name: str, model: str | None, feedback: str, attempt: int) -> None:
    prompt = build_prompt(config, role, task, task_name, feedback)
    command = ["claude", "-p", "--permission-mode", "bypassPermissions", "--dangerously-skip-permissions", "--output-format", "json"]
    if model:
        command += ["--model", model]
    started = time.time()
    code, output = run(command, cwd=config.root, stdin=prompt, timeout=4 * 3600)
    record(config, task_name, role, attempt, prompt, output, time.time() - started)


def build_prompt(config, role: Role, task: Path, task_name: str, feedback: str) -> str:
    parts = [
        (ROLES_DIR / f"{role.name}.md").read_text().strip(),
        "# Task\n" + task.read_text().strip(),
        "# Handoffs so far\n" + handoffs(config, task_name),
        "# Finishing\n" + finishing(role, task_name),
    ]
    if feedback:
        parts.append("# Your previous attempt did not pass\n" + feedback)
    return "\n\n".join(parts)


def handoffs(config, task_name: str) -> str:
    folder = config.work / "handoffs" / task_name
    files = sorted(folder.glob("*.md")) if folder.exists() else []
    return "\n\n".join(f"## {path.stem}\n{path.read_text().strip()}" for path in files) or "none"


def finishing(role: Role, task_name: str) -> str:
    gate = f"Run `marestail gate --tier {role.tier}` and keep working until it prints GATE PASSED." if role.tier else "No gate for this role."
    return (
        f"{gate}\n"
        f"Commit everything with a message ending in `By {role.name}.`\n"
        f"Write .marestail/handoffs/{task_name}/{role.name}.md: what you did, what is left, what the next role must know. Under 30 lines."
    )


def verify(config, role: Role, task_name: str) -> str:
    problems = []
    handoff = config.work / "handoffs" / task_name / f"{role.name}.md"
    if not handoff.exists():
        problems.append(f"missing handoff {handoff.relative_to(config.root)}")
    code, status = run(["git", "status", "--porcelain"], cwd=config.root)
    dirty = [line for line in status.splitlines() if not line.endswith((".marestail/", "handoffs/"))]
    if dirty:
        problems.append("uncommitted changes:\n" + "\n".join(dirty[:20]))
    if role.tier:
        results = run_gates(role.tier, False, None)
        if not all(result.ok for result in results):
            problems.append(render(results))
    return "\n\n".join(problems)


def record(config, task_name: str, role: Role, attempt: int, prompt: str, output: str, seconds: float) -> None:
    folder = config.work / "runs" / task_name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{role.name}-{attempt}.prompt.md").write_text(prompt)
    (folder / f"{role.name}-{attempt}.json").write_text(output)
    print(f"   {role.name} finished in {seconds / 60:.1f} min: {summary(output)}")


def summary(output: str) -> str:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return output[-200:].replace("\n", " ")
    return f"turns={data.get('num_turns')} cost=${data.get('total_cost_usd', 0):.2f} {str(data.get('result', ''))[:120]!r}"


def approve(config, task_name: str, role: Role) -> bool:
    handoff = config.work / "handoffs" / task_name / f"{role.name}.md"
    print("\n" + handoff.read_text())
    if not sys.stdin.isatty():
        print("non-interactive: continuing without approval (use --to specifier to stop here)")
        return True
    answer = input("continue to coder? [y/N] ").strip().lower()
    return answer == "y"
