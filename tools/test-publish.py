#!/usr/bin/env python3
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail import cli, publish, upstream

ROOT = Path(__file__).resolve().parent.parent


def expect(name, got, wanted):
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def expect_exit(name, action, fragment):
    try:
        action()
    except SystemExit as error:
        if fragment not in str(error):
            raise SystemExit(f"{name}: {error!r} lacks {fragment!r}")
        return
    raise SystemExit(f"{name}: no SystemExit")


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise SystemExit(f"git {' '.join(args)}: {completed.stderr}")
    return completed.stdout


def configure(root: Path) -> None:
    for key, value in (("user.email", "test@example.com"), ("user.name", "test"), ("commit.gpgsign", "false")):
        git(root, "config", key, value)


def new_repo(root: Path, branch: str = "feature", base: str = "main") -> Path:
    git(root, "init", "-q", "-b", branch)
    configure(root)
    (root / "marestail.toml").write_text(f'[git]\nbase = "{base}"\n')
    (root / "README").write_text("base\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def diverge(root: Path) -> None:
    git(root, "branch", "main")
    git(root, "checkout", "-q", "main")
    (root / "reports").write_text("reports\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "add reports")
    (root / "lockfile").write_text("lock\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "bump lockfile")
    git(root, "checkout", "-q", "feature")


def ancestor(root: Path, ref: str = "main") -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", ref, "HEAD"], cwd=root, check=False).returncode == 0


def subjects(root: Path) -> str:
    return git(root, "log", "--format=%s")


def task_text(root: Path) -> str:
    return "\n".join(path.read_text() for path in sorted((root / "tasks").glob("*.md")))


def recorder(pushed: list[str]):
    def push(current: Path) -> int:
        pushed.append(publish.refspec(publish.current_branch(current)))
        return 0

    return push


def plan(prompt: str) -> str:
    if "add reports" not in prompt or "bump lockfile" not in prompt:
        raise SystemExit("prompt did not list both upstream commits")
    return json.dumps({
        "tasks": [{"slug": "add-reports", "title": "Account for reports", "why": "Main added reports.", "what": "Keep the reports behaviour.", "commits": []}],
        "obsolete": [],
    })


def boom(_prompt: str) -> str:
    raise SystemExit("classifier should not have been called")


def refspecs():
    expect("harnesstest", publish.refspec("harnesstest"), "HEAD:refs/heads/harnesstest")
    expect("slash-branch", publish.refspec("marestail/green-the-repo"), "HEAD:refs/heads/marestail/green-the-repo")
    expect("main-refspec", publish.refspec("main"), "HEAD:refs/heads/main")
    expect_exit("feature-not-main", lambda: publish.assert_destination("feature", "refs/heads/main"), "refusing")
    expect_exit("feature-not-master", lambda: publish.assert_destination("harnesstest", "refs/heads/master"), "refusing")
    expect_exit("dotdot", lambda: publish.destination("feature/../main"), "unsafe")
    expect_exit("dash", lambda: publish.destination("-rf"), "unsafe")
    expect_exit("empty", lambda: publish.destination(""), "unsafe")
    calls = []

    def fake(command, _cwd):
        calls.append(command)
        if command[1:3] == ["branch", "--show-current"]:
            return 0, "harnesstest\n"
        return 0, ""

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        expect("dry-run", publish.push_current(Path("."), dry_run=True, git=fake), 0)
    expect("dry-spec", buf.getvalue().strip(), "HEAD:refs/heads/harnesstest")
    expect("dry-no-push", [command for command in calls if command[1] == "push"], [])
    expect("push", publish.push_current(Path("."), git=fake), 0)
    expect("push-command", calls[-1], ["git", "push", "-u", "origin", "HEAD:refs/heads/harnesstest"])
    expect("not-master", "refs/heads/master" in calls[-1][-1] or calls[-1][-1].endswith("refs/heads/main"), False)

    def as_main(command, _cwd):
        calls.append(command)
        if command[1:3] == ["branch", "--show-current"]:
            return 0, "main\n"
        return 0, ""

    expect("push-main", publish.push_current(Path("."), git=as_main), 0)
    expect("main-command", calls[-1], ["git", "push", "-u", "origin", "HEAD:refs/heads/main"])
    expect_exit("detached", lambda: publish.push_current(Path("."), git=lambda _command, _cwd: (0, "\n")), "detached")


def parses_plan():
    text = 'note {"tasks": [{"slug": "old"}]} trailing {"tasks": [{"slug": "add-reports", "title": "Add reports"}], "obsolete": ["tasks/003-old.md"]}'
    found = upstream.parse_plan(text)
    expect("last-plan", found["tasks"][0]["slug"], "add-reports")
    expect("obsolete", found["obsolete"], ["tasks/003-old.md"])
    outer = json.dumps({"result": json.dumps({"tasks": [{"slug": "add-reports", "title": "Add"}], "obsolete": []})})
    expect("envelope", upstream.parse_plan(outer)["tasks"][0]["slug"], "add-reports")
    try:
        upstream.parse_plan("no json here")
    except ValueError:
        return
    raise SystemExit("parse: no ValueError")


def skips_default_branch(branch: str):
    with tempfile.TemporaryDirectory(prefix="marestail-publish-") as temp:
        root = new_repo(Path(temp), branch)
        calls = []

        def wrapping(command, cwd):
            calls.append(command)
            return publish.real_git(command, cwd)

        code = upstream.sync(root, git=wrapping, ask=boom, push=lambda _current: (_ for _ in ()).throw(SystemExit("pushed")))
        expect(f"{branch}-code", code, 0)
        expect(f"{branch}-only-name", [command[1] for command in calls], ["branch"])
        expect(f"{branch}-no-work", (root / ".marestail").exists(), False)


def merges_relevant_and_irrelevant():
    with tempfile.TemporaryDirectory(prefix="marestail-publish-") as temp:
        root = new_repo(Path(temp))
        diverge(root)
        pushed: list[str] = []
        asked = {"n": 0}

        def asking(prompt: str) -> str:
            asked["n"] += 1
            return plan(prompt)

        code = upstream.sync(root, ask=asking, busy=lambda _root: False, push=recorder(pushed), running=lambda _root: set())
        expect("merge-code", code, 0)
        expect("ancestor", ancestor(root), True)
        expect("lockfile", (root / "lockfile").is_file(), True)
        expect("reports", (root / "reports").is_file(), True)
        files = sorted(path.name for path in (root / "tasks").glob("*.md"))
        expect("one-task", files, ["001-add-reports.md"])
        body = (root / "tasks" / "001-add-reports.md").read_text()
        expect("already", "already in this branch" in body, True)
        expect("never-main", "main or master" in body, True)
        expect("refspec", pushed, ["HEAD:refs/heads/feature"])
        expect("not-origin-main", "refs/heads/main" in pushed[0], False)
        queued = (root / ".marestail" / "upstream" / "queued").read_text().split()
        expect("queued", queued, ["tasks/001-add-reports.md"])
        code = upstream.sync(root, ask=asking, busy=lambda _root: False, push=recorder(pushed), running=lambda _root: set())
        expect("second", (code, asked["n"]), (0, 1))
        previous = os.getcwd()
        try:
            os.chdir(root)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                drained = cli.main(["upstream", "drain"])
            expect("drain", (drained, buf.getvalue().strip()), (0, "tasks/001-add-reports.md"))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                dry = cli.main(["push", "--dry-run"])
            expect("cli-dry", (dry, buf.getvalue().strip()), (0, "HEAD:refs/heads/feature"))
        finally:
            os.chdir(previous)
        (root / ".marestail" / "upstream" / "queued").write_text("tasks/001-add-reports.md\n")
        expect("busy-claim", upstream.claim(root, busy=lambda _root: True), [])
        expect("queue-kept", (root / ".marestail" / "upstream" / "queued").is_file(), True)


def defers_when_busy_or_dirty():
    with tempfile.TemporaryDirectory(prefix="marestail-publish-") as temp:
        root = new_repo(Path(temp))
        diverge(root)
        code = upstream.sync(root, ask=boom, busy=lambda _root: True, push=recorder([]))
        expect("busy", (code, (root / ".marestail/upstream/due").read_text().strip(), ancestor(root)), (0, "busy", False))
    with tempfile.TemporaryDirectory(prefix="marestail-publish-") as temp:
        root = new_repo(Path(temp))
        diverge(root)
        (root / "README").write_text("dirty\n")
        code = upstream.sync(root, ask=boom, busy=lambda _root: False, push=recorder([]))
        expect("dirty", (code, (root / ".marestail/upstream/due").read_text().strip(), ancestor(root), (root / "README").read_text()), (0, "dirty", False, "dirty\n"))


def merges_when_classifier_fails():
    with tempfile.TemporaryDirectory(prefix="marestail-publish-") as temp:
        root = new_repo(Path(temp))
        diverge(root)

        def fail(_prompt: str) -> str:
            raise RuntimeError("boom")

        code = upstream.sync(root, ask=fail, busy=lambda _root: False, push=lambda _current: 7, running=lambda _root: set())
        expect("fail-code", code, 7)
        expect("fail-merged", ancestor(root), True)
        body = task_text(root)
        expect("review", "boom" in body and "could not sort" in body and "review-upstream" in "".join(path.name for path in (root / "tasks").glob("*.md")), True)
        expect("no-feature", "add-reports" in body, False)


def discards_classifier_edits():
    with tempfile.TemporaryDirectory(prefix="marestail-publish-") as temp:
        root = new_repo(Path(temp))
        diverge(root)

        def noisy(_prompt: str) -> str:
            git(root, "commit", "--allow-empty", "-q", "-m", "classifier noise")
            (root / "MESS").write_text("x\n")
            return '{"tasks": [], "obsolete": []}'

        code = upstream.sync(root, ask=noisy, busy=lambda _root: False, push=recorder([]), running=lambda _root: set())
        expect("discarded", (code, "classifier noise" in subjects(root), (root / "MESS").exists(), ancestor(root)), (0, False, False, True))


def leaves_edits_when_a_pipeline_starts():
    with tempfile.TemporaryDirectory(prefix="marestail-publish-") as temp:
        root = new_repo(Path(temp))
        diverge(root)
        seen = {"n": 0}

        def busy(_root: Path) -> bool:
            seen["n"] += 1
            return seen["n"] >= 2

        def noisy(_prompt: str) -> str:
            git(root, "commit", "--allow-empty", "-q", "-m", "classifier noise")
            (root / "MESS").write_text("x\n")
            return '{"tasks": [], "obsolete": []}'

        code = upstream.sync(root, ask=noisy, busy=busy, push=recorder([]), running=lambda _root: set())
        expect(
            "kept",
            (code, "classifier noise" in subjects(root), (root / "MESS").exists(), ancestor(root), (root / ".marestail/upstream/due").read_text().strip()),
            (0, True, True, False, "busy-during-classify"),
        )


def queues_a_merge_task_on_conflict():
    with tempfile.TemporaryDirectory(prefix="marestail-publish-") as temp:
        root = new_repo(Path(temp))
        git(root, "branch", "main")
        git(root, "checkout", "-q", "main")
        (root / "README").write_text("main\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "add reports")
        (root / "notes").write_text("notes\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "bump lockfile")
        git(root, "checkout", "-q", "feature")
        (root / "README").write_text("feature\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "feature edit")
        asked = {"n": 0}
        pushed: list[str] = []

        def asking(prompt: str) -> str:
            asked["n"] += 1
            return plan(prompt)

        kwargs = {"ask": asking, "busy": lambda _root: False, "push": recorder(pushed), "running": lambda _root: set()}
        expect("conflict", upstream.sync(root, **kwargs), 0)
        expect("again", upstream.sync(root, **kwargs), 0)
        names = sorted(path.name for path in (root / "tasks").glob("*.md"))
        expect("tasks", names, ["001-merge-default-branch.md", "002-add-reports.md"])
        expect("asked-once", asked["n"], 1)
        queued = (root / ".marestail" / "upstream" / "queued").read_text().splitlines()
        expect("merge-first", queued, ["tasks/001-merge-default-branch.md", "tasks/002-add-reports.md"])
        expect("readme", (root / "README").read_text(), "feature\n")
        expect("notes-absent", (root / "notes").exists(), False)
        expect("not-merged", ancestor(root), False)
        expect("markers", "<<<<<<<" in (root / "README").read_text(), False)
        git_dir = Path(git(root, "rev-parse", "--git-dir").strip())
        if not git_dir.is_absolute():
            git_dir = root / git_dir
        expect("no-merge-head", (git_dir / "MERGE_HEAD").exists(), False)
        expect("pushed-feature", pushed, ["HEAD:refs/heads/feature"])
        later = (root / "tasks" / "002-add-reports.md").read_text()
        expect("not-yet", "not yet" in later, True)


def sets_obsolete_aside():
    with tempfile.TemporaryDirectory(prefix="marestail-publish-") as temp:
        root = new_repo(Path(temp))
        diverge(root)
        (root / "tasks").mkdir()
        (root / "tasks/003-old.md").write_text("# old\n")
        (root / "tasks/004-live.md").write_text("# live\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "tasks")
        handoff = root / ".marestail/handoffs/004-live"
        handoff.mkdir(parents=True)
        (handoff / "01-coder.md").write_text("done\n")

        def asking(_prompt: str) -> str:
            return json.dumps({"tasks": [], "obsolete": ["tasks/003-old.md", "tasks/004-live.md", "tasks/../../etc/passwd", "nope.md"]})

        expect("obsolete", upstream.sync(root, ask=asking, busy=lambda _root: False, push=recorder([]), running=lambda _root: set()), 0)
        expect("moved", (root / "tasks/003-old.md").exists(), False)
        expect("kept-live", (root / "tasks/004-live.md").is_file(), True)
        expect("tracked", "tasks/003-old.md" in git(root, "ls-files"), False)
        aside = list((root / ".marestail/upstream").glob("obsolete-*/003-old.md"))
        expect("aside", len(aside) == 1 and aside[0].is_file(), True)
        expect("safe", upstream.safe_task(root, "tasks/../../etc/passwd"), (None, None))
    with tempfile.TemporaryDirectory(prefix="marestail-publish-") as temp:
        root = new_repo(Path(temp))
        diverge(root)
        (root / "tasks").mkdir()
        (root / "tasks/003-old.md").write_text("# old\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "tasks")

        def asking(_prompt: str) -> str:
            return json.dumps({"tasks": [], "obsolete": ["tasks/003-old.md"]})

        upstream.sync(root, ask=asking, busy=lambda _root: False, push=recorder([]), running=lambda _root: {"tasks/003-old.md"})
        expect("running-stays", (root / "tasks/003-old.md").is_file(), True)


def skips_head_and_failed_fetch():
    with tempfile.TemporaryDirectory(prefix="marestail-publish-") as temp:
        root = new_repo(Path(temp), base="HEAD")
        diverge(root)
        code = upstream.sync(root, ask=boom, busy=lambda _root: False, push=recorder([]))
        expect("head", (code, (root / "reports").exists(), (root / ".marestail/upstream/due").exists()), (0, False, False))
    with tempfile.TemporaryDirectory(prefix="marestail-publish-") as temp:
        root = new_repo(Path(temp), base="origin/main")
        diverge(root)
        code = upstream.sync(root, ask=boom, busy=lambda _root: False, push=recorder([]))
        expect("fetch", (code, (root / ".marestail/upstream/due").read_text().strip(), ancestor(root)), (0, "fetch", False))


def detached_sync_skips():
    with tempfile.TemporaryDirectory(prefix="marestail-publish-") as temp:
        root = new_repo(Path(temp))
        diverge(root)
        git(root, "checkout", "--detach", "-q")
        code = upstream.sync(root, ask=boom, push=lambda _current: (_ for _ in ()).throw(SystemExit("pushed")))
        expect("detached-sync", (code, ancestor(root)), (0, False))


def cli_on_main():
    with tempfile.TemporaryDirectory(prefix="marestail-publish-") as temp:
        root = new_repo(Path(temp), "main")
        previous = os.getcwd()
        try:
            os.chdir(root)
            expect("cli-upstream", cli.main(["upstream"]), 0)
        finally:
            os.chdir(previous)


def scripts_publish_the_current_branch_only():
    overnight = (ROOT / "tools/overnight.sh").read_text()
    scan = (ROOT / "tools/upstream-scan.sh").read_text()
    expect("overnight-push", "marestail push" in overnight and "marestail upstream" in overnight, True)
    expect("overnight-no-git-push", "git push" in overnight, False)
    expect("scan-route", "dandelion/route" in scan and "unset AGENT EFFORT" in scan, True)
    expect("scan-one-level", '"$HOME"/workspace/*/marestail.toml' in scan, True)
    expect("scan-skips-default", "main|master" in scan, True)
    expect("scan-no-git-push", "git push" in scan, False)


if __name__ == "__main__":
    refspecs()
    parses_plan()
    for branch in ("main", "master"):
        skips_default_branch(branch)
    merges_relevant_and_irrelevant()
    defers_when_busy_or_dirty()
    merges_when_classifier_fails()
    discards_classifier_edits()
    leaves_edits_when_a_pipeline_starts()
    queues_a_merge_task_on_conflict()
    sets_obsolete_aside()
    skips_head_and_failed_fetch()
    detached_sync_skips()
    cli_on_main()
    scripts_publish_the_current_branch_only()
    print("publish ok")
