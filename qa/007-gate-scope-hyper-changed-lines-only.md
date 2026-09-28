# QA procedure: `marestail gate --scope hyper` gates only the changed lines

`M` is the marestail-green checkout; run `python3 $M/marestail/cli.py` as `marestail`. File contents are verbatim from the feature Background.

1. In `$M`: `marestail gate --help; marestail run --help`
   Expected: both list `--scope {all,changed,hard,hyper}`.
2. Python fixture: `rm -rf /tmp/hyper && mkdir -p /tmp/hyper/app /tmp/hyper/tests && cd /tmp/hyper && git init -q -b main`. Write `app/legacy.py` and `tests/test_legacy.py` from the Background, an empty `app/__init__.py`, the Background's `marestail.toml` and `pyproject.toml` (ruff `select = ["E", "F", "I"]`, pytest `testpaths = ["tests"]`), and a `.gitignore` of `.venv/ .marestail/ .mypy_cache/ .ruff_cache/ .coverage __pycache__/ mutants/`. `uv venv .venv && uv pip install --python .venv/bin/python pytest pytest-cov radon ruff mypy vulture import-linter mutmut`. `git add -A && git commit -qm base && git tag base && git checkout -qb work`.
   Expected: `.venv/bin/ruff check --output-format concise app` prints I001 and F401 at `app/legacy.py:1`.
3. `sed -i '6s/.*/    return 2 * price/' app/legacy.py && marestail gate --tier full --scope hyper; echo "exit=$?"`
   Expected: first line `scope: hyper: 1 changed lines in 1 files`; no line contains `app/legacy.py:1`, `:2`, `:14` or `:17`; last line `GATE PASSED`; `exit=0`.
4. `marestail gate --tier full --scope hyper --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["scope"], d["focus"])'`
   Expected: `hyper []`.
5. `marestail gate --tier full --scope changed; echo "exit=$?"`
   Expected: first line `scope: changed (1 files, 1 lines)`; comments lists `app/legacy.py:2 comment: # legacy note`; py.lint lists the I001, F401 and `format:   --> app/legacy.py:14:13` lines; depth lists `app/legacy.py:17 relay only forwards its arguments`; py.mutation lists `x_code__mutmut_` survivors; `exit=1`.
6. `marestail gate --tier full --scope hard --focus app/legacy.py; echo "exit=$?"`
   Expected: comments lists `app/legacy.py:2 comment: # legacy note`; `exit=1`.
7. `sed -i '6s/.*/    return 2 * price  # doubled/' app/legacy.py && marestail gate --tier full --scope hyper --only comments`
   Expected: comments `[FAIL]` with one finding, `app/legacy.py:6 comment: # doubled`.
8. `sed -i '6s/.*/    return 2 * price if price != None else 0/' app/legacy.py && marestail gate --tier full --scope hyper --only py.lint`
   Expected: py.lint `[FAIL]`, summary `1 problems`, one finding: ``ruff: app/legacy.py:6:34: E711 Comparison to `None` should be `cond is not None` ``.
9. `git checkout app/legacy.py && printf '\n\ndef forward(n):\n    return label(n)\n' >> app/legacy.py && marestail gate --tier full --scope hyper --only depth,deadcode`
   Expected: depth has only `app/legacy.py:21 forward only forwards its arguments`; deadcode has only `app/legacy.py:21 unused function 'forward'`. With `--scope changed`, `app/legacy.py:17` and `app/legacy.py:5` findings appear too.
10. `git checkout app/legacy.py && sed -i '10s/.*/    return "item: " + name/' app/legacy.py && marestail gate --tier full --scope hyper --only py.mutation`
    Expected: `[FAIL]`, summary `2 of 3 mutants not killed`, findings `app.legacy.x_label__mutmut_2: survived` and `app.legacy.x_label__mutmut_3: survived` only. With `--scope changed`: `4 of 8 mutants not killed`.
11. `git checkout app/legacy.py && sed -i '6s/.*/    return 2 * price/' app/legacy.py && mv .venv/bin/ruff /tmp/ruff.bak && marestail gate --tier full --scope hyper --only py.lint; echo "exit=$?"; mv /tmp/ruff.bak .venv/bin/ruff`
    Expected: py.lint `[FAIL]`, `2 problems`, findings `ruff: /tmp/hyper/.venv/bin/ruff: not found (...)` and `format: /tmp/hyper/.venv/bin/ruff: not found (...)`; no `file-level`; `exit=1`.
12. `echo '{}' | MARESTAIL_SCOPE=hyper MARESTAIL_FOCUS= marestail gate --hook` after `sed -i '6s/.*/    return 2 * price  # doubled/' app/legacy.py`
    Expected: output contains `app/legacy.py:6 comment: # doubled` and not `app/legacy.py:2`.
13. Write `/tmp/envagent` (`#!/bin/sh` then `env | grep ^MARESTAIL_ > /tmp/hyper-env.txt`), `chmod +x` it, write `/tmp/t.md` with any one-line task, then, still in `/tmp/hyper`, `MARESTAIL_CLAUDE=/tmp/envagent marestail run --scope hyper --to specifier /tmp/t.md` and `cat /tmp/hyper-env.txt`.
    Expected: contains `MARESTAIL_SCOPE=hyper` and the line `MARESTAIL_FOCUS=`. The run itself may then stop because the agent wrote nothing.
14. `git checkout app/legacy.py && marestail gate --tier full --scope hyper; echo "exit=$?"`
    Expected: first line `scope: hyper: 0 changed lines in 0 files`; py.lint `skipped: no changed python files`; py.mutation `skipped: no changed python sources`; `GATE PASSED`; `exit=0`.
15. `marestail gate --scope hyper --focus app/legacy.py; echo "exit=$?"`
    Expected: stderr `--focus cannot be combined with --scope hyper; hyper gates the diff and nothing else`; `exit=2`.
16. TypeScript fixture in `/tmp/hyperts`: write the Background's `src/legacy.ts`, `src/legacy.test.ts`, `eslint.config.js`, `stryker.config.json`, `marestail.toml`, the `tsconfig.app.json`, `package.json` and `vitest.config.ts` from `tools/samples/scope-ts.sh`, and `.gitignore` of `node_modules/ .marestail/ reports/ .stryker-tmp/ coverage/`. `npm install -D vitest @vitest/coverage-v8 typescript@5 eslint @stryker-mutator/core @stryker-mutator/vitest-runner`, then commit everything including `package-lock.json`, `git tag base`, `git checkout -qb work`.
    Expected: `npx eslint src --format json` reports `no-unused-vars` at line 1.
17. `sed -i '6s/.*/  return 2 * price;/' src/legacy.ts && marestail gate --tier full --scope hyper`
    Expected: `scope: hyper: 1 changed lines in 1 files`; no `src/legacy.ts:1`, `:2`, `:14`; `GATE PASSED`. With `--scope changed`, comments lists `src/legacy.ts:2 comment: // legacy note` and the gate fails.
18. `sed -i '6s/.*/  return price == null ? 0 : 2 * price;/' src/legacy.ts && marestail gate --tier full --scope hyper --only ts.lint`
    Expected: one finding, `src/legacy.ts:6 eqeqeq: Expected '===' and instead saw '=='.`
19. `git checkout src/legacy.ts && sed -i '10s/.*/  return "item: " + name;/' src/legacy.ts && marestail gate --tier full --scope hyper --only ts.mutation`
    Expected: `1 surviving mutants`, only `src/legacy.ts:10 StringLiteral Survived: ""`. `reports/mutation/mutation.json` lists only line-10 mutants, which shows stryker got `--mutate src/legacy.ts:10-10`.
20. Sonar cannot run here without a SonarQube server. In `$M`: `python3 tools/test-scope-hyper.py; python3 tools/test-scope-hard.py`
    Expected: both exit 0 with a last line containing `ok`; the hyper output includes its stubbed Sonar check passing (line filter, `1 file-level findings not gated under hyper`, `duplication not gated under hyper`).
21. `grep -n -A6 'scope hyper' README.md`
    Expected: the scope section describes hyper next to `changed` and `hard`, with one line saying how the three differ.
