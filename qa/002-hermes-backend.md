# QA procedure: Hermes backend

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
5. `grep -E '# backend = "claude"  # claude \| agy \| grok \| cursor \| kilo \| kimi \| junie \| hermes' templates/marestail.toml`
   Expected: one match.
6. `grep -E '"hermes"' marestail/tui/collect.py`
   Expected: one match in the `BACKENDS` frozenset.
7. Verify the watch TUI classifies a `python -m hermes_cli.main` process as hermes:
   ```
   python3 - <<'PY'
   from marestail.tui.collect import backend_of
   cmd = ["python", "-m", "hermes_cli.main", "chat", "--query-file", "/tmp/p", "--oneshot", "-Q", "--format", "stream-json", "--yolo", "--accept-hooks", "--max-turns", "1000", "-m", "x-ai/grok-4.6", "--reasoning", "xhigh"]
   assert backend_of(cmd) == "hermes", backend_of(cmd)
   PY
   ```
   Expected: exit code `0`, no output.
8. `grep -E '^\| `MARESTAIL_HERMES`' README.md`
   Expected: one match in the environment variables table.
9. `grep -E 'marestail run tasks/001\.md.*--agent agy\|grok\|cursor\|kilo\|kimi\|junie\|hermes' README.md`
   Expected: one match.
10. `grep -E 'hermes.*--reasoning' README.md`
    Expected: one match in the `--effort` paragraph.
11. `grep -E 'dandelion/route.*hermes|hermes.*dandelion/route' README.md`
    Expected: one match.
12. `grep -E '^## Hermes pipeline runs' README.md` and `grep -F 'hermes chat --query-file' README.md`
    Expected: one match each.
13. Verify the hermes command shape, resolve and label:
    ```
    python3 - <<'PY'
    import os
    from pathlib import Path
    os.environ["MARESTAIL_HERMES"] = "/opt/hermes"
    from marestail.config import Config
    from marestail.runner import Run, hermes_command, resolve_agent, agent_label
    state = Run(config=Config(root=Path("/work"), raw={}), task=Path("/work/t.md"), model="x-ai/grok-4.6", retries=0, agent="hermes", effort="xhigh")
    assert resolve_agent(state) == "hermes"
    assert agent_label(state) == "x-ai/grok-4.6 xhigh"
    assert hermes_command(state, Path("/work/.marestail/runs/002/hermes.prompt.md")) == ["/opt/hermes", "chat", "--query-file", str(Path("/work/.marestail/runs/002/hermes.prompt.md")), "--oneshot", "-Q", "--format", "stream-json", "--yolo", "--accept-hooks", "--max-turns", "1000", "-m", "x-ai/grok-4.6", "--reasoning", "xhigh"]
    no_model = Run(config=Config(root=Path("/work"), raw={}), task=Path("/work/t.md"), model=None, retries=0, agent="hermes", effort="xhigh")
    assert agent_label(no_model) == "hermes xhigh"
    assert "-m" not in hermes_command(no_model, Path("/work/.marestail/runs/002/hermes.prompt.md"))
    no_effort = Run(config=Config(root=Path("/work"), raw={}), task=Path("/work/t.md"), model="x-ai/grok-4.6", retries=0, agent="hermes", effort=None)
    assert "--reasoning" not in hermes_command(no_effort, Path("/work/.marestail/runs/002/hermes.prompt.md"))
    PY
    ```
    Expected: exit code `0`, no output.
14. Verify the hermes summary line from the verified output and that it truncates at 100 characters:
    ```
    python3 - <<'PY'
    from marestail.runner import hermes_summary
    output = '{"type": "system", "subtype": "init", "model": "x-ai/grok-4.6", "session_id": "20260918_151301_be7c1c", "timestamp": 1789740781416}\n{"type": "tool_use", "name": "terminal", "input": {"command": "git status --short"}, "timestamp": 1789740714068}\n{"type": "tool_result", "name": "terminal", "output": "{\\"output\\": \\"exit1=0\\", \\"exit_code\\": 0, \\"error\\": null}", "duration_ms": 218, "is_error": false, "timestamp": 1789740714290}\n{"type": "text", "text": "pong", "timestamp": 1789740800465}\n{"type": "result", "session_id": "20260918_151301_be7c1c", "exit_code": 0, "text": "pong", "tokens": {"input": 14851, "output": 1, "total": 14980, "cache_read": 128, "cache_write": 0}, "duration_ms": 19100, "timestamp": 1789740800516}'
    assert hermes_summary(output) == 'tokens=14980 "pong"', hermes_summary(output)
    text = "word " * 30
    long = f'{{"type": "result", "exit_code": 0, "text": "{text}", "tokens": {{"total": 7}}}}'
    assert hermes_summary(long) == f'tokens=7 "{text[:100]}"', hermes_summary(long)
    PY
    ```
    Expected: exit code `0`, no output.
15. Verify hermes rate-limit detection, including the shared LIMIT_PATTERN and all six entitlement strings:
    ```
    python3 - <<'PY'
    from marestail.runner import hermes_rate_limited
    assert hermes_rate_limited(2, '{"type": "result", "exit_code": 2, "error": "Subscription credits are exhausted. Top up/renew credits, then retry."}') is True
    assert hermes_rate_limited(2, 'stderr prefix\ninsufficient_credits\nstderr suffix') is True
    assert hermes_rate_limited(2, 'no_usable_credits') is True
    assert hermes_rate_limited(2, 'subscription_expired') is True
    assert hermes_rate_limited(2, 'subscription_required') is True
    assert hermes_rate_limited(2, 'member_spend_cap_exceeded') is True
    assert hermes_rate_limited(2, 'rate limit exceeded') is True
    assert hermes_rate_limited(2, "Unknown --reasoning 'ultrahigh'") is False
    assert hermes_rate_limited(0, '{"type": "result", "exit_code": 0, "text": "the quota gate passed"}') is False
    PY
    ```
    Expected: exit code `0`, no output.
16. Verify non-JSON hermes output falls back to its tail and is not treated as rate limited on exit 0:
    ```
    python3 - <<'PY'
    from marestail.runner import hermes_rate_limited, hermes_summary
    assert hermes_summary("plain text failure") == "plain text failure"
    assert hermes_rate_limited(1, "plain text failure") is False
    assert hermes_rate_limited(0, "plain text ok") is False
    PY
    ```
    Expected: exit code `0`, no output.
17. Verify a verdict inside the hermes result is parsed:
    ```
    python3 - <<'PY'
    from pathlib import Path
    from marestail.runner import parse_verdict
    assert parse_verdict(Path("/tmp/missing-verdict.md"), '{"type": "result", "text": "VERDICT: BOUNCE to coder"}') == ("BOUNCE", "coder")
    PY
    ```
    Expected: exit code `0`, no output.
