# QA procedure: bring marestail itself through its own gate

1. `cd /home/max/workspace/marestail-green`
2. `git status`  
   Expected: working tree is clean.
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
    Expected: stdout ends with `installed into /tmp/marestail-install-check; edit marestail.toml and sonar-project.properties` (if Grok has not yet trusted the target, a preceding `trusted ... for grok project hooks` line is also printed). Creates `marestail.toml`, `sonar-project.properties`, `tasks/README.md`, `PERFORMANCE.md`, `guidance/ts.md`, plus `CLAUDE.md` and `AGENTS.md` containing `marestail gate`.
15. `unset MARESTAIL_DANDELION; PATH=/usr/bin:/bin; ./bin/marestail route 2>&1; echo "exit=$?"`  
    Expected: exit code `127`, stderr contains `dandelion is not installed` and `https://github.com/maxh213/dandelion`.
16. `./bin/marestail depth`  
    Expected: exit code `0`, output contains a table with columns `module`, `public`, `stmts`, `ratio`, `lines`.
17. For each script in `tools/test-agent-backends.py`, `tools/test-audit.py`, `tools/test-csproj-additions.py`, `tools/test-drop-ignored.py`, `tools/test-route.py`, `tools/test-scope-hard.py`, `tools/test-sonar-worktree.py`, `tools/test-perf-db.py`, `tools/test-practices.py`:  
    `python3 <script>`  
    Expected: each exits `0` and the last line of output contains `ok`.  
    Then `python3 tools/test-perf.py; echo "exit=$?"`  
    Expected: exits `1` with last output line `verdict-commit-files: '' != 'perf/bench_x.py'`, exactly as before the refactor (its stub agent predates the two-phase perf step; `tools/` is out of scope).
18. `grep -R -E "#[^!]|\"\"\"|'''" marestail/ --include='*.py' | grep -v '^Binary' || true`  
    Expected: no matches that are comments or docstrings (shebangs and string literals are allowed).
19. `grep -A 200 '^## Environment variables' README.md`  
    Expected: the section exists and contains every variable listed in `features/000-green-the-repo.feature`.
20. `grep -E '\b(guidance/ruby\.md|missing path example)\b' README.md`  
    Expected: either the reference is removed or the file exists; the `docs` gate from step 3 passing covers this.
21. `rm -rf /tmp/marestail-hermetic-bin /tmp/marestail-hermetic-home && mkdir /tmp/marestail-hermetic-bin /tmp/marestail-hermetic-home && ln -s "$(command -v git)" "$(command -v sh)" /tmp/marestail-hermetic-bin/ && unshare -r -n env -i HOME=/tmp/marestail-hermetic-home PATH=/tmp/marestail-hermetic-bin .venv/bin/pytest tests`  
    Expected: exit code `0`, no test failed or errored. `env -i` clears every agent and tool override (`MARESTAIL_AGENT`, `MARESTAIL_AGY`, `MARESTAIL_CLAUDE`, `MARESTAIL_CURSOR`, `MARESTAIL_DANDELION`, `MARESTAIL_GROK`, `MARESTAIL_KILO`, `MARESTAIL_KIMI`, `MARESTAIL_SONAR_PASSWORD`, `CLAUDE_CONFIG_DIR`, `GROK_HOME`, `JAVA_HOME`); PATH holds only `git` and `sh`, so docker, every agent CLI and the sonar scanner must be faked; there is no network.
22. `python3 -c "import ast, glob, sys; targets = ['marestail/runner.py', 'marestail/install.py', 'marestail/context.py'] + glob.glob('marestail/gates/*.py'); bad = [f'{p}:{n.name}({getattr(n, \"end_lineno\", 0) - n.lineno + 1} lines)' for p in sorted(targets) for n in ast.walk(ast.parse(open(p).read())) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and getattr(n, 'end_lineno', 0) - n.lineno + 1 > 30 and n.name != 'registry']; print('\n'.join(bad) if bad else 'all orchestration functions <= 30 lines'); sys.exit(1 if bad else 0)"`  
    Expected: exit code `0`, prints `all orchestration functions <= 30 lines`; proves long orchestration functions in `runner.py`, `install.py`, `context.py`, and the gates have been split into small named helpers.  
    `registry` in `marestail/gates/__init__.py` is exempt: it is a flat table of `Gate` declarations with no branches or calls to split out.
23. `./bin/marestail sonar up && ./bin/marestail sonar setup`, then `./bin/marestail gate --tier sonar; echo "exit=$?"`  
    Expected: `exit=0`, last gate line `GATE PASSED`; result lines, in order: `py.tests`, `py.crap`, `py.lint`, `py.deps`, `py.runtime`, `comments`, `depth`, `deadcode`, `docs`, `sonar`, all `[ok  ]`.
24. `./bin/marestail gate --tier full; echo "exit=$?"` and `./bin/marestail gate --tier all; echo "exit=$?"`  
    Expected: each prints `exit=0` and ends with `GATE PASSED`; result lines, in order: `py.tests`, `py.crap`, `py.lint`, `py.deps`, `py.runtime`, `comments`, `depth`, `deadcode`, `docs`, `py.mutation`, `sonar`.
25. `./bin/marestail gate --tier qa`  
    Expected: exits `0`, ends with `GATE PASSED`; result lines are the nine fast gates of step 23 without `sonar`, and no `qa` line (marestail.toml has no `[qa]` section, so the gate is dropped).
26. In the output of steps 23 to 25, find the `py.runtime` line.  
    Expected: `[ok  ] py.runtime     skipped: nothing declares the interpreter that ships  (0.0s)`.
