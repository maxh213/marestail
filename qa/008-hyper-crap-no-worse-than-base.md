# QA procedure: under `--scope hyper`, a fix in a complex legacy function passes CRAP when it is no worse than base

`M` is the marestail-green checkout; run `python3 $M/marestail/cli.py` as `marestail`. File contents are verbatim from the feature Background. `G` means `marestail gate --tier full --only py.tests,py.crap`.

1. Build the Python fixture as in step 2 of `qa/007-gate-scope-hyper-changed-lines-only.md`, but before the base commit also write `app/payments.py` (Background, 28 lines). Commit, `git tag base`, `git checkout -qb work`.
   Expected: `.venv/bin/radon cc -s app/payments.py` shows `F 1:0 load_payment_popup - A (4)` and `F 9:4 receive_message - B (9)`.
2. Write the paid test to `tests/test_payments.py`. `sed -i '15s/.*/            return "thank you"/' app/payments.py && G --scope hyper; echo "exit=$?"`
   Expected: `[ok  ] py.crap` with `1 innermost changed functions, 1 above CRAP 4, 1 of them no worse than base`; no line contains `load_payment_popup`.
3. `G --scope changed; echo "exit=$?"`
   Expected: `[FAIL] py.crap` with `2 functions, 2 above CRAP 4 on changed functions`, then `app/payments.py:9 receive_message crap=48.6 (cc=9, coverage=21%)` and `app/payments.py:1 load_payment_popup crap=5.3 (cc=4, coverage=57%)`; `exit=1`.
4. `sed -i '14s/.*/        if data.get("status") == "paid" or data.get("status") == "donated":/' app/payments.py && G --scope hyper`
   Expected: `[FAIL] py.crap` with `1 innermost changed functions, 1 above CRAP 4, 0 of them no worse than base` and the one finding `app/payments.py:9 receive_message complexity rose from 9 to 10; move the new condition into its own function`.
5. `sed -i '14s/.*/        if has_donated(data):/' app/payments.py && printf '\n\ndef has_donated(data):\n    status = data.get("status")\n    return status in ("paid", "donated")\n' >> app/payments.py && G --scope hyper`
   Expected: `[ok  ] py.crap` with `2 innermost changed functions, 1 above CRAP 4, 1 of them no worse than base`; no line contains `load_payment_popup`.
6. `git checkout -- app/payments.py`, then append the `fee_band` block from the feature (lines 29 to 42) and run `G --scope hyper`.
   Expected: `[FAIL] py.crap` with `1 innermost changed functions, 1 above CRAP 4, 0 of them no worse than base` and the one finding `app/payments.py:31 fee_band crap=42.0 (cc=6, coverage=0%)`.
7. `git checkout -- app/payments.py && sed -i '15s/.*/            return "thank you"/; 21s/.*/            return "on hold"/' app/payments.py && G --scope hyper`
   Expected: `[FAIL] py.crap`, summary as step 6, one finding `app/payments.py:9 receive_message crap=48.6 (cc=9, coverage=21%); changed lines not covered: 21`; no line contains `complexity rose`.
8. `git checkout -- app/payments.py && sed -i '9s/.*/    def on_message(data):/; 15s/.*/            return "thank you"/; 28s/.*/    return on_message/' app/payments.py && G --scope hyper`
   Expected: `[FAIL] py.crap` with the one finding `app/payments.py:9 on_message crap=48.6 (cc=9, coverage=21%)`; no line contains `load_payment_popup`.
9. In `$M`: `grep -n 'Coverage and CRAP work as under' README.md; sed -n '/--scope hyper. gates/p' README.md`
   Expected: the grep prints nothing; the hyper paragraph has one sentence saying only the innermost function holding a changed line is gated, and one saying it passes when CRAP is at most `crap_max` or its complexity is no higher than at `[git] base` with every changed line covered.
10. In `$M`: `python3 tools/test-scope-hyper.py; python3 tools/test-scope-hard.py`
    Expected: last lines `hyper scope ok` and `hard scope ok`.
