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

## Deep modules

`marestail depth` prints, per module, the number of public symbols, the number of statements, and the ratio between them, marking wide-and-thin modules as shallow and files over 300 lines as long. It is a report for the architect, not a gate; the architect is told that length is a reading signal and that modules split by the knowledge they hide, not by line count. Two rules from it are gates: no function whose whole body forwards its own arguments to another call, and no import of a `_private` module from outside its package. The dependency-cruiser template adds the TypeScript equivalent: other code enters a module directory only through its `index.ts`. The architect prompt carries the opinions behind this: few deep modules, narrow general interfaces, complexity pulled down rather than pushed to callers.

## Dead code

The coverage rule has a side effect: the cheapest way to cover a dead function is to test it, so dead code gains tests and mutants instead of disappearing. The `deadcode` gate reports definitions nothing reaches from the program's entry points: vulture for Python, knip for TypeScript. Tests are excluded from the analysis on purpose, so a function only a test calls is dead.

It is deliberately narrow. Python counts unused functions, methods, classes, imports, properties and unreachable code; unused attributes and variables are left out because assignments on framework objects look identical to dead ones. Flask and Click decorators are ignored, and `[deadcode] python_ignore_names` in `marestail.toml` records the dynamic cases a human has checked. TypeScript counts unused files, exports and types; add `"dependencies"` to `[deadcode] ts_kinds` once the project's dependency list is settled. Deleting a live thing breaks a scenario, and the hardener bounces deletions made to satisfy a gate. Elixir has no maintained tool for this, and `mix xref unreachable` never was one, so `marestail/ex/deadcode.exs` reads the compiled BEAM files: every export nobody calls or captures from any module of the app is reported, behaviour callbacks and the usual lifecycle functions are skipped, `[elixir] preset = \"phoenix\"` skips the module kinds the framework calls dynamically, and `deadcode_ignore` names the rest. Elixir mutation testing uses muex, opt-in per repo with `[elixir] mutation = true` once `{:muex, "~> 0.9", only: [:dev, :test], runtime: false}` is in `mix.exs`; muex also covers Erlang. Ruby deadcode is unused private methods the Ripper scan never sees called; mutation is skipped (mutant exists, it is not wired). Rails tests and rubocop run through `[ruby] exec`, so a Docker Compose app can set `exec = ["docker", "compose", "run", "--rm", "-T", "backend", "bundle", "exec"]`. The comment/complexity scanner uses host `ruby` or, if that is missing, `ruby:3.2-slim` via Docker. Gleam has no first-party coverage tool, so `gl.tests` runs `gleam test` then Erlang `cover` on the package BEAMs, mapping hits back to `.gleam` via the compiler's `-file` attributes (attributed lines past the source length anchor to the function start — fail closed). `gl.crap` joins glance complexity with that cover data — the BEAM target is what makes CRAP possible. Mutation is skipped: muex mutates Elixir/Erlang AST, not Gleam source. Deadcode is unused private functions via glance. `gl.deps` fails on import cycles; same-package imports of `packagename/internal` are allowed (Gleam already blocks other packages).

C# dead code is unused private methods and fields only. Anything public, internal or protected in an ASP.NET Core app is reachable through routing, dependency injection, model binding or serialisation, and reporting it would talk an agent into deleting live code. Members with no access keyword count as private, since that is the C# default.

## C# / .NET

The `cs.*` gates run `dotnet` from the host when it has a .NET 8 SDK and otherwise inside `mcr.microsoft.com/dotnet/sdk:8.0` with the repo mounted at its own path, so the paths in every report mean the same thing inside and out; `HOME` and the NuGet cache live under `.marestail/` because only the repo is mounted, and the first run needs network for the image and a restore. `[dotnet] root` is the directory holding the `.csproj`; set `project` and `test_project` when more than one is there. One Roslyn scanner (`marestail/cs/scan/`, built once into `.marestail/cs-scan/`) answers the comments, complexity, depth, deps and dead-code questions from syntax alone. `cs.tests` reads coverlet's JSON and the TRX, and fails on any `[ExcludeFromCodeCoverage]`: `[dotnet] coverage_exclude` is the one place a composition root is waived. `cs.crap` joins complexity to coverage by line window rather than by member name, because coverlet reports async methods and lambdas under compiler-generated names. `cs.lint` reads the SARIF that a `-t:Rebuild` build writes (an up-to-date build writes none) with `AnalysisLevel` 8.0, `AnalysisMode` Recommended and code style enforced in the build; copy `templates/dotnet.editorconfig` next to the `.csproj` or the IDE rules stay silent, and fix formatting with `dotnet csharpier .` rather than `dotnet format analyzers`, which drops usings. `cs.deps` needs `.dotnet-layers.json` (copy `templates/dotnet-layers.json`; folders are relative to `[dotnet] root`) and reports layer breaks, forbidden external namespaces in `using` directives, and dependency cycles; it cannot see reflection or `dynamic`. `cs.mutation` is Stryker.NET, opt-in with `[dotnet] mutation = true` plus `dotnet-stryker` in `.config/dotnet-tools.json`; it needs the tests in their own project, and a project using the Sentry SDK must set `<SentryDisableSourceGenerator>true</SentryDisableSourceGenerator>` because Stryker cannot roll back mutants in generated code. Sonar for a repo with a `[dotnet]` section runs the SonarScanner for .NET (begin, build, end) in one container, imports the OpenCover report `cs.tests` wrote, indexes the other languages in the same pass, and fails unless C# lines and a coverage measure reached the server. sonar-scanner-cli exits 0 having analysed no C# at all, and the .NET scanner refuses to run beside a `sonar-project.properties`, so that repo has none and `[sonar] exclusions` holds the extra globs instead.

## Docs drift

The `docs` gate keeps a repo's documentation honest without reading prose, and it adapts to where the repo already keeps its documentation rather than adding files of its own: `[docs] files` names those places, and `routes_file` names whichever of them holds the routes table, often the README. With a `[docs]` section it checks three things: every route the code serves is a row in the routes ledger with a status, and every ledger row that is not retired is served by something; every environment variable the code reads is named somewhere in the documented files; every backticked path in those files that starts with a configured prefix exists. The routes ledger is a markdown table whose first column is a backticked path starting with `/` and whose second is a status, `live`, `alias`, `redirect` or `retired`; it can sit inside the README and doubles as the historical list of URLs the app has ever answered on. Retired rows must not exist in code; the rest must.

## Frozen files

Workers cannot change what the gate measures or what the spec says. `marestail.toml`, `sonar-project.properties`, `pyproject.toml`, `setup.cfg`, the coverage, Stryker, vitest, eslint, tsconfig and dependency-cruiser configs, `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, the Stop hooks, `features/`, `qa/` and `tasks/` are frozen for workers. The specifier may edit `features/` and `qa/`; the architect may edit the dependency contracts. Override with `[freeze]` in `marestail.toml` (`paths`, `spec`, `allow`). Frozen at any depth too: `.csproj`, `.sln`, `.props`, `.targets`, `NuGet.config`, `dotnet-tools.json`, `.editorconfig`, `.globalconfig`, `*.runsettings`, `stryker-config.*` and `.dotnet-layers.json` (the architect may edit the last), and `jest.config.*`, `babel.config.*`, `.babelrc*` and `next.config.*`, because a `coveragePathIgnorePatterns` entry guts the tests gate from inside the repo.

A worker that changes a frozen file has the change reverted and goes again within the current configuration. If it explained the change under `## Config change` in its handoff, the runner records the reason and the diff as a proposal in the handoffs directory and lists every proposal at the end of the run, so you decide in one place whether any of them should be made by hand.

The Stop hook makes interactive Claude Code, Antigravity (`agy`), Grok, and Cursor sessions loop the same way: it refuses to stop while the fast gate fails on changed files, up to five times per session. `marestail install` writes `.grok/hooks/` and records the repo in `~/.grok/trusted_folders.toml` so Grok will actually run those hooks; pipeline runs also pass `--trust`. The hook is idempotent per turn, so Grok loading both `.grok/hooks/` and `.claude/settings.json` does not double-count. Cursor gets `.cursor/hooks.json`; its stop hook replies with `followup_message` rather than an exit code.

Grok's headless mode does not read the prompt from stdin, so `marestail run --agent grok` writes the role prompt to a file and passes `--prompt-file`. Pipeline runs pass `--always-approve --no-plan --trust`, read JSON from stdout only, and turn off cross-session memory, `ask_user_question`, workflows, and Claude-compat hooks so a role cannot hang waiting for a human, leak context into the next one, or fire the Stop gate twice. Grok has no `--print-timeout`; the runner's four-hour subprocess limit is the cap. If an org policy locks always-approve, the run stops immediately rather than waiting on permission prompts.

Cursor pipeline runs use `cursor-agent` (override with `MARESTAIL_CURSOR`) with `--print --force --trust --sandbox disabled`, prompt on stdin, and JSON on stdout. Prefer `cursor-agent` over bare `agent` so a Grok `agent` on `PATH` is not picked up by mistake.

## Adapting for new languages

Copy the shape, not the tools. Per-language gates live in `marestail/gates/`; a new language is one file per gate plus a section in `marestail.toml`.

Also there is a skill in the repo (`.claude/skills/add-language/` and `.agents/skills/add-language/`; Grok scans both) which should make this process relatively (?) trivial.

## Inspo

Inspiration for this came from this brilliant interview with uncle bob, would recommend it if you're interested in ideas around delivering quality software in the age of AI! https://www.youtube.com/watch?v=zcLPGC-tvgk&t=1s
