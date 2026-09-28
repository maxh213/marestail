Feature: under `--scope hyper`, a fix inside a complex legacy function passes CRAP when it makes the function no worse

  A developer who fixes one line inside a function that was already too complex runs
  `marestail gate --scope hyper` and passes CRAP, provided the function's complexity did not rise
  and the lines they changed are covered.

  Under hyper only, every CRAP gate (py, ts, rb, rs, cs, java, ex, er) does this:
    1. It gates only the innermost function holding a changed line. A function whose range strictly
       contains another function that holds a changed line is dropped: not scored, not counted, not reported.
    2. A gated function passes when its CRAP is at most `crap_max`, or when all three hold:
       it exists at `[git] base`, its cyclomatic complexity at HEAD is no higher than at base, and every changed
       line inside it that the coverage report measures is covered (lines coverage does not measure count as covered).
    3. Base complexity comes from `git show <base>:<path>` run through the same complexity scanner. A function
       is matched by file, name and nesting path (`load_payment_popup.receive_message`). A function whose file,
       name or nesting path differs from base, or whose file is new, has no base and must meet `crap_max`.
    4. Findings, sorted by CRAP descending as today:
       - complexity rose: `<file>:<line> <name> complexity rose from <base cc> to <head cc>; move the new condition into its own function`
       - otherwise the text used today, `<file>:<line> <name> crap=<c> (cc=<n>, coverage=<p>)`, and when the function
         has a base and a changed line is uncovered, followed by `; changed lines not covered: <lines, comma separated>`.
    5. Summary: `<G> innermost changed functions, <A> above CRAP <limit>, <K> of them no worse than base`.
       The gate fails when A > K.
    6. A gate whose scanner cannot scan a base file appends `; no base complexity, crap_max only` to that summary and
       judges every gated function by `crap_max` alone.
  Under `all`, `changed` and `hard` the CRAP gates, their findings, `crap_max` and its config keys, and the scanners'
  complexity numbers are unchanged.

  Background:
    Given the Python fixture of `tools/test-scope-hyper.py` (`[git] base = "base"`, `crap_max` unset so the limit is 4)
    And its base commit also holds `app/payments.py`, lines 1 to 28 verbatim, with no test calling it:
      """
      def load_payment_popup(settings):
          if not settings:
              return None
          if settings.get("sandbox"):
              settings = {**settings, "origin": "https://sandbox.example"}
          if settings.get("debug"):
              print("popup ready")

          def receive_message(data):
              if data is None:
                  return "ignored"
              if data.get("origin") != settings["origin"]:
                  return "foreign"
              if data.get("status") == "paid":
                  return "thanks"
              if data.get("status") == "failed":
                  return "retry"
              if data.get("status") == "cancelled":
                  return "closed"
              if data.get("status") == "pending":
                  return "waiting"
              if data.get("amount", 0) > 1000:
                  return "review"
              if data.get("recurring"):
                  return "monthly"
              return "unknown"

          return receive_message
      """
    And radon scores `load_payment_popup` (line 1) at complexity 4 and `receive_message` (line 9) at complexity 9
    And "the paid test" means `tests/test_payments.py` in the work tree holding:
      """
      from app.payments import load_payment_popup


      def test_paid():
          receive = load_payment_popup({"origin": "https://pay.example"})
          assert receive({"origin": "https://pay.example", "status": "paid"}) == "thank you"
      """
    And every gate run below is `marestail gate --tier full --only py.tests,py.crap` with the scope named

  Scenario: a covered fix that adds no branch passes under hyper and fails under changed
    Given line 15 is `            return "thank you"` and the paid test is present
    When the gate runs with `--scope hyper`
    Then py.crap is `[ok  ]` with summary `1 innermost changed functions, 1 above CRAP 4, 1 of them no worse than base`
    And no output line contains `load_payment_popup`
    When the gate runs with `--scope changed`
    Then py.crap is `[FAIL]` with summary `2 functions, 2 above CRAP 4 on changed functions` and exactly these findings, in order:
      | app/payments.py:9 receive_message crap=48.6 (cc=9, coverage=21%)   |
      | app/payments.py:1 load_payment_popup crap=5.3 (cc=4, coverage=57%) |

  Scenario: adding a branch to the legacy function fails on complexity
    Given line 14 is `        if data.get("status") == "paid" or data.get("status") == "donated":`
    And line 15 is `            return "thank you"` and the paid test is present
    When the gate runs with `--scope hyper`
    Then py.crap is `[FAIL]` with summary `1 innermost changed functions, 1 above CRAP 4, 0 of them no worse than base`
    And its only finding is `app/payments.py:9 receive_message complexity rose from 9 to 10; move the new condition into its own function`

  Scenario: moving the new condition into a small covered function passes
    Given line 14 is `        if has_donated(data):` and line 15 is `            return "thank you"`
    And lines 29 to 33 are appended:
      """


      def has_donated(data):
          status = data.get("status")
          return status in ("paid", "donated")
      """
    And the paid test is present
    When the gate runs with `--scope hyper`
    Then py.crap is `[ok  ]` with summary `2 innermost changed functions, 1 above CRAP 4, 1 of them no worse than base`
    And no output line contains `load_payment_popup`

  Scenario: a new function must meet crap_max
    Given lines 29 to 42 are appended and no test calls `fee_band`:
      """


      def fee_band(amount):
          if amount < 5:
              return "micro"
          if amount < 20:
              return "small"
          if amount < 100:
              return "medium"
          if amount < 500:
              return "large"
          if amount < 5000:
              return "major"
          return "gift"
      """
    When the gate runs with `--scope hyper`
    Then py.crap is `[FAIL]` with summary `1 innermost changed functions, 1 above CRAP 4, 0 of them no worse than base`
    And its only finding is `app/payments.py:31 fee_band crap=42.0 (cc=6, coverage=0%)`

  Scenario: an uncovered changed line fails on coverage, not on complexity
    Given line 21 is `            return "on hold"` and the paid test is present unchanged
    When the gate runs with `--scope hyper`
    Then py.crap is `[FAIL]` with summary `1 innermost changed functions, 1 above CRAP 4, 0 of them no worse than base`
    And its only finding is `app/payments.py:9 receive_message crap=48.6 (cc=9, coverage=21%); changed lines not covered: 21`
    And no output line contains `complexity rose`

  Scenario: a renamed function counts as new
    Given line 9 is `    def on_message(data):`, line 28 is `    return on_message`, and the paid test is present with line 15 unchanged
    When the gate runs with `--scope hyper`
    Then py.crap is `[FAIL]` with summary `1 innermost changed functions, 1 above CRAP 4, 0 of them no worse than base`
    And its only finding is `app/payments.py:9 on_message crap=48.6 (cc=9, coverage=21%)`
    And no output line contains `load_payment_popup`

  Scenario: TypeScript follows the same rules
    Given the TypeScript fixture of `tools/test-scope-hyper.py` also holds at base `src/payments.ts`, a line-for-line port of
      `app/payments.py` (`export function loadPaymentPopup(settings)` containing `function receiveMessage(data)`)
    And the work tree changes the `return "thanks";` line to `return "thank you";` and adds a vitest test that makes that line run
    When `marestail gate --tier full --only ts.tests,ts.crap --scope hyper` runs
    Then ts.crap is `[ok  ]` with summary `1 innermost changed functions, 1 above CRAP 4, 1 of them no worse than base`
    And no output line contains `loadPaymentPopup`

  Scenario: existing CRAP behaviour is kept
    Then under `--scope all`, `--scope changed` and `--scope hard` every CRAP gate's summary and findings match the base commit's
    And `crap_max` is still read from the language section of `marestail.toml` with default 4
    And `marestail gate --tier fast --only ts.crap` with no ts coverage file still fails with `no coverage data; ts.tests must run first`
    And `python3 tools/test-scope-hyper.py` ends with `hyper scope ok`, and `tools/test-scope-hard.py` still ends with `hard scope ok`

  Scenario: README states the two rules
    Then the `--scope hyper` paragraph of README.md no longer says `Coverage and CRAP work as under \`changed\``
    And it states in one sentence that only the innermost function holding a changed line is gated
    And in one sentence that such a function passes when its CRAP is at most `crap_max`, or when its complexity is no higher than at `[git] base` and every changed line in it is covered
