# marestail

It's called marestail because working with LLMs reminds me of hacking away at weeds on the allotment. LLMs drift and cause bugs at speed which you have to correct for as you work with them. Marestail pops up each week with a vengance and I need to pull it all out again. The comparison isn't 1:1 but it's good enough for me :) 

Marestail is gauntlet of deterministic gates for coding agents, after Uncle Bob's approach: don't tell the agent to be clean, measure cleanliness and make it loop until the measurement passes.

This whole project is very opinionated on what I consider to be clean code / good practices which I want to force an LLM into implementing.

## Gates

| Gate | Python | TypeScript | Elixir | Ruby / Rails | C# / .NET | Erlang | Rust |
|---|---|---|---|---|---|---| --- |
| tests, 100% line and branch coverage | pytest, coverage.py | vitest, v8 (or jest) | mix test --cover | rspec + SimpleCov | dotnet test + coverlet | eunit + cover | cargo llvm-cov, lines and code regions |
| CRAP ≤ 4 per function | radon + coverage | typescript AST + istanbul | elixir AST + cover | Ripper AST + SimpleCov | Roslyn scanner + coverlet | erl_parse AST + cover | syn AST + llvm-cov lines |
| mutation testing, changed files | mutmut | Stryker | muex | mutant | Stryker.NET | built-in operator-swap escript | cargo-mutants |
| dependency direction | import-linter | dependency-cruiser | mix xref cycles | Zeitwerk constants vs `.ruby-layers.json` | Roslyn type resolution vs `.dotnet-layers.json`, cycles | beam call-graph cycles | `use`/path resolution vs `.rust-layers.json`, module cycles |
| types and lint | mypy strict, ruff | tsc strict, eslint | mix format, mix compile | rubocop | Roslyn analyzers via SARIF | erlc strong warnings as errors | clippy `-D warnings -D clippy::pedantic`, rustfmt |
| no comments, no docstrings | tokenizer | typescript scanner | elixir AST scanner | Ripper | Roslyn scanner | escript scanner | proc-macro2 token gaps, doc attributes |
| no pass-through functions, no imports of private modules | ast | typescript AST | elixir AST | Ripper | Roslyn scanner (pass-throughs) | escript scanner (pass-throughs) | syn scanner (pass-throughs) |
| no unreachable definitions | vulture | knip | BEAM abstract code scan | unused private methods | unused private members | escript scanner | rustc `dead_code` via clippy, unreferenced `pub` items |
| docs match the code: routes ledger, env vars, paths | regex over sources | regex over sources | regex over sources | regex over sources | regex over sources | regex over sources | regex over sources |
| the code parses on the interpreter that ships | Dockerfile base image vs `requires-python`, ruff, mypy and shebangs, then `ast` at that version | — | — | — | — | — | — |
| Sonar quality gate, zero issues, zero duplication | local SonarQube | local SonarQube | local SonarQube | local SonarQube | local SonarQube, SonarScanner for .NET | local SonarQube, sonar-erlang plugin | local SonarQube, built-in Rust analyzer + LCOV |

The Erlang gates compile and run eunit themselves with erlc and escript (OTP 25+); no rebar3 is required. `er.mutation` is marestail's own mutation tester: an escript rewrites one operator at a time (comparison, arithmetic, andalso/orelse swaps), recompiles, and runs the eunit suite per mutant — survivors fail the gate, `mutation_max` caps the mutants checked when a full pass is too slow. For the sonar tier, `marestail sonar setup` builds the [sonar-erlang](https://github.com/evolution-gaming/sonar-erlang) plugin jar once with docker (pinned to a commit, cached under `~/.config/marestail/`, so the build does not recur), mounts it into the SonarQube container's `extensions/plugins` and restarts the container if the plugin is not loaded yet. The gate imports the coverage `er.tests` already produced: the eunit run exports `.marestail/eunit.coverdata` (via `cover:export`), which the plugin parses into line coverage. It then fails closed when SonarQube shows no Erlang lines or no coverage metric, on top of the usual quality gate, issue, coverage and duplication checks.

The Rust gates need cargo with clippy and rustfmt, plus `cargo install --locked cargo-llvm-cov cargo-mutants`. marestail's own scanner (`marestail/rs/scan`, syn and proc-macro2) is built once per repo into `.marestail/rs-scan`, rebuilt when its source changes, and needs crates.io the first time. `rs.tests` runs `cargo llvm-cov --no-report` then writes `.marestail/rs-lcov.info` (for Sonar and CRAP) and the llvm JSON export; each test binary instruments the library separately, so hits are merged per line and per code region. Stable Rust has no branch coverage, so the gate reports unexecuted code regions, which catch an untaken `else` or match arm even when it shares a line with covered code. Without rustup, the gate points `LLVM_COV`/`LLVM_PROFDATA` at the system LLVM; it must match the LLVM version in `rustc -vV`. Dead code is two checks: rustc's `dead_code` lint fails `rs.lint`, and `deadcode` reports `pub` items whose name appears nowhere else in the sources, `tests/`, `examples/` or `benches/` (a name match, so a same-named item elsewhere hides one). Items defined in `lib.rs` count as the crate's API and are never reported. Pass-through detection skips trait impls, because the trait fixes their signature. There are no default layers; `rs.deps` checks `.rust-layers.json` (`[{"from": "src/domain", "forbid": ["src/web"]}]`) and always fails on module cycles. `rs.mutation` treats a mutant that times out as killed and ignores ones that do not compile. For Sonar, set `sonar.rust.lcov.reportPaths=.marestail/rs-lcov.info` and `sonar.rust.clippy.enabled=false` (the scanner container has no cargo, and `rs.lint` already runs clippy).

Acceptance is the same in every language: whatever command `[qa] cmd` names, run from `[qa] cwd`.

Tiers: `fast` (everything quick), `sonar` (adds the Sonar quality gate), `full` (adds mutation testing), `qa`.

`py.runtime` exists because every other gate runs in the repo's virtualenv, which is not what production runs. It reads the version off the last `FROM` in the Dockerfile (override with `[python] deploy_files`, or state it outright with `[python] runtime = "3.12"`), requires every version the tooling asserts to equal it, and parses each source at that version. Keeping mypy's `python_version` honest is half the point: typeshed then rejects stdlib names the shipped interpreter does not have. It skips when nothing declares a deployed interpreter.

A Next.js repo that will not move to vitest sets `[ts] runner = "jest"`: the gate runs the repo's own `node_modules/.bin/jest` with `--coverageProvider=babel` (v8 cannot express branch arms) and needs `[ts] sources` so untested files still appear in the report.

## Scope

`--scope changed` gates exactly the diff against `[git] base`: coverage counts only the changed lines and branches, CRAP only the functions the hunks land in, and lint, deps, mutation, comments, deadcode and depth only the changed files. The scope is the diff, so it follows the work — split one file into three or move a function into another existing file and the new hunks are gated wherever they land. `--focus PATH` (repeatable, or `[focus] paths` in marestail.toml) adds whole files or folders on top and treats them as fully changed; combined with `--scope all` it is a usage error. Sonar is filtered, not rescoped: the scanner still sees the whole project and the open issues, duplication and hotspots are cut down to the scope afterwards. `docs`, `qa` and `py.runtime` stay global by nature and print a scope note saying so. `--scope all` is unchanged and remains the default.

Mutation testing always runs on the diff: even without `--scope changed`, each mutation gate defaults to the files changed against `[git] base` (an empty diff skips the gate), because a whole-repo mutation pass is too slow to run on every gate. Set `[<lang>] mutation_scope = "all"` (e.g. `[elixir] mutation_scope = "all"`) to opt back into whole-repo runs; any other value fails the gate. An explicit `--scope changed` / `--focus` still wins over the config, and a repo whose `[git] base` ref does not resolve falls back to a full run, noted in the gate summary.

## Use

```sh
export PATH="$PATH:/path/to/marestail/bin"
cd your-repo
marestail install .          # marestail.toml, sonar-project.properties, CLAUDE.md / AGENTS.md, PERFORMANCE.md, Stop hooks
marestail install . --gitignore-generated   # also gitignore features/, qa/, tasks/, PERFORMANCE.md, perf/ and the Stop-hook configs, for repos where not everyone runs marestail
marestail sonar setup        # local SonarQube in docker, token in ~/.config/marestail
marestail gate               # fast tier, whole repo
marestail gate --tier full --scope changed
marestail gate --focus app/services   # the diff, plus a whole folder treated as fully changed
marestail graph              # module dependency graph, for the architect and for you
marestail depth              # prints, per module, the number of public symbols, the number of statements, and the ratio between them, marking wide-and-thin modules as shallow and files over 300 lines as long.
marestail run tasks/001.md   # Claude (default), or --agent agy|grok|cursor|kilo|kimi / MARESTAIL_AGENT
marestail run tasks/001.md --model claude-opus-5 --effort high   # both are stamped on every commit
```

`--effort` names the reasoning effort for the run and every backend carries it in the commit stamp. Claude takes it as `--effort` (`low`, `medium`, `high`, `xhigh`, `max`), agy as `--effort` (`low`, `medium`, `high`), Grok as `--reasoning-effort`, Kilo as `--variant`. Cursor has no flag for it: it goes inside the model, `--model 'claude-opus-4-8[context=1m,effort=high]'`, and `--effort` there only labels the commits. Kimi has no flag for it either, so `--effort` only labels the commits. `[agent] effort` in `marestail.toml` sets the default; `MARESTAIL_GROK_EFFORT` and `MARESTAIL_KILO_VARIANT` still work for those two.

## Overnight

`tools/overnight.sh tasks/000.md tasks/002.md ...` runs tasks in order, each to the hardener by default (`STOP_AT=qa` to include QA), stops at the first failure, waits out rate limits for up to six hours, and writes `.marestail/runs/overnight-<stamp>.md` with one section per task: exit code, minutes, HEAD, the role and verdict lines, any config proposals, and the performance changes. `AGENT`, `MODEL` and `EFFORT` in the environment pass the matching flags through. Start it detached: `nohup setsid tools/overnight.sh ... > /dev/null 2>&1 &`.

Kilo Code pipeline runs (`--agent kilo`) use `kilo run --auto --format json`, prompt on stdin, JSONL on stdout. Default model is StepFun Step 3.7 Flash (free) at variant `high`; `--model` overrides. A judge `VERDICT:` in the JSONL stream still counts. Kilo has no command Stop hook; the runner's four-hour cap is the timeout.

Kimi Code pipeline runs (`--agent kimi`) use `kimi -p --output-format stream-json`, prompt in argv, JSONL on stdout; `-p` mode needs no permission flags. `--model` passes through as `-m`. A judge `VERDICT:` in the JSONL stream still counts. Kimi has no command Stop hook; the runner's four-hour cap is the timeout.

`install --gitignore-generated` exists for repos where not everyone runs marestail: the flag adds the marestail-only working files — `features/`, `qa/`, `tasks/`, `PERFORMANCE.md`, `perf/`, the Stop-hook configs — to the target's `.gitignore`. `marestail.toml`, `sonar-project.properties`, `CLAUDE.md` and `AGENTS.md` are shared configuration and documentation: they are never gitignored.

## Watch

`marestail watch [paths...]` opens a live curses TUI of every repo with a running marestail pipeline — beds whose `.marestail` directory has a live worker process (`--all` shows every bed, running or not). With no paths it reads `~/workspace` when that exists, else the current directory. Each repo is a bed, and the active worker's row carries its role, elapsed time and a scrolling one-line tail of its latest output, so you can see what the fleet is doing without opening a single log. While a claude worker runs, its bed grows up to three dim lines tailed live from the agent's session transcript — its latest thinking, text and tool calls — so progress is visible between step finishes. Enter on a worker opens its full conversation: the prompt it was sent, the handoff it wrote, the result it returned. Arrows or j/k move, Enter opens, q quits. Stdlib curses only, nothing to install. The panels are a registry built to be extended — module graph and coverage views are planned.

## Pipeline

| Step | Kind | Gate | Does |
|---|---|---|---|
| specifier | worker | none | Gherkin scenarios and a QA procedure from the task |
| critic | judge | none | judges the spec against the task; bounces to a fresh specifier until it passes; then a human approval pause |
| coder | worker | fast | implements; must trace every scenario to a test in its handoff |
| cleaner | worker | sonar | readability without comments, CRAP, Sonar |
| architect | worker | sonar | draws module boundaries, moves code, tightens the dependency contracts |
| perf | judge | none | benchmarks every `perf/` bench on the start commit and HEAD; flags degradations and improvements; bounces to a fresh coder only for a fix inside the spec |
| hardener | judge | full | judges the diff and the mutation report; bounces to a fresh coder until it passes |
| qa | worker | qa | turns the QA procedure into an executable end-to-end test |

Workers edit and commit. Judges write one verdict file and nothing else (perf may also write `perf/**`); the runner discards any other edit a judge makes. Handoff and verdict files are runtime state under `.marestail/`, never committed: when a worker passes verification the runner folds its handoff into that role's commit message, and a judge's verdict becomes an empty commit carrying the verdict. Every commit a run produces starts with the model and effort that produced it, `[claude-opus-5 high] coder handoff`; the runner rewrites the subject of any commit a worker made without one, so the stamp is deterministic rather than something the agent has to remember. With no `--model` the backend name stands in for it, and with no effort the stamp is the model alone. `git log` on the branch is the record, and a role in a fresh clone reads its predecessors from there. When a pipeline completes, the task's handoff files are archived under `.marestail/runs/`. Every role runs in a fresh session with a short prompt: the role file, the task, the earlier handoffs, and how to finish. Judges also get the gate report.

After every worker the runner checks, deterministically: the handoff exists, the tree is committed, no frozen file changed, the gate for that tier passes, and for the coder that every scenario in the feature file is traced to a test that exists. Anything failing goes back to the same role as feedback until it passes (`--retries N` caps it; default is unlimited). A judge's gate failing is a bounce regardless of what the judge wrote. A judge bounces as many times as it takes, with one stop: if it writes the same numbered findings twice in a row, the worker is not making progress and the pipeline stops for a human.

## Performance

The `perf` judge measures how each task changed performance. Before every attempt the runner checks out detached worktrees in temp directories and lists them in `.marestail/perf/trees.json` and the prompt: `baseline` at the task's start commit (recorded in `.marestail/runs/<task>/start-commit` when `marestail run` starts, or the merge-base with `[git] base` when that is missing), `head` (the repo itself), and, on the first perf run in a repo only, `pre-marestail`, the parent of the commit that added `marestail.toml`. The worktrees, their databases and `trees.json` are removed after the attempt.

Benchmarks are executable `perf/bench_*` scripts that the perf agent writes and extends, committed with every perf verdict. Workers cannot edit `perf/**` or `PERFORMANCE.md`, and every gate ignores the root `perf/` directory. Every perf run re-runs every bench on every tree, so each row is a full snapshot. Samples only come through `marestail perf run perf/bench_<name> --tree <tree> [--samples N] [--db]`: it runs the script in that tree with `MARESTAIL_PERF_TREE`, `MARESTAIL_PERF_TREE_PATH` and `MARESTAIL_PERF_SAMPLE` set, reads JSON lines `{"target": "GET /donations", "unit": "ms", "better": "lower", "value": 12.3}` (or `{"target": "GET /donations", "absent": true}` when a tree has no such target), and appends them to `.marestail/perf/samples.jsonl`. The runner, not the agent, turns those into p50 and p95 per target and tree.

The runner rejects a perf verdict and retries it, with the reasons in the next prompt, when a bench has no samples on a tree, a target has fewer than `[perf] min_runs` samples, a column already in `PERFORMANCE.md` was not re-measured, or a degraded or improved target is missing from the verdict. A target is degraded or improved when HEAD differs from the baseline measured in the same run by at least `[perf] threshold_percent`. Perf bounces to the coder only when a concrete change inside the task and its feature file would recover a degradation without changing a scenario (an N+1 query, repeated work in a loop, a missing batch, an unbounded payload); a degradation the specification makes inherent passes, listed under `## Degradations` with the reason, and every improvement is listed under `## Improvements`. `marestail run` ends with a `## Performance changes` summary.

On a PASS the runner updates `PERFORMANCE.md`, which `marestail install` creates: one row per task (a re-run replaces its row), one column per target and metric, and a `pre-marestail` row the first time. A cell holds HEAD's value and its change against the start commit: `12.1ms (-2.4%)`, `15.1ms (+21.8%) ⚠`, `9ms (-27.4%) ✓`, `0.02ms (new)` or `removed`. `Rows` records the performance database size the row was measured with.

A bench that touches a database runs with `--db`. With `[perf.db] migrate` set, marestail runs a local Postgres in Docker at the repo's version: `[perf.db] image`, else the first `postgres:` image in a root compose file, then in `.github/workflows`, then `.tool-versions`, else `postgres:18`; every tree uses the same image. For each tree schema it builds a golden data directory once: it migrates an empty `bench` database, runs `perf/seed.sql` (with the psql variable `:rows`) or an executable `perf/seed*` (with `MARESTAIL_PERF_ROWS`), requires every table to hold `[perf.db] rows` rows, vacuums and stops cleanly. `rows` defaults to 50,000,000 per table; `0` builds an empty migrated database with no seed script, and `MARESTAIL_PERF_DB_ROWS` overrides it for one run. Goldens live in the `marestail-perf-pgdata` volume, cached by repo, image, schema (the files matching `[perf.db] migrations`, else the commit), seed and row count. Before every `--db` sample the tree's container is replaced by a fresh Postgres on a `cp --reflink=always` copy of the golden, so the Docker data root must be on a reflink-capable filesystem such as btrfs or XFS. For scale, on btrfs with `postgres:16`, a golden with one 50M-row table took about 140 s to build and 6.9 GB of disk, and a reset about 1.7 s (`tools/perf-db-scale.py` measures it). A golden build refuses to start when the disk estimate exceeds the free space. `marestail perf db golden --tree <tree>` builds in the background and `status` shows progress, `url --tree <tree>` prints the URL to start a tree's app against (it stays fixed across resets), `prune` deletes this repo's goldens the current run does not need, and `down` removes the containers but keeps the volume. The password is generated into `~/.config/marestail/perf-db.json`.

`[perf]` keys: `enabled` (default `true`: perf runs in every pipeline unless set to `false`), `threshold_percent` (10), `min_runs` (10), `sample_timeout` (600 seconds per sample, excluding the reset), `setup` (a command run in each extra worktree, e.g. `uv sync`). `[perf.db]` keys: `migrate`, `url_env` (`DATABASE_URL`), `rows` (50000000), `migrations` (`[]`), `skip_tables` (the usual migration bookkeeping tables), `image`, `port` (55432 for `head`, `baseline` +1, `pre-marestail` +2), `min_free_gb` (50).

## Writing tasks

One task is one vertical slice: a user-visible outcome, thin, through every layer it needs. `marestail install` drops `tasks/README.md` into the repo with the guidance; the critic bounces a spec that delivers a layer instead of a slice unless the task declares itself a refactor.

## Adapting for new languages

Copy the shape, not the tools. Per-language gates live in `marestail/gates/`; a new language is one file per gate plus a section in `marestail.toml`.

Also there is a skill in the repo (`.claude/skills/add-language/` and `.agents/skills/add-language/`; Grok scans both) which should make this process relatively (?) trivial.

## Inspo

Inspiration for this came from this brilliant interview with uncle bob, would recommend it if you're interested in ideas around delivering quality software in the age of AI! https://www.youtube.com/watch?v=zcLPGC-tvgk&t=1s
