# marestail

It's called marestail because working with LLMs reminds me of hacking away at weeds on the allotment. LLMs drift and cause bugs at speed which you have to correct for as you work with them. Marestail pops up each week with a vengance and I need to pull it all out again. The comparison isn't 1:1 but it's good enough for me :) 

Marestail is gauntlet of deterministic gates for coding agents, after Uncle Bob's approach: don't tell the agent to be clean, measure cleanliness and make it loop until the measurement passes.

This whole project is very opinionated on what I consider to be clean code / good practices which I want to force an LLM into implementing.

## Gates

| Gate | Python | TypeScript |
|---|---|---|
| tests, 100% line and branch coverage | pytest, coverage.py | vitest, v8 |
| CRAP ≤ 4 per function | radon + coverage | typescript AST + istanbul |
| mutation testing, changed files | mutmut | Stryker |
| dependency direction | import-linter | dependency-cruiser |
| types and lint | mypy strict, ruff | tsc strict, eslint |
| no comments, no docstrings | tokenizer | typescript scanner |
| no pass-through functions, no imports of private modules | ast | typescript AST |
| no unreachable definitions | vulture | knip |
| Sonar quality gate, zero issues, zero duplication | local SonarQube | local SonarQube |
| acceptance | any command in `[qa]` | |

Tiers: `fast` (everything quick), `sonar` (adds the Sonar quality gate), `full` (adds mutation testing), `qa`.

## Use

```sh
export PATH="$PATH:/path/to/marestail/bin"
cd your-repo
marestail install .          # marestail.toml, sonar-project.properties, CLAUDE.md line, Stop hook
marestail sonar setup        # local SonarQube in docker, token in ~/.config/marestail
marestail gate               # fast tier, whole repo
marestail gate --tier full --scope changed
marestail graph              # module dependency graph, for the architect and for you
marestail run tasks/001.md   # the pipeline below, each role in a fresh claude -p
```

## Pipeline

| Step | Kind | Gate | Does |
|---|---|---|---|
| specifier | worker | none | Gherkin scenarios and a QA procedure from the task |
| critic | judge | none | judges the spec against the task; bounces to a fresh specifier, twice at most; then a human approval pause |
| coder | worker | fast | implements; must trace every scenario to a test in its handoff |
| cleaner | worker | sonar | readability without comments, CRAP, Sonar |
| architect | worker | sonar | draws module boundaries, moves code, tightens the dependency contracts |
| hardener | judge | full | judges the diff and the mutation report; bounces to a fresh coder, three times at most |
| qa | worker | qa | turns the QA procedure into an executable end-to-end test |

Workers edit and commit. Judges write one verdict file and nothing else; the runner discards any other edit a judge makes. Handoff and verdict files are runtime state under `.marestail/`, never committed: when a worker passes verification the runner folds its handoff into that role's commit message, and a judge's verdict becomes an empty commit carrying the verdict. `git log` on the branch is the record, and a role in a fresh clone reads its predecessors from there. When a pipeline completes, the task's handoff files are archived under `.marestail/runs/`. Every role runs in a fresh session with a short prompt: the role file, the task, the earlier handoffs, and how to finish. Judges also get the gate report.

After every worker the runner checks, deterministically: the handoff exists, the tree is committed, no frozen file changed, the gate for that tier passes, and for the coder that every scenario in the feature file is traced to a test that exists. Anything failing goes back to the same role as feedback, three attempts at most. A judge's gate failing is a bounce regardless of what the judge wrote.

## Writing tasks

One task is one vertical slice: a user-visible outcome, thin, through every layer it needs. `marestail install` drops `tasks/README.md` into the repo with the guidance; the critic bounces a spec that delivers a layer instead of a slice unless the task declares itself a refactor.

## Deep modules

`marestail depth` prints, per module, the number of public symbols, the number of statements, and the ratio between them, marking wide-and-thin modules as shallow. It is a report for the architect, not a gate. Two rules from it are gates: no function whose whole body forwards its own arguments to another call, and no import of a `_private` module from outside its package. The dependency-cruiser template adds the TypeScript equivalent: other code enters a module directory only through its `index.ts`. The architect prompt carries the opinions behind this: few deep modules, narrow general interfaces, complexity pulled down rather than pushed to callers.

## Dead code

The coverage rule has a side effect: the cheapest way to cover a dead function is to test it, so dead code gains tests and mutants instead of disappearing. The `deadcode` gate reports definitions nothing reaches from the program's entry points: vulture for Python, knip for TypeScript. Tests are excluded from the analysis on purpose, so a function only a test calls is dead.

It is deliberately narrow. Python counts unused functions, methods, classes, imports, properties and unreachable code; unused attributes and variables are left out because assignments on framework objects look identical to dead ones. Flask and Click decorators are ignored, and `[deadcode] python_ignore_names` in `marestail.toml` records the dynamic cases a human has checked. TypeScript counts unused files, exports and types; add `"dependencies"` to `[deadcode] ts_kinds` once the project's dependency list is settled. Deleting a live thing breaks a scenario, and the hardener bounces deletions made to satisfy a gate.

## Frozen files

Workers cannot change what the gate measures or what the spec says. `marestail.toml`, `sonar-project.properties`, `pyproject.toml`, `setup.cfg`, the coverage, Stryker, vitest, eslint, tsconfig and dependency-cruiser configs, `CLAUDE.md`, the Stop hook, `features/`, `qa/` and `tasks/` are frozen for workers. The specifier may edit `features/` and `qa/`; the architect may edit the dependency contracts. Override with `[freeze]` in `marestail.toml` (`paths`, `spec`, `allow`).

A worker that changes a frozen file has the change reverted and goes again within the current configuration. If it explained the change under `## Config change` in its handoff, the runner records the reason and the diff as a proposal in the handoffs directory and lists every proposal at the end of the run, so you decide in one place whether any of them should be made by hand.

The Stop hook makes interactive Claude Code sessions loop the same way: it refuses to stop while the fast gate fails on changed files, up to five times per session.

## Adapting for new languages

Copy the shape, not the tools. Per-language gates live in `marestail/gates/`; a new language is one file per gate plus a section in `marestail.toml`.

Also there is a claude skill in the repo which should make this process relatively (?) trivial.

## Inspo

Inspiration for this came from this brilliant interview with uncle bob, would recommend it if you're interested in ideas around delivering quality software in the age of AI! https://www.youtube.com/watch?v=zcLPGC-tvgk&t=1s
