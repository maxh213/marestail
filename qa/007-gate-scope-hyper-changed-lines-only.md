# QA procedure: `marestail gate --scope hyper` gates only the changed lines

1. `cd` to the marestail-green repo root with `.venv` active.
   Expected: `marestail --help` works.
2. Build the Python fixture: `rm -rf /tmp/hyper && mkdir -p /tmp/hyper/app /tmp/hyper/tests && cd /tmp/hyper && git init -q -b main && git config user.email q@a && git config user.name qa`. Write `app/legacy.py` exactly as in the feature Background, `app/__init__.py` empty, `tests/test_legacy.py` asserting `double(2) == 4` and `double(3) == 6` and calling `label("x")` and `code(1)`, a `pyproject.toml` with `pythonpath = ["."]`, a `marestail.toml` with `[git] base = "base"`, `[python] root = "."`, `sources = ["app"]`, and a `.venv` holding pytest, pytest-cov, radon, ruff, mypy, vulture, import-linter and mutmut. `git add -A && git commit -qm base && git tag base`.
   Expected: `marestail gate --tier full` fails on comments (`app/legacy.py:2`), py.lint and py.mutation.
3. `sed -i '6s/.*/    return 2 * price/' app/legacy.py && marestail gate --tier full --scope hyper; echo "exit=$?"`
   Expected: first line `scope: hyper: 1 changed lines in 1 files`; py.lint is ok with `1 file-level findings not gated under hyper` in its summary; no line mentions `app/legacy.py:1`, `:2`, `:10` or `:14`; last line `GATE PASSED`; `exit=0`.
4. `marestail gate --tier full --scope changed; echo "exit=$?"`
   Expected: comments lists `app/legacy.py:2 comment: # legacy note`; py.lint lists `format: Would reformat: app/legacy.py`; `exit=1`.
5. `marestail gate --tier full --scope hard --focus app/legacy.py; echo "exit=$?"`
   Expected: comments lists `app/legacy.py:2`; `exit=1`.
6. `sed -i '6s/.*/    return 2 * price  # doubled/' app/legacy.py && marestail gate --tier full --scope hyper --only comments`
   Expected: comments FAIL with exactly one finding, `app/legacy.py:6 comment: # doubled`.
7. `sed -i '6s/.*/    return 2 * price if price != None else 0/' app/legacy.py && marestail gate --tier full --scope hyper --only py.lint`
   Expected: py.lint FAIL with exactly one finding, containing `app/legacy.py:6:` and `E711`; nothing mentions `F401`.
8. `git checkout app/legacy.py && sed -i '10s/.*/    return "item: " + name/' app/legacy.py && marestail gate --tier full --scope hyper --only py.mutation`
   Expected: FAIL; every listed mutant contains `x_label__mutmut_`; none contains `x_code__` or `x_double__`. Re-run with `--scope changed`: `x_code__mutmut_` mutants appear as well.
9. `mv .venv/bin/ruff /tmp/ruff.bak && marestail gate --tier full --scope hyper --only py.lint; echo "exit=$?"; mv /tmp/ruff.bak .venv/bin/ruff`
   Expected: py.lint `[FAIL]`; `exit=1`.
10. `git checkout app/legacy.py && marestail gate --tier full --scope hyper`
    Expected: py.lint `skipped: no changed python files`, py.mutation `skipped: no changed python sources`, the same as with `--scope changed`.
11. `marestail gate --scope hyper --focus app/legacy.py; echo "exit=$?"`
    Expected: stderr `--focus cannot be combined with --scope hyper; hyper gates the diff and nothing else`; `exit=2`.
12. `sed -i '6s/.*/    return 2 * price  # doubled/' app/legacy.py && echo '{}' | MARESTAIL_SCOPE=hyper marestail gate --hook`
    Expected: output names `app/legacy.py:6 comment: # doubled` and not `app/legacy.py:2`.
13. Repeat steps 3 to 6 on a TypeScript fixture shaped like the feature Background (`src/legacy.ts`, vitest, eslint, stryker). Then change only line 10 and run `--only ts.mutation`.
    Expected: the same results as for Python; the ts.mutation run mutates only `src/legacy.ts:10-10`, and only mutants that start on line 10 are listed.
14. Back in the marestail-green root: `marestail gate --help`
    Expected: `--scope` choices are `all`, `changed`, `hard` and `hyper`.
15. `python3 tools/test-scope-hyper.py; python3 tools/test-scope-hard.py`
    Expected: both exit 0 with a last line containing `ok`.
16. `grep -n 'scope hyper' README.md`
    Expected: the scope section describes hyper next to `changed` and `hard`, with one line saying how the three differ.
