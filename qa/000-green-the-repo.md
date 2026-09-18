# QA procedure: bring marestail itself through its own gate

1. `cd /home/max/workspace/marestail-green`
2. `git status`  
   Expected: working tree is clean except for the new `features/000-green-the-repo.feature` and `qa/000-green-the-repo.md`.
3. `./bin/marestail gate --tier full`  
   Expected: exit code `0`, final line `GATE PASSED`, no `[FAIL]` lines.
4. `./bin/marestail gate`  
   Expected: exit code `0`, final line `GATE PASSED`.
5. `./bin/marestail gate --tier fast --json | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["scope"], len(d["results"]))'`  
   Expected: prints `all <N>` where `N` > 0 and at least one result has `"gate": "py.tests"`.
6. `./bin/marestail gate --tier fast --scope changed --focus marestail`  
   Expected: exit code `0`, output contains `scope: changed` and final line `GATE PASSED`.
7. `./bin/marestail gate --tier fast --scope hard --focus marestail/cli.py`  
   Expected: exit code `0`, output contains `scope: hard: marestail/cli.py` and final line `GATE PASSED`.
8. `./bin/marestail gate --tier fast --scope all`  
   Expected: exit code `0`, output contains `scope: all` and final line `GATE PASSED`.
9. `./bin/marestail gate --tier fast --scope all --focus marestail 2>&1; echo "exit=$?"`  
   Expected: exit code `2`, stderr contains `--focus cannot be combined with --scope all`.
10. `printf '# a comment\npass\n' > marestail/_deliberate_comment.py && ./bin/marestail gate --tier fast --only comments; CODE=$?; rm -f marestail/_deliberate_comment.py; echo "exit=$CODE"`  
    Expected: while the file exists the run exits `1`, output contains `marestail/_deliberate_comment.py:1 comment:` and ends with `GATE FAILED: comments`. After removal the file is gone.
11. `./bin/marestail --help`  
    Expected: lists `gate`, `run`, `install`, `sonar`, `watch`, `perf`, `route`, `graph`, `depth`.
12. `./bin/marestail gate --help`  
    Expected: lists `--tier`, `--scope`, `--focus`, `--only`, `--json`, `--hook`.
13. `./bin/marestail run --help`  
    Expected: lists `--from`, `--to`, `--auto`, `--scope`, `--focus`, `--model`, `--retries`, `--effort`, `--agent`.
14. `rm -rf /tmp/marestail-install-check && mkdir /tmp/marestail-install-check && ./bin/marestail install /tmp/marestail-install-check`  
    Expected: prints exactly `installed into /tmp/marestail-install-check; edit marestail.toml and sonar-project.properties` and creates `marestail.toml`, `sonar-project.properties`, `tasks/README.md`, `PERFORMANCE.md`, `guidance/ts.md`, plus `CLAUDE.md` and `AGENTS.md` containing `marestail gate`.
15. `unset MARESTAIL_DANDELION; PATH=/usr/bin:/bin; ./bin/marestail route 2>&1; echo "exit=$?"`  
    Expected: exit code `127`, stderr contains `dandelion is not installed` and `https://github.com/maxh213/dandelion`.
16. `./bin/marestail depth`  
    Expected: exit code `0`, output contains a table with columns `module`, `public`, `stmts`, `ratio`, `lines`.
17. For each script in `tools/test-agent-backends.py`, `tools/test-audit.py`, `tools/test-csproj-additions.py`, `tools/test-drop-ignored.py`, `tools/test-route.py`, `tools/test-scope-hard.py`, `tools/test-sonar-worktree.py`, `tools/test-perf.py`, `tools/test-perf-db.py`, `tools/test-practices.py`:  
    `python3 <script>`  
    Expected: each exits `0` and the last line of output contains `ok`.
18. `grep -R -E '#[^!]|"""|''' marestail/ --include='*.py' | grep -v '^Binary' || true`  
    Expected: no matches that are comments or docstrings (shebangs and string literals are allowed).
19. `grep -A 200 '^## Environment variables' README.md`  
    Expected: the section exists and contains every variable listed in `features/000-green-the-repo.feature`.
20. `grep -E '\b(guidance/ruby\.md|missing path example)\b' README.md`  
    Expected: either the reference is removed or the file exists; the `docs` gate from step 3 passing covers this.
