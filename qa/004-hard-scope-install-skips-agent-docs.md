# QA procedure: hard-scoped install leaves CLAUDE.md and AGENTS.md alone

1. `cd` to the marestail-green repo root with `.venv` active.
2. `mkdir -p /tmp/mt-hard-empty && rm -rf /tmp/mt-hard-empty/* /tmp/mt-hard-empty/.[!.]* 2>/dev/null; GROK_HOME=/tmp/mt-grok-$$ mkdir -p "$GROK_HOME"`
   Expected: empty install target ready.
3. `GROK_HOME=/tmp/mt-grok-$$ marestail install /tmp/mt-hard-empty`
   Expected: exit 0; `CLAUDE.md` and `AGENTS.md` contain `marestail gate`; stdout ends with `installed into /tmp/mt-hard-empty; edit marestail.toml and sonar-project.properties` (no `CLAUDE.md` in that line).
4. `cp /tmp/mt-hard-empty/CLAUDE.md /tmp/mt-hard-claude-before.md && GROK_HOME=/tmp/mt-grok-$$ marestail install /tmp/mt-hard-empty && cmp /tmp/mt-hard-empty/CLAUDE.md /tmp/mt-hard-claude-before.md`
   Expected: exit 0; `CLAUDE.md` byte-identical (GATE_MARKER blocks a second Gate append).
5. `rm -rf /tmp/mt-scope-changed && mkdir /tmp/mt-scope-changed && GROK_HOME=/tmp/mt-grok-$$ marestail install --scope changed /tmp/mt-scope-changed`
   Expected: exit 0; both agent docs contain `marestail gate`; same full-install closing line as step 3 (no `CLAUDE.md` in that line).
6. `rm -rf /tmp/mt-hard-empty && mkdir /tmp/mt-hard-empty && GROK_HOME=/tmp/mt-grok-$$ marestail install --scope hard /tmp/mt-hard-empty`
   Expected: exit 0; neither `CLAUDE.md` nor `AGENTS.md` exists; `.gitignore` lists every `GITIGNORE_GENERATED_LINES` entry; `marestail.toml`, `sonar-project.properties`, `tasks/README.md`, `PERFORMANCE.md`, `guidance/ts.md`, and the four Stop-hook configs exist; `.claude/settings.json` Stop includes `marestail gate --hook`; stdout ends with `installed into /tmp/mt-hard-empty; left CLAUDE.md and AGENTS.md alone; edit marestail.toml and sonar-project.properties`.
7. `rm -rf /tmp/mt-hard-keep && mkdir /tmp/mt-hard-keep && printf 'team rules\n' > /tmp/mt-hard-keep/CLAUDE.md && GROK_HOME=/tmp/mt-grok-$$ marestail install --scope hard /tmp/mt-hard-keep`
   Expected: `CLAUDE.md` is still exactly `team rules\n`; no `AGENTS.md`.
8. `rm -rf /tmp/mt-hard-agents && mkdir /tmp/mt-hard-agents && printf 'team rules\n' > /tmp/mt-hard-agents/AGENTS.md && GROK_HOME=/tmp/mt-grok-$$ marestail install --scope hard /tmp/mt-hard-agents`
   Expected: `AGENTS.md` is still exactly `team rules\n`; no `CLAUDE.md`.
9. `rm -rf /tmp/mt-full-gi && mkdir /tmp/mt-full-gi && GROK_HOME=/tmp/mt-grok-$$ marestail install --gitignore-generated /tmp/mt-full-gi`
   Expected: `.gitignore` has generated lines; `CLAUDE.md` and `AGENTS.md` still contain `marestail gate`.
10. `rm -rf /tmp/mt-hard-gated && mkdir /tmp/mt-hard-gated && GROK_HOME=/tmp/mt-grok-$$ marestail install /tmp/mt-hard-gated && cp /tmp/mt-hard-gated/CLAUDE.md /tmp/mt-gated-claude.md && cp /tmp/mt-hard-gated/AGENTS.md /tmp/mt-gated-agents.md && GROK_HOME=/tmp/mt-grok-$$ marestail install --scope hard /tmp/mt-hard-gated && cmp /tmp/mt-hard-gated/CLAUDE.md /tmp/mt-gated-claude.md && cmp /tmp/mt-hard-gated/AGENTS.md /tmp/mt-gated-agents.md`
    Expected: both files byte-identical to before the hard install (Gate section left in place).
11. `marestail install --scope soft /tmp/mt-hard-empty`
    Expected: non-zero exit; stderr mentions choices `all`, `changed`, `hard`.
12. `marestail install --help`
    Expected: lists `--scope` and `--gitignore-generated`.
13. `grep -n 'install --scope hard\|CLAUDE.md\|gitignore-generated' README.md | head -20`
    Expected: quick start and the `--gitignore-generated` paragraph say hard install leaves `CLAUDE.md`/`AGENTS.md` alone and implies `--gitignore-generated`; shared-docs wording is for full install only.
14. `python3 tools/test-practices.py; python3 tools/test-scope-hard.py; python3 tools/test-install-hard.py`
    Expected: each exits 0 (practices / scope-hard / install-hard usual success lines).
15. `python3 tools/test-perf.py; echo "exit=$?"`
    Expected: exit 1; last line `verdict-commit-files: '' != 'perf/bench_x.py'` (same contract as task 000 — not a pass).
