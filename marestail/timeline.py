import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marestail.report import Result
from marestail.shell import ensure_dir

JSON_NAME = "timeline.json"
MD_NAME = "timeline.md"
STEP_KEYS = ("id", "role", "attempt", "started_at", "ended_at", "gate", "waits", "agent", "verdict", "commits", "files", "done")


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def gate_entries(results: list[Result]) -> list[dict[str, Any]]:
    return [{"name": result.gate, "seconds": result.seconds, "ok": result.ok} for result in results]


def first_paragraph(text: str) -> str:
    return text.strip().split("\n\n", 1)[0].strip()


def done_line(handoff: Path, commits: list[dict[str, str]], agent: dict[str, Any] | None) -> str:
    paragraph = handoff_paragraph(handoff)
    if paragraph:
        return paragraph
    return commit_or_summary(commits, agent)


def handoff_paragraph(handoff: Path) -> str:
    if not handoff.exists():
        return ""
    return first_paragraph(handoff.read_text())


def commit_or_summary(commits: list[dict[str, str]], agent: dict[str, Any] | None) -> str:
    if commits:
        return commits[0]["subject"]
    if agent is not None:
        return str(agent.get("summary", ""))
    return ""


def verdict_text(verdict: str, target: str | None) -> str:
    return verdict if not target else f"{verdict} {target}"


def ordered_step(raw: dict[str, Any]) -> dict[str, Any]:
    return {key: raw[key] for key in STEP_KEYS if key in raw}


def build_step(
    report_id: str,
    role: str,
    attempt: int,
    started_at: str,
    ended_at: str,
    gate: list[dict[str, Any]],
    waits: list[dict[str, Any]],
    agent: dict[str, Any] | None,
    verdict: str | None,
    commits: list[dict[str, str]],
    files: list[str],
    done: str,
) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": report_id,
        "role": role,
        "attempt": attempt,
        "started_at": started_at,
        "ended_at": ended_at,
        "gate": gate,
        "waits": waits,
        "commits": commits,
        "files": files,
        "done": done,
    }
    if agent is not None:
        raw["agent"] = agent
    if verdict is not None:
        raw["verdict"] = verdict
    return ordered_step(raw)


def load_document(folder: Path, task: str) -> dict[str, Any]:
    path = folder / JSON_NAME
    if not path.exists():
        return {"task": task, "steps": []}
    loaded: dict[str, Any] = json.loads(path.read_text())
    return loaded


def render_section(step: dict[str, Any]) -> str:
    lines = [f"## {step['id']} (attempt {step['attempt']})", ""]
    for key in STEP_KEYS:
        if key in ("id", "attempt") or key not in step:
            continue
        lines.append(f"- {key}: {format_value(step[key])}")
    lines.append("")
    return "\n".join(lines)


def format_value(value: Any) -> str:
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return " ".join(str(value).split())


def render_markdown(steps: list[dict[str, Any]]) -> str:
    return "\n".join(render_section(step) for step in steps)


def write_files(folder: Path, task: str, steps: list[dict[str, Any]]) -> None:
    ensure_dir(folder)
    document = {"task": task, "steps": steps}
    (folder / JSON_NAME).write_text(json.dumps(document, indent=2) + "\n")
    (folder / MD_NAME).write_text(render_markdown(steps))


def append_step(folder: Path, task: str, step: dict[str, Any]) -> None:
    document = load_document(folder, task)
    document["steps"].append(step)
    write_files(folder, task, document["steps"])
