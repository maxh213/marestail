# QA procedure: hard-scoped install leaves CLAUDE.md and AGENTS.md alone

1. `cd` to the marestail-green repo root with `.venv` active.
2. `mkdir -p /tmp/mt-hard-empty && rm -rf /tmp/mt-hard-empty/* /tmp/mt-hard-empty/.[!.]* 2>/dev/null; GROK_HOME=/tmp/mt-grok-$$ mkdir -p "$GROK_HOME"`
   Expected: empty install target ready.
3. `GROK_HOME=/tmp/mt-grok-$$ marestail install /tmp/mt-hard-empty`
   Expected: exit 0; `CLAUDE.md` and `AGENTS.md` contain `marestail gate`; stdout ends with `installed into /tmp/mt-hard-empty; edit marestail.toml and sonar-project.properties` (no `CLAUDE.md` in that line).
4. `rm -rf /tmp/mt-hard-empty && mkdir /tmp/mt-hard-empty && GROK_HOME=/tmp/mt-grok-$$ marestail install --scope hard /tmp/mt-hard-empty`
   Expected: exit 0; neither `CLAUDE.md` nor `AGENTS.md` exists; `.gitignore` lists every `GITIGNORE_GENERATED_LINES` entry; `marestail.toml`, `sonar-project.properties`, `tasks/README.md`, `PERFORMANCE.md`, `guidance/ts.md`, and the four Stop-hook configs exist; `.claude/settings.json` Stop includes `marestail gate --hook`; stdout ends with `installed into /tmp/mt-hard-empty; left CLAUDE.md and AGENTS.md alone; edit marestail.toml and sonar-project.properties`.
5. `rm -rf /tmp/mt-hard-keep && mkdir /tmp/mt-hard-keep && printf 'team rules\n' > /tmp/mt-hard-keep/CLAUDE.md && GROK_HOME=/tmp/mt-grok-$$ marestail install --scope hard /tmp/mt-hard-keep`
   Expected: `CLAUDE.md` is still exactly `team rules\n`; no `AGENTS.md`.
6. `rm -rf /tmp/mt-full-gi && mkdir /tmp/mt-full-gi && GROK_HOME=/tmp/mt-grok-$$ marestail install --gitignore-generated /tmp/mt-full-gi`
   Expected: `.gitignore` has generated lines; `CLAUDE.md` and `AGENTS.md` still contain `marestail gate`.
7. `marestail install --scope soft /tmp/mt-hard-empty`
   Expected: non-zero exit; stderr mentions choices `all`, `changed`, `hard`.
8. `marestail install --help`
   Expected: lists `--scope` and `--gitignore-generated`.
9. `grep -n 'install --scope hard\|CLAUDE.md\|gitignore-generated' README.md | head -20`
   Expected: quick start and the `--gitignore-generated` paragraph say hard install leaves `CLAUDE.md`/`AGENTS.md` alone and implies `--gitignore-generated`; shared-docs wording is for full install only.
10. `python3 tools/test-practices.py; python3 tools/test-perf.py; python3 tools/test-scope-hard.py` (and `python3 tools/test-install-hard.py` if that file was added)
    Expected: each exits 0 with a last line containing `ok` (or the practices/perf scripts' usual success lines).
