# QA procedure: `marestail install --scope hyper` commits nothing

Start in the marestail-green repo root with `.venv` active and `bin/` on PATH, and run `export M=$PWD` there. Step 1 moves you into `$T`; later steps say when to change directory. `$G` starts empty, so the first install into any folder prints `trusted <folder> for grok project hooks` as its first line; later installs into that folder do not.

1. Build the target and a fake npm:
   ```sh
   export T=/tmp/mt-hyper G=/tmp/mt-hyper-grok F=/tmp/mt-hyper-bin
   rm -rf $T $G $F /tmp/mt-hyper-wt /tmp/mt-hyper-plain && mkdir -p $T $G $F /tmp/mt-hyper-plain
   printf '#!/bin/sh\necho "$PWD $*" >> %s/npm.log\necho "npm out"; echo "npm err" >&2\nmkdir -p .marestail/tooling/node_modules/.bin\nexit "${NPM_EXIT:-0}"\n' $F > $F/npm && chmod +x $F/npm
   export PATH=$F:$PATH GROK_HOME=$G
   cd $T && git init -q -b main && git config user.email q@a && git config user.name qa
   printf 'node_modules/\n' > .gitignore && printf 'team rules\n' > CLAUDE.md && git add . && git commit -qm seed
   ```
   Expected: `git status --porcelain` prints nothing.
2. `marestail install --scope hyper $T; echo "exit=$?"; cat .marestail/tooling/npm.log`
   Expected: exactly `trusted /tmp/mt-hyper for grok project hooks`, then `left tracked files alone: .gitignore, CLAUDE.md`, then `installed into /tmp/mt-hyper with --scope hyper; nothing to commit, see .git/info/exclude`, then `exit=0`; no `npm out`/`npm err` before `exit=0`; npm.log holds `npm out` and `npm err`; `grep -A1 mt-hyper $G/trusted_folders.toml` shows `trusted = true`.
3. `git status --porcelain; cat .gitignore CLAUDE.md; ls AGENTS.md package.json sonar-project.properties .claude/settings.json`
   Expected: status prints nothing; the files print `node_modules/` and `team rules`; all four `ls` targets are missing.
4. `sed -n '/# marestail (install --scope hyper)/,/# end marestail/p' .git/info/exclude`
   Expected: one block listing `marestail.toml`, `sonar-project.properties`, `guidance/`, `tasks/`, `features/`, `qa/`, `perf/`, `PERFORMANCE.md`, `.marestail/`, `mutants/`, `.claude/settings.local.json`, `.agents/hooks.json`, `.grok/hooks/marestail-gate.json`, `.cursor/hooks.json`; the git sample comments above it remain.
5. `git check-ignore -v marestail.toml; grep -A8 '^\[ts\]' marestail.toml | grep tooling; grep marestail .claude/settings.local.json`
   Expected: check-ignore names `.git/info/exclude`; `tooling = ".marestail/tooling"`; the local settings carry `marestail gate --hook`.
6. `ls -a .marestail/tooling; cat $F/npm.log; cat .marestail/tooling/package.json; grep -n include .marestail/tooling/tsconfig.json; grep fileName .marestail/tooling/.dependency-cruiser.cjs; grep exclusions .marestail/tooling/sonar-project.properties`
   Expected: `package.json`, `eslint.config.mjs`, `tsconfig.json`, `.dependency-cruiser.cjs`, `stryker.config.json`, `knip.json`, `vitest.config.ts`, `sonar-project.properties`, `node_modules`; one log line `/tmp/mt-hyper install --prefix .marestail/tooling`; package.json matches the feature's pinned text (nine devDependencies incl. `typescript-eslint`, `@vitest/coverage-v8`, `@stryker-mutator/vitest-runner`); include is `"../../client/app/src"` (the template's `[ts] root`); `fileName: "/tmp/mt-hyper/.marestail/tooling/tsconfig.json"`; exclusions end with `,.marestail/**`.
7. `marestail install --scope hyper $T >/dev/null; grep -c '# marestail (install --scope hyper)' .git/info/exclude; git status --porcelain`
   Expected: `1`; status prints nothing.
8. `git add -f marestail.toml && git commit -qm force && git ls-files marestail.toml`
   Expected: prints `marestail.toml`. Then run `cd $M &&
   python -c "from pathlib import Path; from marestail.config import load; from marestail import runner; runner.drop_ignored_since(load(Path('$T')), 'HEAD~1')"` then `cd $T && git ls-files marestail.toml; ls marestail.toml`
   Expected: the untrack step's output contains `dropping gitignored files: marestail.toml`; `ls-files` prints nothing; the file is still on disk.
9. `git worktree add -q /tmp/mt-hyper-wt && touch /tmp/mt-hyper-wt/marestail.toml && git -C /tmp/mt-hyper-wt status --porcelain`
   Expected: prints nothing.
10. `printf '{}\n' > .cursor/hooks.json && git add -f .cursor/hooks.json && git commit -qm cursor && marestail install --scope hyper $T | grep -e 'no Stop hook' -e 'left tracked'; cat .cursor/hooks.json`
    Expected: `left tracked files alone: .cursor/hooks.json, .gitignore, CLAUDE.md`; `no Stop hook for cursor: .cursor/hooks.json is tracked`; the file is still `{}`.
11. `NPM_EXIT=1 marestail install --scope hyper $T > /tmp/mt-npm.out; echo "exit=$?"; tail -1 /tmp/mt-npm.out`
    Expected: last line `npm install --prefix .marestail/tooling failed (exit 1); everything else is installed, see .marestail/tooling/npm.log`; no `installed into` line; `exit=1`.
12. `marestail install --scope hyper /tmp/mt-hyper-plain; echo "exit=$?"; ls -A /tmp/mt-hyper-plain`
    Expected: `marestail install --scope hyper needs a git repository: /tmp/mt-hyper-plain` and no trust line; `exit=1`; nothing listed; `grep -c mt-hyper-plain $G/trusted_folders.toml` prints `0`.
13. Other installs, each into a git repo:
    ```sh
    cd $M && for d in a b c; do rm -rf /tmp/mt-$d && git init -q /tmp/mt-$d && sha1sum /tmp/mt-$d/.git/info/exclude > /tmp/mt-$d.sum; done
    : > $F/npm.log
    marestail install /tmp/mt-a | tail -1; marestail install --gitignore-generated /tmp/mt-b | tail -1; marestail install --scope hard /tmp/mt-c | tail -1
    for d in a b c; do sha1sum -c --quiet /tmp/mt-$d.sum && echo "$d exclude same"; ls -d /tmp/mt-$d/.marestail/tooling 2>&1 | tail -1; done; wc -l < $F/npm.log
    marestail install --help | grep -A1 -- --scope
    ```
    Expected: `installed into /tmp/mt-a; edit marestail.toml and sonar-project.properties`, the same for `/tmp/mt-b`, and `installed into /tmp/mt-c; left CLAUDE.md and AGENTS.md alone; edit marestail.toml and sonar-project.properties`; `a exclude same`, `b exclude same`, `c exclude same`, each `ls` reporting no such file; npm log `0`; choices `{all,changed,hard,hyper}`.
14. `python3 tools/test-install-hyper.py; python3 tools/test-install-hard.py; python3 tools/test-perf.py; echo "exit=$?"`
    Expected: the first two end in a line containing `ok`; test-perf ends `verdict-commit-files: '' != 'perf/bench_x.py'` with `exit=1`.
15. Jest target: `rm -rf /tmp/mt-jest && git init -q /tmp/mt-jest && printf '[ts]\nroot = "web"\nrunner = "jest"\nsources = ["app"]\ntooling = ".marestail/tooling"\n' > /tmp/mt-jest/marestail.toml && marestail install --scope hyper /tmp/mt-jest >/dev/null; ls /tmp/mt-jest/.marestail/tooling; grep -e jest -e vitest /tmp/mt-jest/.marestail/tooling/package.json /tmp/mt-jest/.marestail/tooling/stryker.config.json; grep -e types -e typeRoots -e include /tmp/mt-jest/.marestail/tooling/tsconfig.json`
    Expected: no `vitest.config.ts` and no `jest.config.*` listed; the only jest/vitest matches are `@stryker-mutator/jest-runner` in package.json and `"testRunner": "jest"`, `"plugins": ["@stryker-mutator/jest-runner"]` and `"jest": { "projectType": "custom" }` in the Stryker config, which matches the feature's pinned jest text; the tsconfig shows `"typeRoots": ["../../web/node_modules/@types"]` and `"include": ["../../web/app"]` and no `"types"` line.
16. Kept files: `cd $T && printf 'ours\n' > PERFORMANCE.md && mkdir -p .claude && printf '{}\n' > .claude/settings.local.json && git add -f PERFORMANCE.md .claude/settings.local.json && git commit -qm ours && marestail install --scope hyper $T; cat PERFORMANCE.md .claude/settings.local.json; git status --porcelain`
    Expected: no trust line (step 2 already trusted `$T`); stdout lines `left tracked files alone: .claude/settings.local.json, .cursor/hooks.json, .gitignore, CLAUDE.md, PERFORMANCE.md`, `no Stop hook for claude: .claude/settings.local.json is tracked`, `no Stop hook for cursor: .cursor/hooks.json is tracked`, then the `installed into` line; the files print `ours` and `{}`; status prints nothing.
17. Tooling-less config: `rm -rf /tmp/mt-old && git init -q /tmp/mt-old && printf '[ts]\nroot = "."\n' > /tmp/mt-old/marestail.toml && marestail install --scope hyper /tmp/mt-old; cat /tmp/mt-old/marestail.toml`
    Expected: first line `trusted /tmp/mt-old for grok project hooks` (a new folder); stdout has `marestail.toml has no [ts] tooling; add tooling = ".marestail/tooling" under [ts] so the gates use it` just before the last `installed into /tmp/mt-old ...` line; the file is still `[ts]` / `root = "."`.
18. Tracked Sonar file: `rm -rf /tmp/mt-sonar && git init -q -b main /tmp/mt-sonar && cd /tmp/mt-sonar && git config user.email q@a && git config user.name qa && mkdir web && printf 'sonar.projectKey=ours\n' > sonar-project.properties && printf 'export default [];\n' > web/eslint.config.js && git add . && git commit -qm seed && marestail install --scope hyper /tmp/mt-sonar; cat sonar-project.properties; ls .marestail/tooling/sonar-project.properties; git status --porcelain`
    Expected: first line `trusted /tmp/mt-sonar for grok project hooks` (a new folder); then exactly `left tracked files alone: sonar-project.properties` (no `web/eslint.config.js`) and the `installed into /tmp/mt-sonar with --scope hyper; nothing to commit, see .git/info/exclude` line; the file prints `sonar.projectKey=ours`; `ls` reports no such file; status prints nothing.
19. `cd $M && grep -n -A20 'Installing into a repository that does not use marestail' README.md`
    Expected: the section shows `marestail install --scope hyper`, names `.git/info/exclude` and `.marestail/tooling`, says a reviewer sees only the fix and its tests, and says Python venv and tool lookups are unchanged for now.
