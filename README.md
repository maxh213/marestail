# marestail

It's called marestail because working with LLMs reminds me of hacking away at weeds on the allotment. LLMs drift and cause bugs at speed which you have to correct for as you work with them. Marestail pops up each week with a vengance and I need to pull it all out again. The comparison isn't 1:1 but it's good enough for me :) 

Marestail is gauntlet of deterministic gates for coding agents, after Uncle Bob's approach: don't tell the agent to be clean, measure cleanliness and make it loop until the measurement passes.

## Gates

| Gate | Python | TypeScript |
|---|---|---|
| tests, 100% line and branch coverage | pytest, coverage.py | vitest, v8 |
| CRAP ≤ 6 per function | radon + coverage | typescript AST + istanbul |
| mutation testing, changed files | mutmut | Stryker |
| dependency direction | import-linter | dependency-cruiser |
| types and lint | mypy strict, ruff | tsc strict, eslint |
| no comments, no docstrings | tokenizer | typescript scanner |
| Sonar quality gate, zero issues, zero duplication | local SonarQube | local SonarQube |
| acceptance | any command in `[qa]` | |

Tiers: `fast` (everything quick), `full` (adds mutation and Sonar), `qa`.

## Use

```sh
export PATH="$PATH:/path/to/marestail/bin"
cd your-repo
marestail install .          # marestail.toml, sonar-project.properties, CLAUDE.md line, Stop hook
marestail sonar setup        # local SonarQube in docker, token in ~/.config/marestail
marestail gate               # fast tier, whole repo
marestail gate --tier full --scope changed
marestail run tasks/001.md   # specifier → coder → cleaner → hardener → qa, each in a fresh claude -p
```

Each role gets a short prompt from `roles/`, the task, the earlier handoffs, and one instruction: loop on `marestail gate` until it passes. The runner reruns the gate itself after every role and sends the agent back with the report if it disagrees.

The Stop hook makes interactive Claude Code sessions do the same: it refuses to stop while the fast gate fails on changed files, up to five times per session.

## Adapting for new languages

Copy the shape, not the tools. Per-language gates live in `marestail/gates/`; a new language is one file per gate plus a section in `marestail.toml`.
