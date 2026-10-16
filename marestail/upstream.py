import contextlib
import fcntl
import json
import os
import re
import time
from pathlib import Path

from marestail import publish
from marestail.config import load
from marestail.publish import PROTECTED, real_git

TASK_CAP = 20
COMMIT_CAP = 40
STAT_CAP = 200
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def sync(root: Path, *, git=real_git, ask=None, busy=None, push=None, running=None) -> int:
    root = Path(root).resolve()
    branch = branch_name(root, git)
    if branch is None:
        say("detached HEAD; skipping")
        return 0
    if branch in PROTECTED:
        return 0
    ask = ask or ask_model
    busy = busy or pipeline_busy
    running = running or running_task_paths
    push = push or (lambda current: publish.push_current(current, git=git))
    with held(root) as locked:
        if not locked:
            say("locked; skipping")
            return 0
        return locked_sync(root, branch, git, ask, busy, push, running)


def claim(root: Path, *, busy=None) -> list[str]:
    root = Path(root).resolve()
    busy = busy or pipeline_busy
    with held(root) as locked:
        if not locked or busy(root):
            return []
        path = queue_path(root)
        if not path.is_file():
            return []
        tasks = [line.strip() for line in path.read_text().splitlines() if line.strip()]
        path.unlink()
        return [task for task in tasks if safe_task(root, task)[0] is not None and (root / task).is_file()]


def locked_sync(root: Path, branch: str, git, ask, busy, push, running) -> int:
    if busy(root):
        mark_due(root, "busy")
        say("pipeline busy; deferred")
        return 0
    if dirty(root, git):
        mark_due(root, "dirty")
        say("dirty tree; deferred")
        return 0
    config = load(root)
    base = str(config.get("git", "base", "origin/master"))
    if base == "HEAD":
        clear_due(root)
        say("base is HEAD; skipping")
        return 0
    ref = fetch_base(root, git, base)
    if ref is None:
        mark_due(root, "fetch")
        return 0
    commits = commit_list(root, git, ref)
    if not commits:
        clear_due(root)
        clear_blocked(root)
        return 0
    base_sha = rev(root, git, ref)
    if base_sha is None:
        mark_due(root, "base")
        return 0
    before = rev(root, git, "HEAD")
    if before is None:
        return 1
    failure = ""
    if classified_sha(root) != base_sha:
        try:
            plan = parse_plan(ask(prompt_for(root, git, branch, ref, commits)))
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
            failure = str(error)
            say(f"classifier failed ({failure}); merging and queueing a review")
            plan = None
        if busy(root):
            mark_due(root, "busy-during-classify")
            say("pipeline started during classify; leaving the tree alone")
            return 0
        discard(root, git, branch, before)
        if rev(root, git, "HEAD") != before or dirty(root, git):
            mark_due(root, "discard")
            say("could not discard classifier edits; deferred")
            return 0
    else:
        plan = {"tasks": [], "obsolete": []}
        say(f"already classified {base_sha[:12]}")
    merged = integrate(root, git, ref)
    if merged == "stuck":
        say("merge left the tree conflicted; not continuing")
        return 1
    if merged == "failed":
        mark_due(root, "merge")
        return 0
    created = queue_tasks(root, git, branch, ref, commits, plan, merged == "ok", running, failure)
    remember(root, base_sha, merged == "conflict")
    if merged == "ok":
        clear_due(root)
    if rev(root, git, "HEAD") == before:
        return 0
    code = push(root)
    if code != 0:
        say(f"push failed ({code})")
    return code


def branch_name(root: Path, git) -> str | None:
    code, output = git(["git", "branch", "--show-current"], root)
    branch = output.strip()
    if code != 0 or not branch:
        return None
    if not publish.safe_branch(branch):
        raise SystemExit(f"refusing unsafe branch name {branch!r}")
    return branch


def fetch_base(root: Path, git, base: str) -> str | None:
    if base.startswith("origin/"):
        remote = base[len("origin/"):]
        if not publish.safe_branch(remote):
            raise SystemExit(f"refusing unsafe base {base!r}")
        code, output = git(["git", "fetch", "--no-tags", "origin", remote], root)
        if code != 0:
            say(git_failure(output, f"fetch {base} failed"))
            return None
        return base
    if not publish.safe_branch(base):
        raise SystemExit(f"refusing unsafe base {base!r}")
    return base


def integrate(root: Path, git, ref: str) -> str:
    code, output = git(["git", "merge", "--no-edit", ref], root)
    if code == 0 and not unmerged(root, git):
        say(f"merged {ref}")
        return "ok"
    if not merging(root, git) and not unmerged(root, git):
        say((output.strip() or f"merge {ref} failed").splitlines()[-1])
        return "failed"
    if not abort_merge(root, git):
        return "stuck"
    say(f"merge of {ref} conflicted; aborted")
    return "conflict"


def abort_merge(root: Path, git) -> bool:
    if merging(root, git) or unmerged(root, git):
        git(["git", "merge", "--abort"], root)
    if merging(root, git) or unmerged(root, git):
        git(["git", "reset", "--hard", "-q"], root)
    return not merging(root, git) and not unmerged(root, git)


def queue_tasks(root: Path, git, branch: str, ref: str, commits: list[tuple[str, str]], plan: dict | None, merged: bool, running, failure: str) -> list[str]:
    created: list[str] = []
    if not merged:
        path = write_task(root, "merge-default-branch", f"merge {ref} into {branch}", merge_body(branch, ref, commits))
        if path is not None:
            created.append(path)
    moved: list[str] = []
    if plan is None:
        path = write_task(root, "review-upstream", f"review commits from {ref}", review_body(branch, ref, commits, merged, failure))
        if path is not None:
            created.append(path)
    else:
        for item in plan.get("tasks", [])[:TASK_CAP]:
            slug = clean_slug(str(item.get("slug", "")))
            title = clean_line(str(item.get("title", ""))) or "sync upstream commits"
            why = clean_text(str(item.get("why", ""))) or "This branch had not covered that upstream work."
            what = clean_text(str(item.get("what", ""))) or "Account for the behaviour those commits added."
            cited = [str(sha) for sha in item.get("commits", []) if isinstance(sha, str)]
            path = write_task(root, slug, title, feature_body(branch, ref, title, why, what, cited or [sha for sha, _ in commits[:COMMIT_CAP]], merged))
            if path is not None:
                created.append(path)
        moved = move_obsolete(root, plan.get("obsolete", []), running(root))
    commit_paths(root, git, [*created, *moved])
    if created:
        write_queue(root, created)
        say("queued " + ", ".join(created))
    return created


def write_task(root: Path, slug: str, title: str, body: str) -> str | None:
    tasks = root / "tasks"
    tasks.mkdir(exist_ok=True)
    if slug in taken_slugs(root):
        return None
    number = next_number(root)
    width = max(3, len(str(number)))
    label = f"{number:0{width}d} — {title}"
    first, _, rest = body.partition("\n")
    body = f"# {label}\n{rest}" if first.startswith("# ") else f"# {label}\n\n{body}"
    relative = f"tasks/{number:0{width}d}-{slug}.md"
    (root / relative).write_text(body if body.endswith("\n") else body + "\n")
    return relative


def feature_body(branch: str, ref: str, title: str, why: str, what: str, commits: list[str], merged: bool) -> str:
    if merged:
        state = f"The commits below are already in this branch's history (merged from `{ref}`). Do not reimplement them. Account for the behaviour they added and leave the merged code in place."
    else:
        state = f"Those commits are on `{ref}` and not yet in `{branch}` because the merge conflicted. The merge task in front of this one brings them in. After it lands, account for the behaviour; do not reimplement it and do not drop the upstream commits."
    listed = "\n".join(f"- `{sha}`" for sha in commits[:COMMIT_CAP]) or "- (see the merge)"
    return f"""# {title}

{why}

{state} Never push this branch to main or master.

## What changes

{what}

Commits:

{listed}

## What must not change

- Publish this branch only to a remote branch of the same name, never to main or master.
- Upstream commits that this task does not mention stay in history. Do not revert them to make the diff smaller.

## Done when

- The behaviour from those commits is present on `{branch}` and covered by the gates.
- No push from this branch targets main or master.
"""


def merge_body(branch: str, ref: str, commits: list[tuple[str, str]]) -> str:
    subjects = "\n".join(f"- `{sha[:12]}` {subject}" for sha, subject in commits[:COMMIT_CAP])
    extra = f"\n- and {len(commits) - COMMIT_CAP} more" if len(commits) > COMMIT_CAP else ""
    return f"""# merge {ref} into {branch}

`{ref}` has commits `{branch}` does not, and merging them hit conflicts. Bring every one of them in, including commits that have no feature task, so a later review stays a simple diff.

## What changes

- Merge `{ref}` into `{branch}` and resolve the conflicts.
- Do not drop upstream commits to make the diff smaller.
- Never push this branch to main or master.

Commits:

{subjects}{extra}

## Done when

- `{ref}` is an ancestor of HEAD.
- No conflict markers remain, and this branch still does not push to main or master.
"""


def review_body(branch: str, ref: str, commits: list[tuple[str, str]], merged: bool, failure: str) -> str:
    subjects = "\n".join(f"- `{sha[:12]}` {subject}" for sha, subject in commits[:COMMIT_CAP])
    where = f"They are already merged into `{branch}`." if merged else f"Merging `{ref}` conflicted, so the merge task in front of this one has to land first."
    detail = f" ({clean_line(failure)})" if failure else ""
    return f"""# review commits from {ref}

The classifier could not sort these commits{detail}. {where} Read them and keep, adjust, or leave any behaviour `{branch}` has not covered. Do not reimplement work that is already in the commits. Never push this branch to main or master.

## What changes

- Account for behaviour from `{ref}` that `{branch}` does not have yet.

Commits:

{subjects}

## Done when

- Those commits are in `{branch}`'s history.
- Behaviour this branch had not covered is covered or explicitly left as-is, and no push targets main or master.
"""


def move_obsolete(root: Path, listed: list, running: set[str]) -> list[str]:
    stamp = os.environ.get("MARESTAIL_UPSTREAM_STAMP") or time.strftime("%Y%m%dT%H%M%S")
    destination = root / ".marestail" / "upstream" / f"obsolete-{stamp}"
    moved = []
    for item in listed:
        relative, source = safe_task(root, str(item))
        if relative is None or source is None or not source.is_file():
            continue
        if relative in running or has_handoffs(root, source.stem):
            say(f"keeping {relative}")
            continue
        destination.mkdir(parents=True, exist_ok=True)
        source.rename(destination / source.name)
        moved.append(relative)
        say(f"set aside {relative}")
    return moved


def commit_paths(root: Path, git, paths: list[str]) -> None:
    if not paths:
        return
    git(["git", "add", "-A", "--", *paths], root)
    code, _ = git(["git", "diff", "--cached", "--quiet"], root)
    if code == 0:
        return
    code, output = git(["git", "commit", "-q", "-m", "human: queue upstream tasks"], root)
    if code != 0:
        raise SystemExit((output.strip() or "could not commit upstream tasks"))


def prompt_for(root: Path, git, branch: str, ref: str, commits: list[tuple[str, str]]) -> str:
    shown = commits[:COMMIT_CAP]
    lines = "\n".join(f"{sha} {subject}" for sha, subject in shown)
    if len(commits) > COMMIT_CAP:
        lines += f"\n... and {len(commits) - COMMIT_CAP} more; they will be merged too"
    names = existing_tasks(root)
    stat = diff_stat(root, git, ref)
    return f"""Reply with one JSON object and nothing else. Do not edit, create, or commit any file.

The work branch is {branch}. These commits are on {ref} and not on {branch}:

{lines}

Diff stat:
{stat}

Existing task files:
{names or "(none)"}

A commit that adds behaviour this branch has not covered becomes one task to merge, sync, or add that behaviour. A commit that is irrelevant (a chore, a fix this branch already replaced, or work the branch has superseded) gets no task: it is still merged, so later review stays a simple diff. If drift has made an existing task file irrelevant, list its path. Leave a task off that list when it is still worth doing.

JSON shape:
{{"tasks":[{{"slug":"short-kebab","title":"verb phrase","why":"plain sentence","what":"plain sentence","commits":["full sha"]}}],"obsolete":["tasks/003-old.md"]}}
"""


def ask_model(root: Path, prompt: str) -> str:
    from marestail import route as dandelion
    from marestail.runner import Run, invoke

    choice, problem = dandelion.choose("dandelion/route", root)
    if choice is None:
        raise RuntimeError(problem or "dandelion route failed")
    say(f"dandelion/route: {choice.line}")
    config = load(root)
    task = config.work / "upstream" / "upstream-classify.md"
    task.parent.mkdir(parents=True, exist_ok=True)
    state = Run(
        config=config,
        task=task,
        model=choice.model,
        retries=0,
        agent=choice.backend,
        effort=choice.effort,
        route=None,
        account_env=dict(choice.env),
    )
    invoke(state, "classify", prompt)
    output = state.folder / "classify.json"
    if not output.is_file() or not output.read_text().strip():
        raise RuntimeError("classifier returned nothing")
    return output.read_text()


def parse_plan(text: str) -> dict:
    found = last_plan(text)
    if found is None:
        for blob in envelope_strings(text):
            found = last_plan(blob)
            if found is not None:
                break
    if found is None:
        raise ValueError("classifier returned no tasks object")
    tasks = found.get("tasks", [])
    obsolete = found.get("obsolete", [])
    if not isinstance(tasks, list) or not isinstance(obsolete, list):
        raise ValueError("classifier tasks/obsolete were not lists")
    return {
        "tasks": [item for item in tasks if isinstance(item, dict)],
        "obsolete": [item for item in obsolete if isinstance(item, str)],
    }


def last_plan(text: str) -> dict | None:
    decoder = json.JSONDecoder()
    found = None
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "tasks" in obj:
            found = obj
    return found


def envelope_strings(text: str) -> list[str]:
    decoder = json.JSONDecoder()
    found = []
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        for key in ("result", "text", "message", "content"):
            value = obj.get(key)
            if isinstance(value, str):
                found.append(value)
    return found


def discard(root: Path, git, branch: str, before: str) -> None:
    git(["git", "checkout", "-q", "-f", branch], root)
    git(["git", "reset", "--hard", "-q", before], root)
    git(["git", "clean", "-fdq", "-e", ".marestail"], root)


def pipeline_busy(root: Path) -> bool:
    root = root.resolve()
    me = os.getpid()
    proc = Path("/proc")
    if not proc.is_dir():
        return False
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        command = cmdline(entry)
        if not is_pipeline(command):
            continue
        try:
            cwd = (entry / "cwd").resolve()
        except OSError:
            continue
        if cwd == root:
            return True
    return False


def running_task_paths(root: Path) -> set[str]:
    root = root.resolve()
    found = set()
    proc = Path("/proc")
    if not proc.is_dir():
        return found
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        command = cmdline(entry)
        if not is_pipeline(command):
            continue
        try:
            cwd = (entry / "cwd").resolve()
        except OSError:
            continue
        if cwd != root:
            continue
        for token in command:
            relative, _ = safe_task(root, token)
            if relative is not None:
                found.add(relative)
    return found


def is_pipeline(command: list[str]) -> bool:
    return any(os.path.basename(token) == "cli.py" and command[index + 1:index + 2] == ["run"] for index, token in enumerate(command))


def cmdline(entry: Path) -> list[str]:
    try:
        raw = (entry / "cmdline").read_bytes()
    except OSError:
        return []
    return [part.decode(errors="replace") for part in raw.split(b"\0") if part]


def dirty(root: Path, git) -> bool:
    code, output = git(["git", "status", "--porcelain", "--untracked-files=all"], root)
    if code != 0:
        return True
    for line in output.splitlines():
        if any(not ignored_status(path) for path in porcelain_paths(line)):
            return True
    return False


def porcelain_paths(line: str) -> list[str]:
    if len(line) < 4:
        return []
    body = line[3:]
    if " -> " in body:
        return [part.strip() for part in body.split(" -> ", 1)]
    return [body.strip()]


def ignored_status(path: str) -> bool:
    return path == ".marestail" or path.startswith(".marestail/")


def unmerged(root: Path, git) -> bool:
    code, output = git(["git", "status", "--porcelain"], root)
    if code != 0:
        return True
    return any(len(line) >= 2 and "U" in line[:2] or line[:2] in {"AA", "DD"} for line in output.splitlines())


def merging(root: Path, git) -> bool:
    code, output = git(["git", "rev-parse", "--git-dir"], root)
    if code != 0 or not output.strip():
        return False
    path = Path(output.strip())
    if not path.is_absolute():
        path = root / path
    return (path / "MERGE_HEAD").exists()


def commit_list(root: Path, git, ref: str) -> list[tuple[str, str]]:
    code, output = git(["git", "log", "--reverse", "--format=%H%x09%s", f"HEAD..{ref}"], root)
    if code != 0:
        return []
    commits = []
    for line in output.splitlines():
        if "\t" not in line:
            continue
        sha, subject = line.split("\t", 1)
        if sha:
            commits.append((sha, subject.strip()))
    return commits


def diff_stat(root: Path, git, ref: str) -> str:
    code, output = git(["git", "diff", "--stat", f"HEAD...{ref}"], root)
    if code != 0:
        return "(unavailable)"
    lines = output.splitlines()[:STAT_CAP]
    if len(output.splitlines()) > STAT_CAP:
        lines.append("...")
    return "\n".join(lines) or "(empty)"


def rev(root: Path, git, ref: str) -> str | None:
    code, output = git(["git", "rev-parse", ref], root)
    if code != 0:
        return None
    return output.strip() or None


def existing_tasks(root: Path) -> str:
    paths = sorted(path.relative_to(root).as_posix() for path in (root / "tasks").glob("*.md")) if (root / "tasks").is_dir() else []
    return "\n".join(paths[:80])


def taken_slugs(root: Path) -> set[str]:
    found = set()
    tasks = root / "tasks"
    if not tasks.is_dir():
        return found
    for path in tasks.glob("*.md"):
        prefix, _, slug = path.stem.partition("-")
        if prefix.isdigit() and slug:
            found.add(slug)
    return found


def next_number(root: Path) -> int:
    highest = 0
    tasks = root / "tasks"
    if tasks.is_dir():
        for path in tasks.glob("*.md"):
            prefix = path.name.split("-", 1)[0]
            if prefix.isdigit():
                highest = max(highest, int(prefix))
    return highest + 1


def has_handoffs(root: Path, stem: str) -> bool:
    folder = root / ".marestail" / "handoffs" / stem
    return folder.is_dir() and any(folder.glob("*.md"))


def safe_task(root: Path, value: str) -> tuple[str | None, Path | None]:
    if not value or value.startswith("/") or ".." in Path(value).parts:
        return None, None
    relative = Path(value)
    if relative.parts[:1] != ("tasks",) or relative.suffix != ".md":
        return None, None
    source = (root / relative).resolve()
    try:
        source.relative_to((root / "tasks").resolve())
    except ValueError:
        return None, None
    return relative.as_posix(), source


def clean_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:48].strip("-")
    if not slug or not SLUG.fullmatch(slug):
        return "upstream-sync"
    return slug


def clean_line(value: str) -> str:
    return " ".join(value.split())[:160]


def clean_text(value: str) -> str:
    return "\n".join(line.rstrip() for line in value.strip().splitlines()[:40])


def remember(root: Path, base_sha: str, conflict: bool) -> None:
    folder = root / ".marestail" / "upstream"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "classified").write_text(base_sha + "\n")
    blocked = folder / "blocked"
    if conflict:
        blocked.write_text(base_sha + "\n")
    else:
        blocked.unlink(missing_ok=True)


def classified_sha(root: Path) -> str:
    path = root / ".marestail" / "upstream" / "classified"
    return path.read_text().strip() if path.is_file() else ""


def write_queue(root: Path, paths: list[str]) -> None:
    queue = queue_path(root)
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text("".join(f"{path}\n" for path in paths))


def queue_path(root: Path) -> Path:
    return root / ".marestail" / "upstream" / "queued"


def mark_due(root: Path, reason: str) -> None:
    folder = root / ".marestail" / "upstream"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "due").write_text(reason + "\n")


def clear_due(root: Path) -> None:
    (root / ".marestail" / "upstream" / "due").unlink(missing_ok=True)


def clear_blocked(root: Path) -> None:
    (root / ".marestail" / "upstream" / "blocked").unlink(missing_ok=True)


def git_failure(output: str, fallback: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    fatal = next((line for line in lines if line.lower().startswith("fatal:")), "")
    return fatal or (lines[0] if lines else fallback)


def say(message: str) -> None:
    print(f"upstream: {message}", flush=True)


@contextlib.contextmanager
def held(root: Path):
    folder = root / ".marestail"
    folder.mkdir(exist_ok=True)
    handle = (folder / "upstream.lock").open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        yield False
        return
    try:
        yield True
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
