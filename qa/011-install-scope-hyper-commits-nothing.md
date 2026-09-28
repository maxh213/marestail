# QA procedure: `marestail install --scope hyper` commits nothing

Run from the marestail-green repo root with `.venv` active and `bin/` on PATH.

1. Build the target and a fake npm:
   ```sh
   export T=/tmp/mt-hyper G=/tmp/mt-hyper-grok F=/tmp/mt-hyper-bin
   rm -rf $T $G $F /tmp/mt-hyper-wt /tmp/mt-hyper-plain && mkdir -p $T $G $F /tmp/mt-hyper-plain
   printf '#!/bin/sh\necho "$PWD $*" >> %s/npm.log\nmkdir -p .marestail/tooling/node_modules/.bin\nexit "${NPM_EXIT:-0}"\n' $F > $F/npm && chmod +x $F/npm
   export PATH=$F:$PATH GROK_HOME=$G
   cd $T && git init -q -b main && git config user.email q@a && git config user.name qa
   printf 'node_modules/\n' > .gitignore && printf 'team rules\n' > CLAUDE.md && git add . && git commit -qm seed
   ```
   Expected: `git status --porcelain` prints nothing.
2. `marestail install --scope hyper $T; echo "exit=$?"`
   Expected: `exit=0`; stdout has `left tracked files alone: .gitignore, CLAUDE.md`; last line `installed into /tmp/mt-hyper with --scope hyper; nothing to commit, see .git/info/exclude`.
3. `git status --porcelain; cat .gitignore CLAUDE.md; ls AGENTS.md package.json sonar-project.properties .claude/settings.json`
   Expected: status prints nothing; the files print `node_modules/` and `team rules`; all four `ls` targets are missing.
4. `sed -n '/# marestail (install --scope hyper)/,/# end marestail/p' .git/info/exclude`
   Expected: one block listing `marestail.toml`, `sonar-project.properties`, `guidance/`, `tasks/`, `features/`, `qa/`, `perf/`, `PERFORMANCE.md`, `.marestail/`, `mutants/`, `.claude/settings.local.json`, `.agents/hooks.json`, `.grok/hooks/marestail-gate.json`, `.cursor/hooks.json`; the git sample comments above it remain.
5. `git check-ignore -v marestail.toml; grep -A8 '^\[ts\]' marestail.toml | grep tooling; grep marestail .claude/settings.local.json`
   Expected: check-ignore names `.git/info/exclude`; `tooling = ".marestail/tooling"`; the local settings carry `marestail gate --hook`.
6. `ls -a .marestail/tooling; cat $F/npm.log`
   Expected: `package.json`, `eslint.config.mjs`, `tsconfig.json`, `.dependency-cruiser.cjs`, `stryker.config.json`, `knip.json`, `vitest.config.ts`, `sonar-project.properties`, `node_modules`; one log line `/tmp/mt-hyper install --prefix .marestail/tooling`.
7. `marestail install --scope hyper $T >/dev/null; grep -c '# marestail (install --scope hyper)' .git/info/exclude; git status --porcelain`
   Expected: `1`; status prints nothing.
8. `git add -f marestail.toml && git commit -qm force && git ls-files marestail.toml`
   Expected: prints `marestail.toml`. Then from the marestail-green root run
   `python -c "from pathlib import Path; from marestail.config import load; from marestail import runner; runner.drop_ignored_since(load(Path('$T')), 'HEAD~1')"` and in $T `git ls-files marestail.toml; ls marestail.toml`
   Expected: the untrack step prints `dropping gitignored files: marestail.toml`; `ls-files` prints nothing; the file is still on disk.
9. `git worktree add -q /tmp/mt-hyper-wt && touch /tmp/mt-hyper-wt/marestail.toml && git -C /tmp/mt-hyper-wt status --porcelain`
   Expected: prints nothing.
10. `printf '{}\n' > .cursor/hooks.json && git add -f .cursor/hooks.json && git commit -qm cursor && marestail install --scope hyper $T | grep -e 'no Stop hook' -e 'left tracked'; cat .cursor/hooks.json`
    Expected: `left tracked files alone: .cursor/hooks.json, .gitignore, CLAUDE.md`; `no Stop hook for cursor: .cursor/hooks.json is tracked`; the file is still `{}`.
11. `NPM_EXIT=1 marestail install --scope hyper $T > /tmp/mt-npm.out; echo "exit=$?"; tail -1 /tmp/mt-npm.out`
    Expected: `npm install --prefix .marestail/tooling failed (exit 1); everything else is installed`; exit 1.
12. `marestail install --scope hyper /tmp/mt-hyper-plain; echo "exit=$?"; ls -A /tmp/mt-hyper-plain`
    Expected: `marestail install --scope hyper needs a git repository: /tmp/mt-hyper-plain`; `exit=1`; nothing listed.
13. `cd - && rm -rf /tmp/mt-full && mkdir /tmp/mt-full && marestail install --scope hard /tmp/mt-full | tail -1; ls /tmp/mt-full/.marestail 2>&1; marestail install --help | grep -A1 -- --scope`
    Expected: `installed into /tmp/mt-full; left CLAUDE.md and AGENTS.md alone; edit marestail.toml and sonar-project.properties`; no `.marestail`; choices `{all,changed,hard,hyper}`.
14. `python3 tools/test-install-hyper.py; python3 tools/test-install-hard.py; python3 tools/test-perf.py; echo "exit=$?"`
    Expected: the first two end in a line containing `ok`; test-perf ends `verdict-commit-files: '' != 'perf/bench_x.py'` with `exit=1`.
15. `grep -n -A20 'Installing into a repository that does not use marestail' README.md`
    Expected: the section shows `marestail install --scope hyper`, names `.git/info/exclude` and `.marestail/tooling`, says a reviewer sees only the fix and its tests, and says Python venv and tool lookups are unchanged for now.
