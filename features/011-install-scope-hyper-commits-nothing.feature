Feature: `marestail install --scope hyper` leaves nothing of marestail in the target's history

  After this task a developer can install marestail into a shared repository,
  run a hyper-scoped fix and open a pull request that holds only the fix and
  its tests. Below, <T> is `<target>/.marestail/tooling` as an absolute path,
  <b> is `<T>/node_modules/.bin`, <exclude> is the path
  `git rev-parse --git-path info/exclude` prints in the target, <R> is the ts root
  (`[ts] root` of the marestail.toml at the target root after install), <P> is the
  path from <T> to <R> (`../../web` when `root = "web"`) and <S> each entry of
  `[ts] sources`, else `[ts] source`, else `src`.

  Pinned rules:
  - The exclude block starts with the line `# marestail (install --scope hyper)`
    and ends with `# end marestail`. Between them, each once: every entry of
    `GITIGNORE_LINES`, plus `marestail.toml`, `sonar-project.properties`,
    `guidance/`, `tasks/`, `features/`, `qa/`, `perf/`, `PERFORMANCE.md`,
    `.claude/settings.local.json`, `.agents/hooks.json`, `.grok/hooks/marestail-gate.json`
    and `.cursor/hooks.json`. Text outside the block is left byte-for-byte.
  - Hyper install writes `marestail.toml` (the template plus `tooling = ".marestail/tooling"`
    under `[ts]`) only when none exists; an existing one is left byte-for-byte. It
    writes `tasks/README.md`, `PERFORMANCE.md` and `guidance/ts.md` only when missing
    (as today); a tracked or untracked existing one is left byte-for-byte. Only the
    files under <T> of the scenario "tooling files have the pinned contents" are
    overwritten on every hyper install. It never writes `sonar-project.properties`
    at the root.
  - A kind the target already has is not written to <T>. It counts as present when
    one of these exists in <R> (Sonar: in the target root):
    eslint: `eslint.config.{js,mjs,cjs,ts,mts,cts}`, `.eslintrc`, `.eslintrc.{js,cjs,json,yml,yaml}`;
    tsconfig: the file `[ts] tsconfig` names (default `tsconfig.app.json`), the one the gate falls back to;
    depcruise: the file `[ts] depcruise_config` names (default `.dependency-cruiser.cjs`), likewise;
    vitest: `vitest.config.*`, `vite.config.*`; stryker: `stryker.conf.*`, `stryker.config.*`;
    knip: `knip.json`, `knip.jsonc`, `.knip.json`, `.knip.jsonc`, `knip.ts`, `knip.config.*`;
    sonar: `sonar-project.properties`.
    `package.json` under <T> is always written.
  - `[ts] runner = "jest"`: install writes no jest config and adds no jest; jest comes
    from the target's own `node_modules`, as today.
  - It then runs `npm install --prefix .marestail/tooling` with the target as cwd.
    npm's stdout and stderr both go to `.marestail/tooling/npm.log` (replaced each
    run) and never to marestail's stdout or stderr.
  - Stop hooks: Claude goes to `.claude/settings.local.json`, never `.claude/settings.json`.
    Every hook file (Claude, Agy, Grok, Cursor) is merged as today only when not
    tracked; a tracked one is left byte-for-byte and install prints
    `no Stop hook for <backend>: <path> is tracked` (backend `claude`, `agy`, `grok`, `cursor`).
  - Hyper install never writes `.gitignore`, `CLAUDE.md` or `AGENTS.md`, tracked or not.
  - Stdout, in order: an optional Grok trust line; `left tracked files alone: <paths>`
    (comma+space separated, sorted, every tracked path install would have written or
    edited, including `.gitignore`, `CLAUDE.md`, `AGENTS.md`, `marestail.toml`,
    `tasks/README.md`, `PERFORMANCE.md`, `guidance/ts.md` and hook files; omitted when
    there are none); the `no Stop hook` lines, sorted by path; when the root
    `marestail.toml` (new or existing) has no `tooling` under `[ts]`, the line
    `marestail.toml has no [ts] tooling; add tooling = ".marestail/tooling" under [ts] so the gates use it`;
    and last either `installed into <target> with --scope hyper; nothing to commit, see <exclude>`
    (exit 0) or, when npm exits non-zero with code <n>,
    `npm install --prefix .marestail/tooling failed (exit <n>); everything else is installed, see .marestail/tooling/npm.log`
    (exit 1, and no `installed into` line). Nothing else is printed.
  - Gates with `[ts] tooling` set: every `npx <tool>` (tsc, eslint, depcruise, stryker,
    knip, vitest) becomes `<b>/<tool>`, and the config flag points into <T> when that
    file exists in <T>, else it is today's flag and path:
    tsc `<b>/tsc --noEmit -p <T>/tsconfig.json`; the `[ts] tsconfig ... does not exist`
    check is skipped when `<T>/tsconfig.json` exists and runs as today otherwise;
    eslint `<b>/eslint -c <T>/eslint.config.mjs .` then today's remaining arguments;
    depcruise (ts.deps and `graph.ts_graph`) `<b>/depcruise --config <T>/.dependency-cruiser.cjs --output-type <err|text> <source>`;
    stryker `<b>/stryker run <T>/stryker.config.json` then today's remaining arguments;
    knip `<b>/knip --config <T>/knip.json --reporter json --no-progress`;
    vitest `<b>/vitest run --config <T>/vitest.config.ts` then today's coverage arguments;
    jest is unchanged: `<R>/node_modules/.bin/jest` and today's arguments;
    the TS scanners run `node <script> <T> <files...>` with today's cwd, so
    `ts_complexity.mjs`, `ts_depth.mjs` and `ts_comments.mjs` resolve `typescript`
    from `<T>/package.json`;
    Sonar: when `<T>/sonar-project.properties` exists, the scanner gets
    `-Dproject.settings=<T>/sonar-project.properties` and `sonar.exclusions` are read
    from that file, not the root one.
    With `[ts] tooling` unset, every command, argv and file read is today's.

  Background:
    Given a temporary git repository <target> on branch `main` with one commit
    And that commit tracks `.gitignore` holding exactly `node_modules/\n`
    And that commit tracks `CLAUDE.md` holding exactly `team rules\n`
    And a fake `npm` first on PATH that records its argv and cwd, prints `npm out` to stdout and `npm err` to stderr, creates `.marestail/tooling/node_modules/.bin/`, and exits 0
    And GROK_HOME points at a fresh empty directory

  Scenario: a hyper install leaves the working tree clean
    When I run `marestail install --scope hyper <target>`
    Then the exit code is 0
    And `git status --porcelain` in <target> prints nothing
    And `.gitignore` is still exactly `node_modules/\n` and `CLAUDE.md` still exactly `team rules\n`
    And `AGENTS.md` does not exist
    And stdout is exactly the two lines `left tracked files alone: .gitignore, CLAUDE.md` and `installed into <target> with --scope hyper; nothing to commit, see .git/info/exclude`
    And stderr is empty
    And `.marestail/tooling/npm.log` contains `npm out` and `npm err`

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
    Then `.marestail/tooling/package.json` exists
    And the fake npm was called once with argv `npm install --prefix .marestail/tooling` and cwd <target>
    And <target> has no `package.json`, `package-lock.json` or `eslint.config.mjs` at its root
    And `marestail.toml` has `tooling = ".marestail/tooling"` under `[ts]`
    And `sonar-project.properties` exists under `.marestail/tooling/` and not at the root

  Scenario: tooling files have the pinned contents
    Given <target> has an untracked `marestail.toml` holding exactly `[ts]\nroot = "web"\nsource = "src"\ntooling = ".marestail/tooling"\n`
    When I run `marestail install --scope hyper <target>`
    Then `marestail.toml` is unchanged
    And `.marestail/tooling/package.json` is exactly
      """
      {
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
    And `.marestail/tooling/eslint.config.mjs` is exactly
      """
      import tseslint from "typescript-eslint";

      export default tseslint.config(
        { ignores: ["node_modules/**", "dist/**", "coverage/**"] },
        { files: ["src/**/*.{ts,tsx}"], extends: [tseslint.configs.recommended] },
      );
      """
    And `.marestail/tooling/tsconfig.json` is exactly
      """
      {
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
    And `.marestail/tooling/vitest.config.ts` is exactly
      """
      import { defineConfig } from "vitest/config";

      export default defineConfig({
        root: process.cwd(),
        test: {
          globals: true,
          include: ["src/**/*.{test,spec}.{ts,tsx}"],
          coverage: { provider: "v8", include: ["src/**/*.{ts,tsx}"], exclude: ["src/**/*.{test,spec}.{ts,tsx}"] },
        },
      });
      """
    And `.marestail/tooling/stryker.config.json` is exactly, with <T> written as the absolute path
      """
      {
        "testRunner": "vitest",
        "plugins": ["@stryker-mutator/vitest-runner"],
        "vitest": { "configFile": "<T>/vitest.config.ts" },
        "mutate": ["src/**/*.ts", "src/**/*.tsx", "!src/**/*.test.*", "!src/**/*.spec.*"],
        "ignorePatterns": [".marestail"],
        "coverageAnalysis": "perTest"
      }
      """
    And `.marestail/tooling/knip.json` is exactly
      """
      {
        "entry": ["src/index.{ts,tsx}", "src/main.{ts,tsx}"],
        "project": ["src/**/*.{ts,tsx}"]
      }
      """
    And `.marestail/tooling/.dependency-cruiser.cjs` is `templates/dependency-cruiser.cjs` with `fileName: "tsconfig.app.json"` replaced by `fileName: "<T>/tsconfig.json"` (by the `[ts] tsconfig` name when <T> gets no tsconfig)
    And `.marestail/tooling/sonar-project.properties` is `templates/sonar-project.properties` with `,.marestail/**` appended to the `sonar.exclusions` line
    And each file ends with one newline, and with `[ts] sources = ["app", "lib"]` every `src` pattern is written once per source in that order

  Scenario: a jest target gets the jest Stryker runner and no vitest
    Given <target> has an untracked `marestail.toml` holding exactly `[ts]\nroot = "web"\nrunner = "jest"\nsources = ["app"]\ntooling = ".marestail/tooling"\n`
    When I run `marestail install --scope hyper <target>`
    Then `.marestail/tooling/package.json` devDependencies are the pinned ones without `vitest`, `@vitest/coverage-v8` and `@stryker-mutator/vitest-runner`, plus `"@stryker-mutator/jest-runner": "^9.0.0"`
    And `.marestail/tooling/stryker.config.json` has `"testRunner": "jest"`, `"plugins": ["@stryker-mutator/jest-runner"]` and no `vitest` key
    And `.marestail/tooling/tsconfig.json` has `"types": []`
    And no `vitest.config.ts` and no `jest.config.*` exists under `.marestail/tooling/`
    And the ts.tests command is `<target>/web/node_modules/.bin/jest --ci --coverage ...` exactly as today

  Scenario: a repo's own configs are not duplicated into tooling
    Given <target> has an untracked `marestail.toml` with `[ts]` `root = "web"` and `tooling = ".marestail/tooling"`
    And `web/` holds `eslint.config.js`, `tsconfig.app.json` and `.dependency-cruiser.cjs`
    When I run `marestail install --scope hyper <target>`
    Then `.marestail/tooling/` has no `eslint.config.mjs`, `tsconfig.json` or `.dependency-cruiser.cjs`
    And it has `package.json`, `vitest.config.ts`, `stryker.config.json`, `knip.json` and `sonar-project.properties`

  Scenario: a tracked hook file is left alone and reported
    Given the commit also tracks `.cursor/hooks.json` holding exactly `{}\n`
    When I run `marestail install --scope hyper <target>`
    Then `.cursor/hooks.json` is still exactly `{}\n`
    And stdout contains `no Stop hook for cursor: .cursor/hooks.json is tracked`
    And stdout contains `left tracked files alone: .cursor/hooks.json, .gitignore, CLAUDE.md`
    And `git status --porcelain` prints nothing

  Scenario: a target that already committed marestail files keeps them
    Given the commit also tracks `marestail.toml` holding exactly `[git]\nbase = "main"\n`
    And the commit also tracks `PERFORMANCE.md` holding exactly `ours\n` and `.claude/settings.local.json` holding exactly `{}\n`
    When I run `marestail install --scope hyper <target>`
    Then `marestail.toml` is still exactly `[git]\nbase = "main"\n` and `git ls-files marestail.toml` still prints `marestail.toml`
    And `PERFORMANCE.md` is still exactly `ours\n` and `.claude/settings.local.json` still exactly `{}\n`
    And stdout is exactly these lines in order:
      | left tracked files alone: .claude/settings.local.json, .gitignore, CLAUDE.md, PERFORMANCE.md, marestail.toml |
      | no Stop hook for claude: .claude/settings.local.json is tracked |
      | marestail.toml has no [ts] tooling; add tooling = ".marestail/tooling" under [ts] so the gates use it |
      | installed into <target> with --scope hyper; nothing to commit, see .git/info/exclude |
    And the exit code is 0 and `git status --porcelain` prints nothing

  Scenario: untracked marestail files are kept, not overwritten
    Given <target> has an untracked `marestail.toml` holding exactly `[ts]\nroot = "."\n`, an untracked `guidance/ts.md` holding `mine\n` and an untracked `.claude/settings.local.json` holding `{"env": {"A": "1"}}\n`
    When I run `marestail install --scope hyper <target>`
    Then `marestail.toml` and `guidance/ts.md` are unchanged
    And `.claude/settings.local.json` keeps `"env": {"A": "1"}` and its Stop hooks include `marestail gate --hook`
    And stdout is exactly these lines in order:
      | left tracked files alone: .gitignore, CLAUDE.md |
      | marestail.toml has no [ts] tooling; add tooling = ".marestail/tooling" under [ts] so the gates use it |
      | installed into <target> with --scope hyper; nothing to commit, see .git/info/exclude |

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
    And stdout is exactly these lines in order:
      | left tracked files alone: .gitignore, CLAUDE.md |
      | npm install --prefix .marestail/tooling failed (exit 1); everything else is installed, see .marestail/tooling/npm.log |
    And stderr is empty and `.marestail/tooling/npm.log` contains `npm out` and `npm err`
    And `marestail.toml`, `.marestail/tooling/package.json` and `.claude/settings.local.json` exist
    And `git status --porcelain` prints nothing

  Scenario: a force-added marestail file is untracked by the runner
    Given `marestail install --scope hyper <target>` has run and <before> is HEAD
    When a worker runs `git add -f marestail.toml` and commits
    And the runner's untrack step `runner.drop_ignored_since(config, <before>)` runs
    Then stdout contains `dropping gitignored files: marestail.toml`
    And `git ls-files marestail.toml` prints nothing and `marestail.toml` is still on disk

  Scenario: a second worktree of the clone sees the same exclusions
    Given `marestail install --scope hyper <target>` has run
    When I run `git worktree add ../wt` in <target> and create `../wt/marestail.toml` and `../wt/.marestail/x`
    Then `git status --porcelain` in `../wt` prints nothing

  Scenario: gates point at the tooling configs when tooling is set
    Given a context whose marestail.toml has `[ts]` with `root = "."`, `source = "src"` and `tooling = ".marestail/tooling"`
    And every <T> file of the scenario "tooling files have the pinned contents" exists
    Then tsc runs `<b>/tsc --noEmit -p <T>/tsconfig.json` in <R>
    And eslint runs `<b>/eslint -c <T>/eslint.config.mjs . --ignore-pattern perf/ --max-warnings 0 --format json`
    And ts.deps runs `<b>/depcruise --config <T>/.dependency-cruiser.cjs --output-type err src`
    And `graph.ts_graph` runs `<b>/depcruise --config <T>/.dependency-cruiser.cjs --output-type text src`
    And stryker runs `<b>/stryker run <T>/stryker.config.json --reporters json,progress --tempDirName <today's temp dir> --cleanTempDir always`
    And knip runs `<b>/knip --config <T>/knip.json --reporter json --no-progress`
    And vitest runs `<b>/vitest run --config <T>/vitest.config.ts --coverage.enabled=true` then today's remaining coverage arguments
    And the complexity, depth and comments scanners run `node <script> <T> <files...>`
    And the Sonar scanner command contains `-Dproject.settings=<T>/sonar-project.properties`

  Scenario: Sonar exclusions come from the tooling properties
    Given tooling is set, `<T>/sonar-project.properties` has `sonar.exclusions=a/**` and the root has `sonar-project.properties` with `sonar.exclusions=b/**`
    Then the scanner's `-Dsonar.exclusions=` value contains `a/**` and not `b/**`

  Scenario: gates fall back to the repo's own configs when <T> lacks one
    Given a context with `[ts]` `root = "."`, `tsconfig = "tsconfig.build.json"`, `depcruise_config = ".dependency-cruiser.js"` and `tooling = ".marestail/tooling"`
    And <R> holds `tsconfig.build.json` and <T> holds no config files
    Then tsc runs `<b>/tsc --noEmit -p tsconfig.build.json`
    And ts.deps runs `<b>/depcruise --config .dependency-cruiser.js --output-type err src`
    And eslint runs `<b>/eslint . --ignore-pattern perf/ --max-warnings 0 --format json`
    And stryker runs `<b>/stryker run --reporters json,progress ...` with no config argument
    And knip runs `<b>/knip --reporter json --no-progress` and vitest runs `<b>/vitest run --coverage.enabled=true ...`
    And the Sonar scanner command contains no `-Dproject.settings` and exclusions come from the root file
    But with `tsconfig.build.json` also missing from <R>, ts.lint reports `marestail.toml:1 [ts] tsconfig = 'tsconfig.build.json' does not exist under .` as today

  Scenario: gates without tooling run the commands of today
    Given a context whose marestail.toml has `[ts]` with `root = "."` and `source = "src"` and no `tooling`
    Then tsc runs `npx tsc --noEmit -p tsconfig.app.json`
    And eslint runs `npx eslint . --ignore-pattern perf/ --max-warnings 0 --format json`
    And ts.deps runs `npx depcruise --config .dependency-cruiser.cjs --output-type err src` and `graph.ts_graph` the same with `--output-type text`
    And stryker runs `npx stryker run --reporters json,progress --tempDirName <today's temp dir> --cleanTempDir always`
    And knip runs `npx --yes knip --reporter json --no-progress` and vitest runs `npx vitest run --coverage.enabled=true ...`
    And the scanners run `node <script> <R> <files...>`
    And the Sonar scanner command contains no `-Dproject.settings`

  Scenario: the other installs are unchanged
    Given three fresh git repositories, each with one empty commit, and a checksum of each `.git/info/exclude`
    When I run `marestail install <a>`, `marestail install --gitignore-generated <b>` and `marestail install --scope hard <c>`
    Then <a> and <b> end stdout with `installed into <dir>; edit marestail.toml and sonar-project.properties`
    And <c> ends stdout with `installed into <c>; left CLAUDE.md and AGENTS.md alone; edit marestail.toml and sonar-project.properties`
    And each writes exactly what features/004-hard-scope-install-skips-agent-docs.feature pins
    And no `.git/info/exclude` checksum changed, no `.marestail/tooling/` exists and npm was never called
    And `marestail install --help` lists `--scope` with choices `all`, `changed`, `hard`, `hyper`

  Scenario: diagnostics and README
    When I run `python3 tools/test-install-hyper.py`
    Then it exits 0 and its last line contains `ok`
    And every other `tools/test-*.py` exits as it did before this task (`tools/test-perf.py` still exits 1 with last line `verdict-commit-files: '' != 'perf/bench_x.py'`)
    And README has a section headed `Installing into a repository that does not use marestail`
    And that section shows `marestail install --scope hyper`, names `.git/info/exclude` and `.marestail/tooling`, says the pull request shows only the fix and its tests and no marestail file, and says Python targets still keep their venv and tool lookups where they are today
