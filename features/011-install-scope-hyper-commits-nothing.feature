Feature: `marestail install --scope hyper` leaves nothing of marestail in the target's history

  After this task a developer can install marestail into a shared repository,
  run a hyper-scoped fix and open a pull request that holds only the fix and
  its tests. Below, <T> is `<target>/.marestail/tooling` as an absolute path and
  <exclude> is the path `git rev-parse --git-path info/exclude` prints in the
  target (`.git/info/exclude` in its main worktree).

  Pinned rules:
  - The exclude block starts with the line `# marestail (install --scope hyper)`
    and ends with `# end marestail`. Between them, each once: every entry of
    `GITIGNORE_LINES`, plus `marestail.toml`, `sonar-project.properties`,
    `guidance/`, `tasks/`, `features/`, `qa/`, `perf/`, `PERFORMANCE.md`,
    `.claude/settings.local.json`, `.agents/hooks.json`, `.grok/hooks/marestail-gate.json`
    and `.cursor/hooks.json`. Text outside the block is left byte-for-byte.
  - Hyper install writes: `marestail.toml` (template plus `tooling = ".marestail/tooling"`
    under `[ts]`), `tasks/README.md`, `PERFORMANCE.md`, `guidance/ts.md`, and under <T>:
    `package.json` (private, devDependencies keys `eslint`, `typescript`,
    `dependency-cruiser`, `@stryker-mutator/core`, `knip`, `vitest`), `eslint.config.mjs`,
    `tsconfig.json`, `.dependency-cruiser.cjs`, `stryker.config.json`, `knip.json`,
    `vitest.config.ts` and `sonar-project.properties`. A config kind the target's ts
    root already has (e.g. its own `eslint.config.mjs`) is not written to <T>.
    It never writes `sonar-project.properties` at the root.
  - It then runs `npm install --prefix .marestail/tooling` with the target as cwd.
  - Stop hooks: Claude goes to `.claude/settings.local.json`, never `.claude/settings.json`.
    Agy, Grok and Cursor hook files are written only when not tracked; a tracked one
    is left alone and install prints `no Stop hook for <backend>: <path> is tracked`.
  - Hyper install never writes `.gitignore`, `CLAUDE.md` or `AGENTS.md`, tracked or not.
  - Stdout, in order: an optional Grok trust line; `left tracked files alone: <paths>`
    (comma+space separated, sorted, every tracked path install would have written or
    edited; the line is omitted when there are none); the `no Stop hook` lines; and
    last `installed into <target> with --scope hyper; nothing to commit, see <exclude>`.
  - Gate commands with `[ts] tooling` set, `<b>` = `<T>/node_modules/.bin`:
    tsc `<b>/tsc --noEmit -p <T>/tsconfig.json`;
    eslint `<b>/eslint -c <T>/eslint.config.mjs .` then today's remaining arguments;
    depcruise `<b>/depcruise --config <T>/.dependency-cruiser.cjs --output-type err <source>`;
    stryker `<b>/stryker run <T>/stryker.config.json` then today's remaining arguments;
    knip `<b>/knip --config <T>/knip.json --reporter json --no-progress`;
    vitest `<b>/vitest run --config <T>/vitest.config.ts` then today's coverage arguments
    (jest: `<b>/jest --config <T>/jest.config.js` then today's arguments);
    the TS scanners resolve `typescript` from `<T>/package.json`;
    the Sonar scanner gets `-Dproject.settings=<T>/sonar-project.properties`.
    A config file missing from <T> leaves that flag out; the binary still comes from <b>.

  Background:
    Given a temporary git repository <target> on branch `main` with one commit
    And that commit tracks `.gitignore` holding exactly `node_modules/\n`
    And that commit tracks `CLAUDE.md` holding exactly `team rules\n`
    And a fake `npm` first on PATH that records its argv and cwd, creates `.marestail/tooling/node_modules/.bin/`, and exits 0
    And GROK_HOME points at a fresh empty directory

  Scenario: a hyper install leaves the working tree clean
    When I run `marestail install --scope hyper <target>`
    Then the exit code is 0
    And `git status --porcelain` in <target> prints nothing
    And `.gitignore` is still exactly `node_modules/\n` and `CLAUDE.md` still exactly `team rules\n`
    And `AGENTS.md` does not exist
    And stdout contains `left tracked files alone: .gitignore, CLAUDE.md`
    And the last stdout line is `installed into <target> with --scope hyper; nothing to commit, see .git/info/exclude`

  Scenario: everything install wrote is hidden by the local exclude file
    When I run `marestail install --scope hyper <target>`
    Then `.git/info/exclude` holds exactly one line `# marestail (install --scope hyper)` and one line `# end marestail`
    And every pinned exclude entry appears once between them
    And the git sample comment lines that were in `.git/info/exclude` before are still there
    And `marestail.toml` exists at the root and `git check-ignore -q marestail.toml` exits 0
    And `.claude/settings.local.json` Stop hooks include `marestail gate --hook`
    And `.claude/settings.json` does not exist

  Scenario: installing twice leaves one block
    When I run `marestail install --scope hyper <target>` twice
    Then `.git/info/exclude` still holds exactly one `# marestail (install --scope hyper)` line
    And `git status --porcelain` prints nothing

  Scenario: tooling lives out of tree
    When I run `marestail install --scope hyper <target>`
    Then `.marestail/tooling/package.json` exists and its devDependencies hold the pinned keys
    And the fake npm was called once with argv `npm install --prefix .marestail/tooling` and cwd <target>
    And <target> has no `package.json`, `package-lock.json` or `eslint.config.mjs` at its root
    And `marestail.toml` has `tooling = ".marestail/tooling"` under `[ts]`
    And `sonar-project.properties` exists under `.marestail/tooling/` and not at the root

  Scenario: a repo's own config is not duplicated into tooling
    Given the target's ts root holds its own `eslint.config.mjs`
    When I run `marestail install --scope hyper <target>`
    Then `.marestail/tooling/eslint.config.mjs` does not exist
    And the ts.lint eslint command has no `-c` and runs `<T>/node_modules/.bin/eslint .`

  Scenario: a tracked hook file is left alone and reported
    Given the commit also tracks `.cursor/hooks.json` holding exactly `{}\n`
    When I run `marestail install --scope hyper <target>`
    Then `.cursor/hooks.json` is still exactly `{}\n`
    And stdout contains `no Stop hook for cursor: .cursor/hooks.json is tracked`
    And stdout contains `left tracked files alone: .cursor/hooks.json, .gitignore, CLAUDE.md`
    And `git status --porcelain` prints nothing

  Scenario: a target that already committed marestail files keeps them
    Given the commit also tracks `marestail.toml` holding exactly `[git]\nbase = "main"\n`
    When I run `marestail install --scope hyper <target>`
    Then `marestail.toml` is still exactly `[git]\nbase = "main"\n` and `git ls-files marestail.toml` still prints `marestail.toml`
    And stdout contains `left tracked files alone: .gitignore, CLAUDE.md, marestail.toml`
    And `git status --porcelain` prints nothing

  Scenario: a target that is not a git repository is refused
    Given <plain> is an empty directory outside any git repository
    When I run `marestail install --scope hyper <plain>`
    Then the exit code is 1
    And stdout is `marestail install --scope hyper needs a git repository: <plain>`
    And <plain> is still empty

  Scenario: npm failing is reported
    Given the fake npm exits 1
    When I run `marestail install --scope hyper <target>`
    Then the exit code is 1
    And the last stdout line is `npm install --prefix .marestail/tooling failed (exit 1); everything else is installed`
    And `git status --porcelain` prints nothing

  Scenario: a force-added marestail file is untracked by the runner
    Given `marestail install --scope hyper <target>` has run and <before> is HEAD
    When a worker runs `git add -f marestail.toml` and commits
    And the runner's untrack step `runner.drop_ignored_since(config, <before>)` runs
    Then `git ls-files marestail.toml` prints nothing and `marestail.toml` is still on disk

  Scenario: a second worktree of the clone sees the same exclusions
    Given `marestail install --scope hyper <target>` has run
    When I run `git worktree add ../wt` in <target> and create `../wt/marestail.toml` and `../wt/.marestail/x`
    Then `git status --porcelain` in `../wt` prints nothing

  Scenario: gates point at the tooling configs when tooling is set
    Given a context whose marestail.toml has `[ts]` with `root = "."`, `source = "src"` and `tooling = ".marestail/tooling"`
    And every config file named in the pinned rules exists under <T>
    Then the tsc, eslint, depcruise, stryker, knip and vitest commands are exactly the pinned commands
    And the Sonar scanner command contains `-Dproject.settings=<T>/sonar-project.properties`

  Scenario: gates without tooling run the commands of today
    Given a context whose marestail.toml has `[ts]` with `root = "."` and `source = "src"` and no `tooling`
    Then tsc runs `npx tsc --noEmit -p tsconfig.app.json`
    And eslint runs `npx eslint . --max-warnings 0 --format json` (plus `--ignore-pattern perf/` when the ts root is the repo root)
    And depcruise runs `npx depcruise --config .dependency-cruiser.cjs --output-type err src`
    And stryker runs `npx stryker run --reporters json,progress --tempDirName <today's temp dir> --cleanTempDir always`
    And knip runs `npx --yes knip --reporter json --no-progress`
    And the Sonar scanner command contains no `-Dproject.settings`

  Scenario: the other installs are unchanged
    When I run `marestail install <dir>`, `marestail install --gitignore-generated <dir>` and `marestail install --scope hard <dir>` into fresh empty directories
    Then each writes and prints exactly what features/004-hard-scope-install-skips-agent-docs.feature pins
    And none of them writes `.marestail/tooling/`, runs npm, or touches `.git/info/exclude`
    And `marestail install --help` lists `--scope` with choices `all`, `changed`, `hard`, `hyper`

  Scenario: diagnostics and README
    When I run `python3 tools/test-install-hyper.py`
    Then it exits 0 and its last line contains `ok`
    And every other `tools/test-*.py` exits as it did before this task (`tools/test-perf.py` still exits 1 with last line `verdict-commit-files: '' != 'perf/bench_x.py'`)
    And README has a section headed `Installing into a repository that does not use marestail`
    And that section shows `marestail install --scope hyper`, names `.git/info/exclude` and `.marestail/tooling`, says the pull request shows only the fix and its tests and no marestail file, and says Python targets still keep their venv and tool lookups where they are today
