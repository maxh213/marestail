#!/usr/bin/env python3
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail import runner
from marestail.config import load
from marestail.context import Context
from marestail.gates import deadcode, sonar, ts_deps, ts_lint, ts_mutation, ts_tests

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "marestail" / "cli.py"
TEMPLATES = ROOT / "templates"
START = "# marestail (install --scope hyper)"
END = "# end marestail"
TOOLING = ".marestail/tooling"
NO_TOOLING = 'marestail.toml has no [ts] tooling; add tooling = ".marestail/tooling" under [ts] so the gates use it'
NPM_FAILED = "npm install --prefix .marestail/tooling failed (exit 1); everything else is installed, see .marestail/tooling/npm.log"
LEFT = "left tracked files alone: .gitignore, CLAUDE.md"
ENTRIES = [
    ".marestail/",
    "mutants/",
    ".scannerwork/",
    ".venv/",
    ".coverage",
    "reports/mutation/",
    ".stryker-tmp/",
    "StrykerOutput/",
    ".sonarqube/",
    ".idea/",
    ".vscode/",
    "marestail.toml",
    "sonar-project.properties",
    "guidance/",
    "tasks/",
    "features/",
    "qa/",
    "perf/",
    "PERFORMANCE.md",
    ".claude/settings.local.json",
    ".agents/hooks.json",
    ".grok/hooks/marestail-gate.json",
    ".cursor/hooks.json",
]
FAKE_NPM = """#!/bin/sh
echo "$PWD|$*" >> "{log}"
echo "npm out"
echo "npm err" >&2
mkdir -p .marestail/tooling/node_modules/.bin
exit "${{NPM_EXIT:-0}}"
"""
PACKAGE = """{
  "name": "marestail-tooling",
  "private": true,
  "devDependencies": {
    "@stryker-mutator/core": "^9.0.0",
    "@stryker-mutator/vitest-runner": "^9.0.0",
    "@vitest/coverage-v8": "^3.2.0",
    "dependency-cruiser": "^16.0.0",
    "eslint": "^9.0.0",
    "knip": "^5.0.0",
    "typescript": "^5.8.0",
    "typescript-eslint": "^8.0.0",
    "vitest": "^3.2.0"
  }
}
"""
ESLINT = """import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["node_modules/**", "dist/**", "coverage/**"] },
  { files: ["src/**/*.{ts,tsx}"], extends: [tseslint.configs.recommended] },
);
"""
TSCONFIG = """{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "types": ["vitest/globals"]
  },
  "include": ["../../web/src"]
}
"""
JEST_TSCONFIG = """{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "typeRoots": ["../../web/node_modules/@types"]
  },
  "include": ["../../web/app"]
}
"""
VITEST = """import { defineConfig } from "vitest/config";

export default defineConfig({
  root: process.cwd(),
  test: {
    globals: true,
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    coverage: { provider: "v8", include: ["src/**/*.{ts,tsx}"], exclude: ["src/**/*.{test,spec}.{ts,tsx}"] },
  },
});
"""
STRYKER = """{
  "testRunner": "vitest",
  "plugins": ["@stryker-mutator/vitest-runner"],
  "vitest": { "configFile": "<T>/vitest.config.ts" },
  "mutate": ["src/**/*.ts", "src/**/*.tsx", "!src/**/*.test.*", "!src/**/*.spec.*"],
  "ignorePatterns": [".marestail"],
  "coverageAnalysis": "perTest"
}
"""
JEST_STRYKER = """{
  "testRunner": "jest",
  "plugins": ["@stryker-mutator/jest-runner"],
  "jest": { "projectType": "custom" },
  "mutate": ["app/**/*.ts", "app/**/*.tsx", "!app/**/*.test.*", "!app/**/*.spec.*"],
  "ignorePatterns": [".marestail"],
  "coverageAnalysis": "perTest"
}
"""
KNIP = """{
  "entry": ["src/index.{ts,tsx}", "src/main.{ts,tsx}"],
  "project": ["src/**/*.{ts,tsx}"]
}
"""
WEB_TOML = '[ts]\nroot = "web"\nsource = "src"\ntooling = ".marestail/tooling"\n'
JEST_TOML = '[ts]\nroot = "web"\nrunner = "jest"\nsources = ["app"]\ntooling = ".marestail/tooling"\n'
REPORTERS = ["--reporters", "json,progress", "--tempDirName", ".stryker-tmp", "--cleanTempDir", "always"]


def expect(name: str, got: object, wanted: object) -> None:
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


def status(root: Path) -> str:
    return git(root, "status", "--porcelain")


def commit(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    git(root, "add", "-f", *files)
    git(root, "commit", "-qm", "seed")


def new_repo(folder: Path) -> Path:
    folder.mkdir(parents=True)
    git(folder, "init", "-q", "-b", "main")
    git(folder, "config", "user.email", "t@example.com")
    git(folder, "config", "user.name", "t")
    git(folder, "config", "commit.gpgsign", "false")
    return folder.resolve()


def trust(grok: Path, target: Path) -> None:
    grok.mkdir(parents=True, exist_ok=True)
    (grok / "trusted_folders.toml").write_text(f"[folders.{json.dumps(str(target.resolve()))}]\ntrusted = true\ndecided_at = 1\n")


class Sandbox:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.npm_log = tmp / "npm-calls.log"
        (self.bin / "npm").write_text(FAKE_NPM.format(log=self.npm_log))
        (self.bin / "npm").chmod(0o755)
        self.grok = tmp / "grok"
        self.target = new_repo(tmp / "target")
        commit(self.target, {".gitignore": "node_modules/\n", "CLAUDE.md": "team rules\n"})
        trust(self.grok, self.target)
        self.npm_exit = "0"

    def install(self, *args: str, target: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "GROK_HOME": str(self.grok), "NPM_EXIT": self.npm_exit}
        where = str(target or self.target)
        return subprocess.run([sys.executable, str(CLI), "install", *args, where], capture_output=True, text=True, check=False, env=env)

    def hyper(self, target: Path | None = None) -> subprocess.CompletedProcess[str]:
        return self.install("--scope", "hyper", target=target)

    def npm_calls(self) -> list[str]:
        return self.npm_log.read_text().splitlines() if self.npm_log.exists() else []

    @property
    def tooling(self) -> Path:
        return self.target / TOOLING

    def done(self) -> str:
        return f"installed into {self.target} with --scope hyper; nothing to commit, see .git/info/exclude"


@contextlib.contextmanager
def sandbox() -> Iterator[Sandbox]:
    with tempfile.TemporaryDirectory() as tmp:
        yield Sandbox(Path(tmp))


def lines(completed: subprocess.CompletedProcess[str]) -> list[str]:
    return completed.stdout.splitlines()


def exclude_text(root: Path) -> str:
    return (root / ".git" / "info" / "exclude").read_text()


def block(root: Path) -> list[str]:
    text = exclude_text(root).splitlines()
    return text[text.index(START) + 1 : text.index(END)]


def hyper_install_leaves_tree_clean() -> None:
    with sandbox() as box:
        done = box.hyper()
        expect("clean-exit", done.returncode, 0)
        expect("clean-status", status(box.target), "")
        expect("clean-gitignore", (box.target / ".gitignore").read_text(), "node_modules/\n")
        expect("clean-claude", (box.target / "CLAUDE.md").read_text(), "team rules\n")
        expect("clean-agents", (box.target / "AGENTS.md").exists(), False)
        expect("clean-stdout", lines(done), [LEFT, box.done()])
        expect("clean-stderr", done.stderr, "")
        log = (box.tooling / "npm.log").read_text()
        expect("clean-npm-log", ("npm out" in log, "npm err" in log), (True, True))


def first_install_trusts_grok() -> None:
    with sandbox() as box:
        shutil.rmtree(box.grok)
        done = box.hyper()
        expect("trust-stdout", lines(done), [f"trusted {box.target} for grok project hooks", LEFT, box.done()])
        store = (box.grok / "trusted_folders.toml").read_text()
        expect("trust-store", f"[folders.{json.dumps(str(box.target))}]\ntrusted = true" in store, True)
        expect("trust-again", lines(box.hyper()), [LEFT, box.done()])


def npm_failure_still_trusts_grok() -> None:
    with sandbox() as box:
        shutil.rmtree(box.grok)
        box.npm_exit = "1"
        done = box.hyper()
        expect("npm-trust-exit", done.returncode, 1)
        expect("npm-trust-stdout", lines(done), [f"trusted {box.target} for grok project hooks", LEFT, NPM_FAILED])


def exclude_hides_everything() -> None:
    with sandbox() as box:
        before = exclude_text(box.target)
        box.hyper()
        text = exclude_text(box.target)
        expect("exclude-start", text.splitlines().count(START), 1)
        expect("exclude-end", text.splitlines().count(END), 1)
        expect("exclude-entries", sorted(block(box.target)), sorted(ENTRIES))
        expect("exclude-samples", text.startswith(before), True)
        expect("exclude-toml", (box.target / "marestail.toml").exists(), True)
        ignored = subprocess.run(["git", "check-ignore", "-q", "marestail.toml"], cwd=box.target, check=False)
        expect("exclude-check-ignore", ignored.returncode, 0)
        settings = (box.target / ".claude" / "settings.local.json").read_text()
        expect("exclude-local-hook", "marestail gate --hook" in settings, True)
        expect("exclude-no-settings", (box.target / ".claude" / "settings.json").exists(), False)


def install_twice_one_block() -> None:
    with sandbox() as box:
        box.hyper()
        box.hyper()
        expect("twice-blocks", exclude_text(box.target).splitlines().count(START), 1)
        expect("twice-status", status(box.target), "")


def tooling_lives_out_of_tree() -> None:
    with sandbox() as box:
        box.hyper()
        expect("tree-package", (box.tooling / "package.json").exists(), True)
        expect("tree-npm", box.npm_calls(), [f"{box.target}|install --prefix .marestail/tooling"])
        for name in ("package.json", "package-lock.json", "eslint.config.mjs", "sonar-project.properties"):
            expect(f"tree-no-root-{name}", (box.target / name).exists(), False)
        expect("tree-tooling-key", 'tooling = ".marestail/tooling"' in (box.target / "marestail.toml").read_text(), True)
        expect("tree-sonar", (box.tooling / "sonar-project.properties").exists(), True)


def tooling_files_pinned() -> None:
    with sandbox() as box:
        (box.target / "marestail.toml").write_text(WEB_TOML)
        box.hyper()
        tooling = str(box.tooling)
        expect("pinned-toml", (box.target / "marestail.toml").read_text(), WEB_TOML)
        expect("pinned-package", (box.tooling / "package.json").read_text(), PACKAGE)
        expect("pinned-eslint", (box.tooling / "eslint.config.mjs").read_text(), ESLINT)
        expect("pinned-tsconfig", (box.tooling / "tsconfig.json").read_text(), TSCONFIG)
        expect("pinned-vitest", (box.tooling / "vitest.config.ts").read_text(), VITEST)
        expect("pinned-stryker", (box.tooling / "stryker.config.json").read_text(), STRYKER.replace("<T>", tooling))
        expect("pinned-knip", (box.tooling / "knip.json").read_text(), KNIP)
        depcruise = (
            (TEMPLATES / "dependency-cruiser.cjs")
            .read_text()
            .replace('fileName: "tsconfig.app.json"', f'fileName: "{tooling}/tsconfig.json"')
        )
        expect("pinned-depcruise", (box.tooling / ".dependency-cruiser.cjs").read_text(), depcruise)
        sonar_lines = (box.tooling / "sonar-project.properties").read_text().splitlines()
        expect(
            "pinned-sonar",
            next(line for line in sonar_lines if line.startswith("sonar.exclusions=")).endswith(",perf/**,.marestail/**"),
            True,
        )


def tooling_files_per_source() -> None:
    with sandbox() as box:
        (box.target / "marestail.toml").write_text('[ts]\nroot = "web"\nsources = ["app", "lib"]\ntooling = ".marestail/tooling"\n')
        box.hyper()
        knip = (box.tooling / "knip.json").read_text()
        expect("sources-knip", '"project": ["app/**/*.{ts,tsx}", "lib/**/*.{ts,tsx}"]' in knip, True)
        expect("sources-tsconfig", '"include": ["../../web/app", "../../web/lib"]' in (box.tooling / "tsconfig.json").read_text(), True)


def global_modules() -> str:
    npm = shutil.which("npm")
    if npm is None or shutil.which("node") is None:
        return ""
    return subprocess.run([npm, "root", "-g"], capture_output=True, text=True, check=False).stdout.strip()


def typescript_home() -> Path | None:
    candidates = [os.environ.get("MARESTAIL_TYPESCRIPT", ""), global_modules()]
    found = [Path(base) / "typescript" for base in candidates if base] + sorted(
        Path("/usr/lib/node_modules").glob("*/node_modules/typescript")
    )
    return next((path for path in found if pinned_major(path)), None)


def pinned_major(typescript: Path) -> bool:
    package = typescript / "package.json"
    return package.exists() and json.loads(package.read_text()).get("version", "").startswith("5.")


def jest_types_resolve(box: Sandbox) -> None:
    typescript = typescript_home()
    if typescript is None or shutil.which("node") is None:
        print("jest-tsc: skipped, no typescript 5 found (set MARESTAIL_TYPESCRIPT to a node_modules folder holding one)")
        return
    types = box.target / "web" / "node_modules" / "@types" / "jest"
    types.mkdir(parents=True)
    (types / "index.d.ts").write_text("declare function describe(name: string, body: () => void): void;\n")
    (box.target / "web" / "app").mkdir(parents=True)
    (box.target / "web" / "app" / "a.test.ts").write_text('describe("a", () => {});\n')
    completed = subprocess.run(
        ["node", str(typescript / "bin" / "tsc"), "--noEmit", "-p", str(box.tooling / "tsconfig.json")],
        cwd=box.target / "web",
        capture_output=True,
        text=True,
        check=False,
    )
    expect("jest-tsc", (completed.returncode, completed.stdout), (0, ""))


def jest_target_gets_jest_runner() -> None:
    with sandbox() as box:
        (box.target / "marestail.toml").write_text(JEST_TOML)
        box.hyper()
        package = json.loads((box.tooling / "package.json").read_text())["devDependencies"]
        wanted = json.loads(PACKAGE)["devDependencies"]
        for name in ("vitest", "@vitest/coverage-v8", "@stryker-mutator/vitest-runner"):
            del wanted[name]
        wanted["@stryker-mutator/jest-runner"] = "^9.0.0"
        expect("jest-package", package, wanted)
        expect("jest-stryker", (box.tooling / "stryker.config.json").read_text(), JEST_STRYKER)
        expect("jest-tsconfig", (box.tooling / "tsconfig.json").read_text(), JEST_TSCONFIG)
        expect(
            "jest-no-runner-config",
            sorted(path.name for path in box.tooling.glob("*.config.*") if "vitest" in path.name or "jest" in path.name),
            [],
        )
        ctx = Context(config=load(box.target))
        expect(
            "jest-command",
            ts_tests.jest_command(ctx)[:3],
            [str(box.target / "web" / "node_modules" / ".bin" / "jest"), "--ci", "--coverage"],
        )
        jest_types_resolve(box)


def own_configs_not_duplicated() -> None:
    with sandbox() as box:
        (box.target / "marestail.toml").write_text('[ts]\nroot = "web"\ntooling = ".marestail/tooling"\n')
        (box.target / "web").mkdir()
        for name in ("eslint.config.js", "tsconfig.app.json", ".dependency-cruiser.cjs"):
            (box.target / "web" / name).write_text("")
        box.hyper()
        names = sorted(path.name for path in box.tooling.iterdir() if path.is_file())
        expected = ["knip.json", "npm.log", "package.json", "sonar-project.properties", "stryker.config.json", "vitest.config.ts"]
        expect("own-configs", names, expected)


def tracked_hook_left_alone() -> None:
    with sandbox() as box:
        commit(box.target, {".cursor/hooks.json": "{}\n"})
        done = box.hyper()
        expect("hook-file", (box.target / ".cursor" / "hooks.json").read_text(), "{}\n")
        expect("hook-skipped", "no Stop hook for cursor: .cursor/hooks.json is tracked" in lines(done), True)
        expect("hook-left", "left tracked files alone: .cursor/hooks.json, .gitignore, CLAUDE.md" in lines(done), True)
        expect("hook-status", status(box.target), "")


def committed_files_kept() -> None:
    with sandbox() as box:
        commit(box.target, {"marestail.toml": '[git]\nbase = "main"\n', "PERFORMANCE.md": "ours\n", ".claude/settings.local.json": "{}\n"})
        done = box.hyper()
        expect("kept-toml", (box.target / "marestail.toml").read_text(), '[git]\nbase = "main"\n')
        expect("kept-toml-tracked", git(box.target, "ls-files", "marestail.toml"), "marestail.toml\n")
        expect("kept-performance", (box.target / "PERFORMANCE.md").read_text(), "ours\n")
        expect("kept-settings", (box.target / ".claude" / "settings.local.json").read_text(), "{}\n")
        expected = [
            "left tracked files alone: .claude/settings.local.json, .gitignore, CLAUDE.md, PERFORMANCE.md, marestail.toml",
            "no Stop hook for claude: .claude/settings.local.json is tracked",
            NO_TOOLING,
            box.done(),
        ]
        expect("kept-stdout", lines(done), expected)
        expect("kept-exit", (done.returncode, status(box.target)), (0, ""))


def tracked_sonar_listed() -> None:
    with sandbox() as box:
        commit(box.target, {"sonar-project.properties": "sonar.projectKey=ours\n", "web/eslint.config.js": "export default [];\n"})
        done = box.hyper()
        expect("sonar-file", (box.target / "sonar-project.properties").read_text(), "sonar.projectKey=ours\n")
        expect("sonar-no-tooling", (box.tooling / "sonar-project.properties").exists(), False)
        expect("sonar-stdout", lines(done), ["left tracked files alone: .gitignore, CLAUDE.md, sonar-project.properties", box.done()])
        expect("sonar-exit", (done.returncode, status(box.target)), (0, ""))


def untracked_files_kept() -> None:
    with sandbox() as box:
        (box.target / "marestail.toml").write_text('[ts]\nroot = "."\n')
        (box.target / "guidance").mkdir()
        (box.target / "guidance" / "ts.md").write_text("mine\n")
        (box.target / ".claude").mkdir()
        (box.target / ".claude" / "settings.local.json").write_text('{"env": {"A": "1"}}\n')
        done = box.hyper()
        expect("untracked-toml", (box.target / "marestail.toml").read_text(), '[ts]\nroot = "."\n')
        expect("untracked-guidance", (box.target / "guidance" / "ts.md").read_text(), "mine\n")
        settings = json.loads((box.target / ".claude" / "settings.local.json").read_text())
        expect("untracked-env", settings["env"], {"A": "1"})
        expect("untracked-hook", "marestail gate --hook" in json.dumps(settings["hooks"]["Stop"]), True)
        expect("untracked-stdout", lines(done), [LEFT, NO_TOOLING, box.done()])


def non_git_refused() -> None:
    with sandbox() as box:
        plain = box.tmp / "plain"
        plain.mkdir()
        done = box.hyper(target=plain)
        expect("plain-exit", done.returncode, 1)
        expect("plain-stdout", lines(done), [f"marestail install --scope hyper needs a git repository: {plain}"])
        expect("plain-empty", list(plain.iterdir()), [])
        expect("plain-untrusted", str(plain) in (box.grok / "trusted_folders.toml").read_text(), False)


def npm_failure_reported() -> None:
    with sandbox() as box:
        box.npm_exit = "1"
        done = box.hyper()
        expect("npm-exit", done.returncode, 1)
        expect("npm-stdout", lines(done), [LEFT, NPM_FAILED])
        log = (box.tooling / "npm.log").read_text()
        expect("npm-log", (done.stderr, "npm out" in log, "npm err" in log), ("", True, True))
        for name in ("marestail.toml", f"{TOOLING}/package.json", ".claude/settings.local.json"):
            expect(f"npm-wrote-{name}", (box.target / name).exists(), True)
        expect("npm-status", status(box.target), "")


def force_added_file_untracked() -> None:
    with sandbox() as box:
        box.hyper()
        before = git(box.target, "rev-parse", "HEAD").strip()
        git(box.target, "add", "-f", "marestail.toml")
        git(box.target, "commit", "-qm", "force")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            runner.drop_ignored_since(load(box.target), before)
        expect("force-dropped", "dropping gitignored files: marestail.toml" in out.getvalue(), True)
        expect("force-untracked", git(box.target, "ls-files", "marestail.toml"), "")
        expect("force-on-disk", (box.target / "marestail.toml").exists(), True)


def worktree_shares_exclusions() -> None:
    with sandbox() as box:
        box.hyper()
        worktree = box.tmp / "wt"
        git(box.target, "worktree", "add", "-q", str(worktree))
        (worktree / "marestail.toml").write_text("")
        (worktree / ".marestail").mkdir()
        (worktree / ".marestail" / "x").write_text("")
        expect("worktree-status", status(worktree), "")


def record(module: object, calls: list[list[str]]) -> None:
    def fake(command: list[str], cwd: Path, **_options: object) -> tuple[int, str]:
        calls.append(list(command))
        return 0, "{}"

    module.run = fake  # type: ignore[attr-defined]


def gate_commands(raw: dict[str, object], root: Path) -> list[list[str]]:
    from marestail.config import Config

    ctx = Context(config=Config(root=root, raw=raw))
    calls: list[list[str]] = []
    for module in (ts_lint, ts_deps, deadcode):
        record(module, calls)
    ts_lint.tsc_findings(ctx)
    calls.append(ts_lint.eslint_command(ctx))
    ts_deps.run_gate(ctx)
    deadcode.ts_findings(ctx)
    calls.append(ts_mutation.mutation_command(ctx, []))
    calls.append(ts_tests.vitest_command(ctx)[:5])
    calls.append(sonar.settings_property(ctx))
    return calls


def gates_follow_tooling() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tsconfig.app.json").write_text("{}")
        today = gate_commands({"ts": {"root": ".", "source": "src"}}, root)
        expect("today-tsc", today[0], ["npx", "tsc", "--noEmit", "-p", "tsconfig.app.json"])
        expect("today-eslint", today[1], ["npx", "eslint", ".", "--ignore-pattern", "perf/", "--max-warnings", "0", "--format", "json"])
        expect("today-deps", today[2], ["npx", "depcruise", "--config", ".dependency-cruiser.cjs", "--output-type", "err", "src"])
        expect("today-knip", today[3], ["npx", "--yes", "knip", "--reporter", "json", "--no-progress"])
        expect("today-stryker", today[4], ["npx", "stryker", "run", *REPORTERS])
        expect("today-vitest", today[5][:3], ["npx", "vitest", "run"])
        expect("today-sonar", today[6], [])
        tooling = root / TOOLING
        tooling.mkdir(parents=True)
        for name in (
            "tsconfig.json",
            "eslint.config.mjs",
            ".dependency-cruiser.cjs",
            "stryker.config.json",
            "knip.json",
            "vitest.config.ts",
            "sonar-project.properties",
        ):
            (tooling / name).write_text("{}")
        b = tooling / "node_modules" / ".bin"
        tooled = gate_commands({"ts": {"root": ".", "source": "src", "tooling": TOOLING}}, root)
        expect("tooled-tsc", tooled[0], [str(b / "tsc"), "--noEmit", "-p", str(tooling / "tsconfig.json")])
        eslint = [
            str(b / "eslint"),
            "-c",
            str(tooling / "eslint.config.mjs"),
            ".",
            "--ignore-pattern",
            "perf/",
            "--max-warnings",
            "0",
            "--format",
            "json",
        ]
        expect("tooled-eslint", tooled[1], eslint)
        expect(
            "tooled-deps",
            tooled[2],
            [str(b / "depcruise"), "--config", str(tooling / ".dependency-cruiser.cjs"), "--output-type", "err", "src"],
        )
        expect("tooled-knip", tooled[3], [str(b / "knip"), "--config", str(tooling / "knip.json"), "--reporter", "json", "--no-progress"])
        expect("tooled-stryker", tooled[4], [str(b / "stryker"), "run", str(tooling / "stryker.config.json"), *REPORTERS])
        expect(
            "tooled-vitest", tooled[5], [str(b / "vitest"), "run", "--config", str(tooling / "vitest.config.ts"), "--coverage.enabled=true"]
        )
        expect("tooled-sonar", tooled[6], [f"-Dproject.settings={tooling / 'sonar-project.properties'}"])


def other_installs_unchanged() -> None:
    with sandbox() as box:
        cases = {"a": [], "b": ["--gitignore-generated"], "c": ["--scope", "hard"]}
        for name, args in cases.items():
            repo = new_repo(box.tmp / name)
            git(repo, "commit", "-q", "--allow-empty", "-m", "empty")
            before = hashlib.sha1((repo / ".git" / "info" / "exclude").read_bytes()).hexdigest()
            done = box.install(*args, target=repo)
            alone = "; left CLAUDE.md and AGENTS.md alone" if name == "c" else ""
            expect(f"other-{name}-last", lines(done)[-1], f"installed into {repo}{alone}; edit marestail.toml and sonar-project.properties")
            expect(f"other-{name}-exclude", hashlib.sha1((repo / ".git" / "info" / "exclude").read_bytes()).hexdigest(), before)
            expect(f"other-{name}-tooling", (repo / TOOLING).exists(), False)
        expect("other-npm", box.npm_calls(), [])
        helped = subprocess.run([sys.executable, str(CLI), "install", "--help"], capture_output=True, text=True, check=False)
        expect("other-help", "{all,changed,hard,hyper}" in helped.stdout, True)


CASES = [
    hyper_install_leaves_tree_clean,
    first_install_trusts_grok,
    npm_failure_still_trusts_grok,
    exclude_hides_everything,
    install_twice_one_block,
    tooling_lives_out_of_tree,
    tooling_files_pinned,
    tooling_files_per_source,
    jest_target_gets_jest_runner,
    own_configs_not_duplicated,
    tracked_hook_left_alone,
    committed_files_kept,
    tracked_sonar_listed,
    untracked_files_kept,
    non_git_refused,
    npm_failure_reported,
    force_added_file_untracked,
    worktree_shares_exclusions,
    gates_follow_tooling,
    other_installs_unchanged,
]


def main() -> int:
    for case in CASES:
        case()
    print("install-hyper ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
