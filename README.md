# marestail

It's called marestail because working with LLMs reminds me of hacking away at weeds on the allotment. LLMs drift and cause bugs at speed which you have to correct for as you work with them. Marestail pops up each week with a vengance and I need to pull it all out again. The comparison isn't 1:1 but it's good enough for me :) 

Marestail is gauntlet of deterministic gates for coding agents, after Uncle Bob's approach: don't tell the agent to be clean, measure cleanliness and make it loop until the measurement passes.

This whole project is very opinionated on what I consider to be clean code / good practices which I want to force an LLM into implementing.

## Gates

| Gate | Python | TypeScript | Elixir | Ruby / Rails | Gleam | C# / .NET |
|---|---|---|---|---|---|---|
| tests, 100% line and branch coverage | pytest, coverage.py | vitest, v8 (or jest) | mix test --cover | rspec + SimpleCov | `gleam test` + Erlang `cover` (Gleam line map) | dotnet test + coverlet |
| CRAP ≤ 4 per function | radon + coverage | typescript AST + istanbul | elixir AST + cover | Ripper AST + SimpleCov | glance AST + cover | Roslyn scanner + coverlet |
| mutation testing, changed files | mutmut | Stryker | muex (opt-in) | (skipped; mutant exists but is not wired) | (skipped; muex mutates Elixir/Erlang AST, not Gleam) | Stryker.NET (opt-in) |
| dependency direction | import-linter | dependency-cruiser | mix xref cycles | Zeitwerk constants vs `.ruby-layers.json` | import cycles (same-package `internal/` allowed) | Roslyn type resolution vs `.dotnet-layers.json`, cycles |
| types and lint | mypy strict, ruff | tsc strict, eslint | mix format, mix compile | rubocop | `gleam format --check`, `gleam build --warnings-as-errors` | Roslyn analyzers via SARIF |
| no comments, no docstrings | tokenizer | typescript scanner | elixir AST scanner | Ripper | `//` / `///` scanner | Roslyn scanner |
| no pass-through functions, no imports of private modules | ast | typescript AST | elixir AST | Ripper | glance AST | Roslyn scanner (pass-throughs) |
| no unreachable definitions | vulture | knip | BEAM abstract code scan | unused private methods | unused private functions (glance) | unused private members |
| docs match the code: routes ledger, env vars, paths | regex over sources | regex over sources | regex over sources | regex over sources | regex over sources | regex over sources |
| Sonar quality gate, zero issues, zero duplication | local SonarQube | local SonarQube | local SonarQube | local SonarQube | local SonarQube | local SonarQube, SonarScanner for .NET |

Acceptance is the same in every language: whatever command `[qa] cmd` names, run from `[qa] cwd`.

Tiers: `fast` (everything quick), `sonar` (adds the Sonar quality gate), `full` (adds mutation testing), `qa`.

A Next.js repo that will not move to vitest sets `[ts] runner = "jest"`: the gate runs the repo's own `node_modules/.bin/jest` with `--coverageProvider=babel` (v8 cannot express branch arms) and needs `[ts] sources` so untested files still appear in the report.

## Use

```sh
export PATH="$PATH:/path/to/marestail/bin"
cd your-repo
marestail install .          # marestail.toml, sonar-project.properties, CLAUDE.md / AGENTS.md, Stop hooks
marestail sonar setup        # local SonarQube in docker, token in ~/.config/marestail
marestail gate               # fast tier, whole repo
marestail gate --tier full --scope changed
marestail graph              # module dependency graph, for the architect and for you
marestail depth              # prints, per module, the number of public symbols, the number of statements, and the ratio between them, marking wide-and-thin modules as shallow and files over 300 lines as long.
marestail run tasks/001.md   # Claude (default), or --agent agy|grok|cursor / MARESTAIL_AGENT
```

## Overnight

`tools/overnight.sh tasks/000.md tasks/002.md ...` runs tasks in order, each to the hardener by default (`STOP_AT=qa` to include QA), stops at the first failure, waits out rate limits for up to six hours, and writes `.marestail/runs/overnight-<stamp>.md` with one section per task: exit code, minutes, HEAD, the role and verdict lines, and any config proposals. Start it detached: `nohup setsid tools/overnight.sh ... > /dev/null 2>&1 &`.

## Pipeline

| Step | Kind | Gate | Does |
|---|---|---|---|
| specifier | worker | none | Gherkin scenarios and a QA procedure from the task |
| critic | judge | none | judges the spec against the task; bounces to a fresh specifier until it passes; then a human approval pause |
| coder | worker | fast | implements; must trace every scenario to a test in its handoff |
| cleaner | worker | sonar | readability without comments, CRAP, Sonar |
| architect | worker | sonar | draws module boundaries, moves code, tightens the dependency contracts |
| hardener | judge | full | judges the diff and the mutation report; bounces to a fresh coder until it passes |
| qa | worker | qa | turns the QA procedure into an executable end-to-end test |

Workers edit and commit. Judges write one verdict file and nothing else; the runner discards any other edit a judge makes. Handoff and verdict files are runtime state under `.marestail/`, never committed: when a worker passes verification the runner folds its handoff into that role's commit message, and a judge's verdict becomes an empty commit carrying the verdict. `git log` on the branch is the record, and a role in a fresh clone reads its predecessors from there. When a pipeline completes, the task's handoff files are archived under `.marestail/runs/`. Every role runs in a fresh session with a short prompt: the role file, the task, the earlier handoffs, and how to finish. Judges also get the gate report.

After every worker the runner checks, deterministically: the handoff exists, the tree is committed, no frozen file changed, the gate for that tier passes, and for the coder that every scenario in the feature file is traced to a test that exists. Anything failing goes back to the same role as feedback until it passes (`--retries N` caps it; default is unlimited). A judge's gate failing is a bounce regardless of what the judge wrote. A judge bounces as many times as it takes, with one stop: if it writes the same numbered findings twice in a row, the worker is not making progress and the pipeline stops for a human.

## Writing tasks

One task is one vertical slice: a user-visible outcome, thin, through every layer it needs. `marestail install` drops `tasks/README.md` into the repo with the guidance; the critic bounces a spec that delivers a layer instead of a slice unless the task declares itself a refactor.

## Adapting for new languages

Copy the shape, not the tools. Per-language gates live in `marestail/gates/`; a new language is one file per gate plus a section in `marestail.toml`.

Also there is a skill in the repo (`.claude/skills/add-language/` and `.agents/skills/add-language/`; Grok scans both) which should make this process relatively (?) trivial.

## Inspo

Inspiration for this came from this brilliant interview with uncle bob, would recommend it if you're interested in ideas around delivering quality software in the age of AI! https://www.youtube.com/watch?v=zcLPGC-tvgk&t=1s
