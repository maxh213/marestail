# QA procedure: Junie backend

1. `cd /home/max/workspace/marestail-green`
2. `python3 tools/test-agent-backends.py`
   Expected: exit code `0`, last line `agent backends ok`.
3. Build a dandelion route-table fixture from marestail's own backend names and run the route script:
   ```
   python3 - > /tmp/marestail-route-fixture.ts <<'EOF'
   from marestail import route
   names = sorted(route.BACKENDS)
   accounts = "\n".join(f"  {{ id: '{n}', ...CLAUDE_LINES }}," for n in names)
   print("const CLAUDE_LINES = { standard: 'claude-opus-5 high', max: 'claude-opus-5 max' }")
   print(f"const ACCOUNTS = [\n{accounts}\n]")
   print("const ROUTES = [{ providers: [" + ", ".join(f"'{n}'" for n in names) + "], line: 'claude-opus-5 medium' }]")
   EOF
   export MARESTAIL_DANDELION_SRC=/tmp/marestail-route-fixture.ts
   python3 tools/test-route.py
   ```
   Expected: exit code `0`, last line `route ok`, and a line matching `^dandelion source: \d+ route lines parse$`.
4. `./bin/marestail run --help`
   Expected: lists `--from`, `--to`, `--auto`, `--scope`, `--focus`, `--model`, `--retries`, `--effort`, `--agent` in this order.
5. `grep -E '# backend = "claude"  # claude \| agy \| grok \| cursor \| kilo \| kimi \| junie' templates/marestail.toml`
   Expected: one match.
6. `grep -E '"junie"' marestail/tui/collect.py`
   Expected: one match in the `BACKENDS` frozenset.
7. `grep -E '^\| `MARESTAIL_JUNIE`' README.md`
   Expected: one match in the environment variables table.
8. `grep -E 'marestail run tasks/001\.md.*--agent agy\|grok\|cursor\|kilo\|kimi\|junie' README.md`
   Expected: one match.
9. `grep -E 'junie.*low.*medium.*high' README.md`
   Expected: one match in the `--effort` paragraph.
10. `grep -E 'dandelion/route.*junie|junie.*dandelion/route' README.md`
    Expected: one match.
11. `grep -E '^## Junie pipeline runs' README.md`
    Expected: one match.
12. Verify the junie command shape, resolve and label:
    ```
    python3 - <<'PY'
    import os
    from pathlib import Path
    os.environ["MARESTAIL_JUNIE"] = "/opt/junie"
    from marestail.config import Config
    from marestail.runner import Run, agent_command, resolve_agent, agent_label
    state = Run(config=Config(root=Path("/work"), raw={}), task=Path("/work/t.md"), model="gemini-3.8-flash", retries=0, agent="junie", effort="high")
    assert resolve_agent(state) == "junie"
    assert agent_label(state) == "gemini-3.8-flash high"
    assert agent_command(state) == ["/opt/junie", "--skip-update-check", "--input-format=json", "--output-format=json", "-p", "/work", "--model=gemini-3.8-flash", "--effort=high"]
    PY
    ```
    Expected: exit code `0`, no output.
13. Verify the junie summary line from the verified output:
    ```
    python3 - <<'PY'
    from marestail.runner import junie_summary
    output = '{"sessionId":"session-260918-135149-1lx1","taskName":"Junie CLI Task Completion and Output Handling","result":"### Summary\\n- pong\\n\\n### Changes\\n- No files were created or modified as requested.\\n\\n### Verification\\n- Verified that the repository remains empty and untouched.","changes":[],"llmUsage":[{"model":"gemini-3.8-flash","calls":17,"cost":0.06395658750000001,"inputTokens":91729,"cacheInputTokens":261869,"outputTokens":10527},{"model":"gpt-5.4-nano","calls":13,"cost":0.01091355,"inputTokens":44499,"cacheInputTokens":0,"outputTokens":1611},{"model":"gpt-4.1-mini-2025-04-14","calls":16,"cost":0.004483599999999999,"inputTokens":10037,"cacheInputTokens":0,"outputTokens":293},{"model":"gpt-4.1-2025-04-14","calls":1,"cost":0.0015019999999999999,"inputTokens":743,"cacheInputTokens":0,"outputTokens":2},{"model":"gemini-3.5-flash-lite","calls":1,"cost":4.9225E-4,"inputTokens":1969,"cacheInputTokens":0,"outputTokens":0}]}'
    expected = 'calls=48 tokens=423279 cost=$0.08 "### Summary - pong  ### Changes - No files were created or modified as requested.  ### Verification "'
    assert junie_summary(output) == expected, junie_summary(output)
    PY
    ```
    Expected: exit code `0`, no output.
14. Verify junie rate-limit detection:
    ```
    python3 - <<'PY'
    from marestail.runner import junie_rate_limited
    assert junie_rate_limited(1, "Your balance is exhausted.") is True
    assert junie_rate_limited(1, '{"errors":[{"level":"ERROR","message":"InsufficientAccountBalance"}]}') is True
    assert junie_rate_limited(1, "insufficient balance") is True
    assert junie_rate_limited(1, "Junie failed with the message: Invalid model: no-such-model-xyz") is False
    assert junie_rate_limited(0, '{"result":"the quota gate passed"}') is False
    PY
    ```
    Expected: exit code `0`, no output.
15. Verify a verdict inside the junie result is parsed:
    ```
    python3 - <<'PY'
    from pathlib import Path
    from marestail.runner import parse_verdict
    assert parse_verdict(Path("/tmp/missing-verdict.md"), '{"result":"VERDICT: BOUNCE to coder"}') == ("BOUNCE", "coder")
    PY
    ```
    Expected: exit code `0`, no output.
