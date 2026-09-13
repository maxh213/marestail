# marestail perf tsar

## Overview
Add a `perf` judge to the marestail pipeline.

For each task it runs every benchmark in `perf/` against up to three git trees:
- a worktree at the task's start commit (the baseline);
- HEAD;
- the first time only, a worktree at the commit from before marestail was added.

All samples go through a `marestail perf run` wrapper, which records every sample. Before each sample of a database-touching bench, it resets a local Docker Postgres. That Postgres runs the same version as the repo, and the reset is a reflink copy of a golden data directory seeded with 50 million rows per table. The runner, not the agent, turns the samples into results.

Performance changes are always flagged, whether degradations or improvements. The judge bounces to the coder only when it can name a fix that stays inside the task's specification.

Each completed task adds one row to a living `PERFORMANCE.md` matrix: rows are tasks, columns are target·metric. `marestail install` creates that file. Everything runs locally.

## Context & Constraints

Repo: `maxh213/marestail`. You are in the worktree `/home/max/workspace/marestail-marestail-perf-tsar` on branch `feat/marestail-perf-tsar`, based on `main` (88c3adb), which includes the Rust gates.

Code style, which the whole repo follows:
- stdlib-only Python 3.12+, no new dependencies. There is no Postgres driver and no YAML parser: talk to the database with `docker exec … psql` through `marestail.shell.run`, and read YAML with regexes.
- No comments or docstrings; names carry the meaning.
- Small functions, dataclasses.
- Match the surrounding files.

Machine facts, verified 2026-09-13:
- Docker 29.7.2 with data root `/var/lib/docker` on **btrfs**, and 593G free.
- Inside a `postgres:16` container, `cp --reflink=always` of a 2 GiB file on a Docker named volume took **8 ms** and produced an identical file. Reflink copies of whole Postgres data directories are therefore near-instant here.
- Scale probe, 2026-09-13, `postgres:16`, one table `(id bigint, amount numeric, created_at timestamptz, note text)` with 50M rows, following decisions 16–17 by hand:
  - **Seeding:** insert 112 s, primary key 24 s, `VACUUM (ANALYZE)` + `CHECKPOINT` 13 s, **150 s total**.
  - **Golden size:** **12.5 GB** (`du -sb` 12504381043).
  - **10 full resets:** **median ≈ 1.97 s**, min 1.36 s, max 2.58 s. Each breaks down as `docker rm -f -v` ~250 ms, `rm -rf` of the old work dir plus the reflink copy ~1.2 s, `docker run -d` ~400 ms, and `pg_isready` ~100 ms.
  - **First query:** an indexed lookup straight after a reset took 100–150 ms, including `docker exec` overhead.
  - **Extrapolation, not measured:** disk grows linearly, at about 12.5 GB per 50M-row table per golden.
- Images `postgres:16`, `postgres:16-alpine`, `postgres:15` and `postgres:13.1-alpine` are present locally.
- Other containers are running and must not be disturbed: `animus-chat-db-1`, `data_tracker-db-1`, `data_tracker-redis-1`, `marestail-sonarqube`.
- Of the target repos, animus-chat declares `postgres:16` in `docker-compose.yml` and in `.github/workflows/ci.yml` and `deploy.yml`.

Files you will touch, and what they do today:
- `marestail/pipeline.py`: `Worker` and `Judge` dataclasses, plus `PIPELINE = specifier, critic, coder, cleaner, architect, hardener, qa`.
- `marestail/runner.py`:
  - `run_pipeline` loops over the steps.
  - `run_judge_loop` bounces to a worker, then re-runs the judge, and stops when the same numbered findings repeat twice.
  - `run_judge` invokes the agent and calls `discard_edits`, which runs `git checkout -- .` and `git clean` to wipe every non-`.marestail/` edit. It then calls `parse_verdict` and `record_commit`, which makes an empty commit carrying the verdict.
  - `archive_handoffs` runs when the pipeline completes.
  - `proposals_summary` prints a `## Config changes…` section that `tools/overnight.sh` greps into its summary.
- `marestail/prompts.py`: `judge_prompt` and `verdict_instructions`. The role text comes from `roles/<name>.md`.
- `marestail/cli.py`: argparse subcommands (`gate`, `run`, `install`, `sonar`, `watch`, `graph`, `depth`).
- `marestail/sonar/setup.py`: the existing pattern for a docker-managed local service with a secret in `~/.config/marestail/sonar.json`.
- `marestail/freeze.py`: `GATE_CONFIG`, `SPEC = features/**, qa/**, tasks/**`, `ALLOWED` per role, `matches`/`matches_any`.
- `marestail/install.py`: `install()`, `copy_if_missing`, `GITIGNORE_LINES`, `GITIGNORE_GENERATED_LINES` (used by `--gitignore-generated`), `extend_gitignore`.
- `marestail/tui/theme.py`: `JUDGE_ROLES = {"critic", "hardener"}`.
- Gate file walkers:
  - `SKIP_DIRS` in `marestail/gates/comments.py`, `docs.py`, `py_runtime.py` and in `marestail/depth.py`.
  - Default exclusions in `marestail/gates/sonar.py`.
  - The vulture excludes in `marestail/gates/deadcode.py`.
  - The language gates' own source discovery, including `marestail/rust.py` `sources()` used by the `rs_*` gates.
- Tests: there is no pytest suite.
  - `tools/test-agent-backends.py` is a plain script with an `expect(name, got, want)` helper that prints `agent backends ok`.
  - `tools/dryrun.sh` builds a throwaway repo at `/tmp/marestail-dryrun/repo` (task `tasks/t.md`, so the task stem is `t`). It drives a full pipeline with `tools/stub-claude`, following the action lines in `tools/dryrun-plan.txt`, and exits non-zero on failure. It puts `bin/` on `PATH`.
- `README.md`: the Pipeline table and the install section. `templates/tasks-README.md` names the pipeline order.

### Decisions already made (do not re-litigate)

#### The step
1. **Name and placement.** The step is named `perf`, and the role file is `roles/perf.md`. It is a `Judge` placed after `architect` and before `hardener`, so the hardener reviews any perf fix. Its tier is `None` and its bounce target is `coder`. It never bounces to the specifier, critic or architect: if a named bounce target is anything other than `coder`, the runner treats it as `coder`.
2. **On by default.** It runs unless `marestail.toml` has `[perf] enabled = false`, in which case the runner prints `perf disabled in marestail.toml; skipping` and moves on without making a commit. This deliberately changes existing target repos and `tools/overnight.sh` runs, since its default `STOP_AT=hardener` now includes perf.
3. **`[perf]` config keys.** None is required.
   - `enabled` (default true).
   - `threshold_percent` (default 10). A change whose magnitude is below this is `unchanged`.
   - `min_runs` (default 10). The minimum number of samples per tree per target.
   - `sample_timeout` (default 600). Seconds allowed for one sample, excluding the database reset.
   - `setup` (default empty). A shell command the runner executes inside each extra worktree before the agent starts, e.g. `uv sync` or `npm ci`.

   `[perf.db]` keys are in decision 15.

#### Commits measured
4. **Which commits are measured.**
   - **Start commit.** At the start of `run_pipeline`, if `.marestail/runs/<task>/start-commit` does not exist, write the current HEAD sha to it. When the pipeline completes, move that file into the archived handoffs folder, so a later re-run of the same task records a fresh start. If perf runs and the file is missing (e.g. `--from perf` on a fresh checkout), use `git merge-base <[git] base> HEAD` and say so in the perf prompt.
   - **Pre-marestail commit.** Only needed when `PERFORMANCE.md` has no data rows yet, i.e. this is the first perf run in the repo, whatever the task is called. It is the parent of the oldest commit that added `marestail.toml`: `git log --diff-filter=A --reverse --format=%H -- marestail.toml`, first line, then `^`.
     - If no commit added `marestail.toml`, or that commit has no parent, there is no pre-marestail commit. The runner prints `no commit before marestail.toml; skipping the pre-marestail row` and continues.
     - If the table already has data rows, there is no pre-marestail row. It is written once, ever.
5. **Worktrees and `trees.json`.** Before each perf judge attempt, the runner:
   - creates a detached worktree at the start commit, plus one at the pre-marestail commit when decision 4 calls for it. Each goes in its own `tempfile.mkdtemp(prefix="marestail-perf-")` directory, outside the repo, so no gate or tool discovers it;
   - runs `[perf] setup` in each, if set;
   - writes `.marestail/perf/trees.json`: `{"task": "<stem>", "image": "<resolved image or null>", "trees": [{"tree": "baseline"|"head"|"pre-marestail", "sha": "…", "path": "…"}]}`, where `head`'s path is the repo root;
   - truncates `.marestail/perf/samples.jsonl`;
   - passes the trees in the prompt.

   After the attempt, in a `finally` that also runs when a bounce is coming, it:
   - removes each worktree (`git worktree remove --force`, then `git worktree prune`);
   - removes this repo's tree database containers and work directories (decision 17), keeping goldens;
   - deletes `trees.json`.

#### Benchmarks and samples
6. **Scripts live in committed `perf/`.**
   - Bench scripts are executable files named `perf/bench_*` (any language, with a shebang). The seed script is `perf/seed.sql` or an executable `perf/seed` / `perf/seed.*`.
   - Every perf run runs every `perf/bench_*` script against every tree, so each row is a full snapshot. The agent also writes or extends benches for every endpoint or function the task added or changed.
   - Scripts always come from HEAD's `perf/`, even when run against the baseline or pre-marestail tree.
   - `perf/**` and `PERFORMANCE.md` are added to `freeze.SPEC`, so workers (including the coder) can't edit them. Only perf's judge write-list lets them through.
7. **Judge write-list.**
   - `Judge` gains `writes: tuple[str, ...] = ()`. For perf it is `("perf/**", "PERFORMANCE.md")`.
   - `discard_edits` keeps paths matching `writes` (use `freeze.matches_any`) and discards everything else as it does today.
   - Before `record_commit`, the runner stages the kept paths with `git add -A -- <paths>`, skipping any path `git check-ignore` reports as ignored. So `perf/` scripts are committed with every perf verdict, PASS or BOUNCE, and the executable bit is kept.
8. **`marestail perf run <script> --tree <tree> [--samples N] [--db]` is the only way samples are taken.**
   - It exits 2 with `no perf run in progress` when `.marestail/perf/trees.json` is missing, and exits 2 when `<script>` is not an executable `perf/bench_*` file in the repo root or `<tree>` is not in `trees.json`.
   - `--samples` defaults to 1. The agent interleaves trees by calling it in rounds.
   - For each sample:
     1. If `--db`, reset that tree's database (decision 17) and record `reset_ms`.
     2. Execute the script with cwd = the tree's path and a `sample_timeout` limit. The environment passes through and adds:
        - `MARESTAIL_PERF_TREE`, `MARESTAIL_PERF_TREE_PATH`, `MARESTAIL_PERF_SAMPLE`;
        - with `--db`, `MARESTAIL_PERF_DATABASE_URL`, `MARESTAIL_PERF_DATABASE` (always `bench`), `MARESTAIL_PERF_DB_CONTAINER` (the tree's container name), and the `[perf.db] url_env` variable set to the same URL.
     3. The script prints JSON lines on stdout, either `{"target": "…", "unit": "ms", "better": "lower"|"higher", "value": 12.3}` or `{"target": "…", "absent": true}` (the target does not exist in this tree). Other stdout lines pass through to the terminal.
     4. Append one record per JSON line to `samples.jsonl`: `tree`, `sha`, `script`, `sample`, `db` (bool), `reset_ms` (or null), `target`, and `unit`/`better`/`value` or `absent`.
   - A sample that exits non-zero, times out, or prints no valid JSON line appends nothing, and the command exits 1 naming the sample.
   - The script owns warm-up. With `--db`, every sample starts on a freshly started, cold Postgres, and an app under test has just lost its connections, so the script makes one untimed request or call before timing.
9. **Compiling results (the runner, after the agent finishes).** The runner reads `samples.jsonl` and writes `<handoff stem>.results.json` in the handoffs folder.
   - For each (target, tree), it computes `p50 = statistics.median(values)` and `p95 = sorted(values)[ceil(0.95 * n) - 1]`.
   - Each target yields two measurements, metrics `p50` and `p95`, with fields `target`, `metric`, `unit`, `better`, `pre_marestail`, `baseline`, `head`, `runs` (the minimum sample count over trees with values), and `script`.
     - A tree with only `absent` records gives `null`.
     - `pre_marestail` is `null` when no pre-marestail tree exists.
   - The column key is `f"{target} {metric}"`.

   It is invalid (decision 11 rejects it) when:
   - a target's `unit` or `better` differs between records;
   - a tree has both values and `absent` for one target;
   - a tree has neither values nor `absent` for a target that some tree measured;
   - a tree with values has fewer than `min_runs`;
   - a script was run with `--db` in one tree and without it in another;
   - some `perf/bench_*` script at HEAD has no records for some tree;
   - `baseline` and `head` are both null.
10. **Deterministic classification**, always comparing this run's `baseline` to `head`:
    - `new` when `baseline` is null.
    - `removed` when `head` is null.
    - Otherwise compute `change = (head - baseline) / baseline * 100`. When `baseline == 0`, the change is 0 if `head == 0`, otherwise ±100 in the direction of `head`.
    - `unchanged` when `abs(change) < threshold_percent`.
    - Otherwise `degraded` when the change is in the worse direction for `better`, and `improved` when it is in the better direction.
11. **Verdict audit.** The runner rejects a perf verdict when:
    - the compiled results are invalid (decision 9); or
    - any column already in `PERFORMANCE.md` has no measurement; or
    - any `degraded` or `improved` target string does not appear verbatim in the verdict file.

    It then retries the judge, like a missing verdict does today (a fresh `trees.json` and an empty `samples.jsonl`), and passes the problems into the next prompt. `judge_prompt` gains an optional `feedback` argument, rendered as `# Why your verdict was rejected`. A PASS with zero measurements is valid only when `perf/` has no `bench_*` scripts and the table has no columns.
12. **Bounce vs flag rule (in `roles/perf.md`).**
    - **BOUNCE** to the coder only when a concrete change inside the task and its feature file would recover a degradation without changing any scenario's behaviour. Examples: an N+1 query, repeated work inside a loop, a missing batch, an unbounded payload, a missing index when a migration is already in the task's scope. Each numbered finding names the file:line, the target, the measured change, and the fix.
    - **PASS** otherwise. A degradation the specification makes inherent (e.g. a new required DB query on an endpoint) goes under `## Degradations` as `- <target>: +N% <metric>, accepted: <why it is inherent, naming the scenario>`. Every improvement goes under `## Improvements` as `- <target>: -N% <metric>`. This includes degradations or improvements in code the task did not touch, which surface because every bench is re-run.
    - Any bench that touches a database must use `--db`. When one is needed but `[perf.db]` is not configured, the agent benches what it can, PASSes, and explains under `## Setup needed` what to add to `marestail.toml`.
    - Judge bounce limits are unchanged: unlimited bounces, and the existing same-findings stop.

#### The living table
13. **One row per task, one column per target·metric.** On a perf `PASS` only, the runner rewrites `PERFORMANCE.md` at the repo root. If the file is missing, it creates it from `templates/PERFORMANCE.md`.
    - **Header:** `| Task | Commit | Date | Rows | <column key> | <column key> | … |`.
    - **Rows cell:** the effective `[perf.db] rows` (decision 15) when this run recorded any `--db` sample, and `—` otherwise. It is written as a plain integer, e.g. `50000000` or `0`. The pre-marestail row gets the same value as the task row written with it.
    - Existing columns keep their order. Column keys first seen in this run are appended on the right, in results-file order (p50 before p95 per target).
    - **Rows, in order:**
      - If decision 4 produced a pre-marestail commit, a row `pre-marestail` comes first. Commit = that short sha. Cells hold the `pre_marestail` value with its unit, no suffix, and `—` for null.
      - Then one row per task, keyed by the Task cell (the task stem). A task that already has a row (a re-run) is replaced in place. A new task is appended at the bottom.
    - **Task row cells:**
      - Commit = the short HEAD sha that was measured. Date = `YYYY-MM-DD`.
      - Each measured column is `<head><unit>` followed by a suffix for its decision-10 status:

        | Status | Suffix | Example |
        |---|---|---|
        | `unchanged` | ` (±N.N%)` | `12.1ms (-2.4%)` |
        | `degraded` | ` (+N.N%) ⚠` | `15.1ms (+21.8%) ⚠` |
        | `improved` | ` (-N.N%) ✓` | `9ms (-27.4%) ✓` |
        | `new` | ` (new)` | `0.02ms (new)` |
        | `removed` | the whole cell is `removed` | `removed` |

        Percentages are signed by raw direction (head vs baseline), rounded to one decimal. Values render with `f"{value:g}"`.
    - Earlier rows get `—` in newly appended columns. `|` in any header or cell is escaped as `\|`. Everything above the table header is preserved byte-for-byte.
    - The file is staged and committed in the verdict commit, unless it is gitignored, in which case it is updated on disk only.
14. **Surfacing flags.** When the pipeline ends (complete or stopped), the runner prints a `## Performance changes` section after the proposals summary. It lists:
    - the Postgres image used, and where it was found (decision 15);
    - the effective row count, and whether it came from `MARESTAIL_PERF_DB_ROWS`, `marestail.toml` or the default;
    - every `degraded`/`improved`/`removed` column from the last perf run of this task, with status, change and column key;
    - any `## Setup needed` text from that verdict.

    `tools/overnight.sh` captures that section the same way it captures `## Config changes`.

#### The performance database (Postgres only, all local)
15. **Config and version matching.**
    - The DB is available when `[perf.db] migrate` is set. `perf run --db` without it exits 2 with `configure [perf.db] migrate in marestail.toml`.
    - Keys:

      | Key | Default | Meaning |
      |---|---|---|
      | `migrate` | none | Shell command, run in a tree's path with the URL env set, that brings an empty `bench` database to that tree's schema, e.g. `alembic upgrade head`, `mix ecto.migrate`. |
      | `url_env` | `"DATABASE_URL"` | Env var the app and scripts read the URL from. |
      | `rows` | `50000000` | Minimum rows per table in a golden. Any integer ≥ 0; e.g. `10000000` for a lighter run, `0` for an empty, migrated-only database. |
      | `min_free_gb` | `50` | Free space required on the Docker data root before a golden build when no earlier golden gives a size estimate (decision 16). |
      | `migrations` | `[]` | Globs of schema-defining files used in the golden hash. |
      | `skip_tables` | `["schema_migrations", "alembic_version", "ar_internal_metadata", "__EFMigrationsHistory"]` | Tables exempt from the row check. |
      | `image` | detected | Postgres image; see below. |
      | `port` | `55432` | Host port of the `head` tree's database; `baseline` uses `port + 1`, `pre-marestail` uses `port + 2`. |

    - **The image matches the repo's Postgres version.** It is resolved once per perf attempt from HEAD's files, and the same image serves every tree, so the comparison measures code, not Postgres versions. First match wins:
      1. `[perf.db] image`.
      2. The first `image: postgres:<tag>` in a root-level `docker-compose*.yml`, `docker-compose*.yaml`, `compose*.yml` or `compose*.yaml`.
      3. The first `postgres:<tag>` in `.github/workflows/*.yml` / `*.yaml`.
      4. A `.tool-versions` line `postgres <version>`, which becomes `postgres:<major>`.
      5. Otherwise `postgres:18`, printing `no Postgres version found in the repo; using postgres:18`.

      The resolved image and its source (e.g. `docker-compose.yml`) are printed, recorded in `trees.json`, and shown in the perf prompt. A missing image is pulled with `docker pull`.
    - The superuser password is generated on first use and stored in `~/.config/marestail/perf-db.json` (mode 0600), following `marestail/sonar/setup.py`.
    - All database files live in one named volume, `marestail-perf-pgdata`, mounted at `/perf` in every container this feature starts. Goldens go in `/perf/goldens/<name>/`; tree work directories go in `/perf/work/<repo key>-<tree>/`, where the repo key is the first 12 hex of sha256 of the repo root path.
    - File operations inside the volume (copy, move, delete, size) run through `docker exec` in one long-lived helper container, `marestail-perf-files`. It is started on demand from the non-alpine variant of the resolved image (strip a `-alpine` suffix from the tag), because busybox `cp` has no `--reflink`. Copies use `cp -a --reflink=always`, which keeps numeric ownership. If it fails, the command fails with `reflink copy failed: the Docker data root must be on a reflink-capable filesystem such as btrfs or XFS`. There is no slow-copy fallback.
    - Tree containers are named `marestail-perf-db-<repo key>-<tree>`, bound to `127.0.0.1` on the tree's port, with database `bench`.
    - Every `docker rm` uses `-f -v`, so the image's anonymous `VOLUME` is removed too.
    - **Effective row count:**
      - `MARESTAIL_PERF_DB_ROWS`, when set, overrides `[perf.db] rows` for that process and everything it starts. This lets a human do a one-off lighter or empty run without editing the frozen `marestail.toml`.
      - The effective value comes from the environment, then `marestail.toml`, then the default. It is recorded in `trees.json` and shown in the perf prompt.
      - A value that is not an integer ≥ 0, from either source, fails the perf step with `[perf.db] rows must be a whole number ≥ 0, got <value>`.
    - `MARESTAIL_PERF_DB_VOLUME`, `MARESTAIL_PERF_DB_PREFIX` (replaces `marestail-perf-` in every container name) and `MARESTAIL_PERF_DB_PORT` override the volume, name prefix and port. Tests use them.
    - The code never touches a container or volume whose name does not start with the prefix.
16. **Golden data directories**, one per (tree schema, image).
    - **Name:** `golden_<first 16 hex of sha256>`. The hash covers:
      - the repo root path;
      - the resolved image;
      - the tree's git blob ids for files matching `[perf.db] migrations`, or the tree's commit sha when `migrations` is empty;
      - the bytes of HEAD's seed script, only when rows > 0;
      - the effective `rows`.
    - **Build** (`marestail perf db golden --tree <tree>`):
      1. Start container `marestail-perf-golden-<name>` from the resolved image with `PGDATA=/perf/goldens/<name>.tmp`, a random localhost port (read back with `docker port`), and `POSTGRES_DB=bench`.
      2. Wait for `pg_isready`.
      3. Run `[perf.db] migrate` in the tree's path, with the URL env pointing at that container and `MARESTAIL_PERF_DB_CONTAINER` set to it.
      4. When rows is 0, skip steps 4 and 5: the golden is the migrated, empty schema, and no seed script is needed. When rows > 0 and HEAD has no seed script, fail with `[perf.db] rows = <n> needs a perf/seed script`. Otherwise, run the seed. `perf/seed.sql` goes through `docker exec -i … psql`; an executable seed runs with cwd = the tree path and the env from decision 8 plus `MARESTAIL_PERF_ROWS`. The seed must fit that tree's schema, e.g. by checking `information_schema`, because older trees may lack tables.
      5. Check that every table in a non-system schema, except `skip_tables`, has `count(*) >= rows`.
      6. Run `VACUUM (ANALYZE)` and `CHECKPOINT`.
      7. Stop cleanly with `docker stop -t 600` and remove the container.
      8. Move `<name>.tmp` to `<name>` in the helper, and write two files into it:
         - `META.json`: `{"rows": <n>, "bytes": <du -sb>, "image": "…", "built_at": "<ISO 8601>"}`;
         - `READY`.

         `perf db status` shows the golden's size from `META.json`.

      On any failure it removes the container and `<name>.tmp`, and records status `failed` with the error.
    - **Disk check, before step 1 of every build with rows > 0:**
      - Read free bytes on the volume with `df -B1 --output=avail /perf` in the helper.
      - **Estimate:** find the largest ready golden of this repo (same repo root in its hash, and `META.json` rows > 0). The estimate is `1.2 × its bytes × effective rows / its rows`. With no such golden, it is `min_free_gb` GiB.
      - When free bytes < estimate, refuse the build without creating anything. Record status `failed` with `not enough disk for <name>: need ~<estimate GB> GB, have <free GB> GB free on the Docker data root; run marestail perf db prune or lower [perf.db] rows`.
      - Builds with rows = 0 skip the check.
      - Put the estimator in a pure function so it is testable without Docker.
    - **Background builds:** `golden` detaches by default, writes progress to `~/.config/marestail/perf-db/<name>.log`, and returns immediately. `--wait` blocks instead. Agent shell commands time out, so the role tells the agent to start builds and poll `marestail perf db status`, which prints one line per tree of `trees.json`: `<tree> <image> <golden name> building|ready|failed|missing <elapsed>`.
    - **Runner pre-build:** before invoking the agent, when `[perf.db] migrate` is set and either the effective rows is 0 or HEAD has a seed script, the runner builds every missing golden for the current trees with `--wait`, one at a time, printing progress. Failures go into the prompt, so the agent can fix the seed.
    - Goldens persist across runs. `marestail perf db prune` deletes every golden directory not needed by the current `trees.json`, or all of them when there is none. `marestail perf db down` removes every container with the prefix and keeps the volume.
17. **Reset before every `--db` sample.** In order:
    1. `docker rm -f -v marestail-perf-db-<repo key>-<tree>`.
    2. In the helper: `rm -rf /perf/work/<repo key>-<tree>`, then `cp -a --reflink=always /perf/goldens/<name> /perf/work/<repo key>-<tree>`, then remove the copied `READY` file.
    3. `docker run -d` the tree container from the resolved image with `PGDATA` set to that work directory and the tree's port.
    4. Poll `pg_isready` through `docker exec` until ready, for up to 120 s.

    The whole sequence is timed as `reset_ms`. It fails with the `perf db status` line when the golden is not `ready`.

    `marestail perf db url --tree <tree>` prints `postgresql://postgres:<password>@127.0.0.1:<tree port>/bench`. The port is fixed per tree, so the agent can start that tree's app against it once. The app loses its connections at every reset and must reconnect; the script's untimed warm-up absorbs this.

#### Install and gates
18. **Install.**
    - `templates/PERFORMANCE.md` holds:
      - a `# Performance` heading;
      - a sentence saying the perf role maintains it with one row per task;
      - the sentence "Percentages compare each task's HEAD against its start commit, measured back to back in the same run, not against the row above.";
      - the header row `| Task | Commit | Date |` with its separator row.
    - `install()` copies it with `copy_if_missing`.
    - `PERFORMANCE.md` and `perf/` are appended to `GITIGNORE_GENERATED_LINES`.
19. **Gates ignore the target's root `perf/`.** Bench and seed scripts are not product code. Exclude the root-level `perf/` from every gate's skip or exclusion mechanism: the `SKIP_DIRS` sets, sonar default exclusions `perf/**`, deadcode excludes, and any language gate that discovers sources by walking from a root that could contain `perf/`. Where a mechanism can only match directory names, not root-relative paths, note in PROGRESS that a nested product `perf/` directory would also be skipped. Audit each file in `marestail/gates/` and write the list of mechanisms you changed into PROGRESS.

### Suggested module layout
- Use a package, `marestail/perf/`, with narrow modules:
  - `trees.py`: commits, worktrees, `trees.json`;
  - `samples.py`: the `perf run` wrapper and the log;
  - `results.py`: compile, validate, classify, audit;
  - `table.py`: `PERFORMANCE.md`;
  - `image.py`: Postgres version detection;
  - `db.py`: helper and tree containers, goldens, reset, url, prune, down.
- CLI wiring goes in `marestail/cli.py` as a `perf` subcommand with `run` and `db golden|status|url|prune|down`. `runner.py` only calls into the package.

## Phases

### Phase 1: The perf step exists and is skippable
- Add `writes` and the coder-only target rule to `Judge` / `runner.run_judge_loop` (decisions 1, 7).
- Insert `Judge("perf", None, bounce_to="coder", writes=("perf/**", "PERFORMANCE.md"))` between architect and hardener in `PIPELINE`.
- Implement the `[perf] enabled = false` skip in `run_step` (decision 2).
- Extend `discard_edits` with keep patterns, and stage the kept, non-ignored paths before the verdict commit (decision 7).
- Add `perf/**` and `PERFORMANCE.md` to `freeze.SPEC`.
- Add `perf` to `JUDGE_ROLES` in `marestail/tui/theme.py`.
- Write `roles/perf.md` covering:
  - the deep analysis: read `git diff <start>..HEAD`, identify touched endpoints and functions, profile any degradation to find its cause;
  - running every bench on every tree through `marestail perf run`, interleaving trees, with ≥ `min_runs` samples, and an untimed warm-up in each sample;
  - `--db` for every database-touching bench, starting golden builds in the background and polling their status, starting each tree's app against `marestail perf db url`;
  - the bounce vs flag rule and the verdict sections (decision 12);
  - the fact that it edits only `perf/**`.

  Keep it under 25 lines.
- Create `tools/test-perf.py`, in the style of `tools/test-agent-backends.py`, covering:
  - the pipeline order;
  - that the target is forced to coder;
  - the freeze paths for coder vs perf;
  - that `discard_edits` keeps `perf/x.py` and discards `src.py`, in a temporary git repo;
  - the disabled skip.

  It ends with `print("perf ok")`.
- Keep the dry run green:
  - add a `perf PASS` action to `tools/stub-claude` that writes only a verdict file;
  - insert `perf PASS` into `tools/dryrun-plan.txt` directly after `worker architect`.

  Until Phase 3 there is no results audit, so this passes.

Deliverables: `marestail.pipeline.names()` includes `perf`, `roles/perf.md` exists, and `tools/test-perf.py` exists.
Verify: `python3 tools/test-perf.py && python3 tools/test-agent-backends.py && tools/dryrun.sh`

### Phase 2: Commits, worktrees, trees.json
- Record `start-commit`, with the merge-base fallback, and archive it on completion (decision 4).
- Implement pre-marestail commit detection (decision 4).
- Create the worktrees, run `[perf] setup`, write `trees.json` (with `image` null until Phase 4), truncate `samples.jsonl`, and clean up in a `finally` (decision 5).
- Add a `# Trees` section to `judge_prompt` when the judge is perf. It lists:
  - each tree's name, sha and path;
  - `threshold_percent` and `min_runs`;
  - the existing column keys from `PERFORMANCE.md`.
- Change `tools/dryrun.sh` so the throwaway repo gets a first commit containing only a `README.md`, and `marestail.toml` is added in a second commit. The dry run then has a pre-marestail commit.
- Tests in `tools/test-perf.py`, each against a temporary git repo:
  - `start-commit` is written once and not overwritten by a second call;
  - the fallback is used when the file is missing;
  - the pre-marestail commit is the parent of the commit adding `marestail.toml`;
  - no pre-marestail commit when `marestail.toml` was added in the root commit;
  - no pre-marestail commit when `PERFORMANCE.md` already has a data row;
  - during a stub invoke, the worktrees exist at the right shas and `trees.json` lists them; afterwards both are gone, including when the invoke raises.

Deliverables: commits are resolved, and the tree lifecycle is implemented.
Verify: `python3 tools/test-perf.py && python3 tools/test-agent-backends.py && tools/dryrun.sh`

### Phase 3: Sampling, results, audit, table, summary (no database)
- Implement `marestail perf run` without `--db` (decision 8).
- Compile, validate and classify results (decisions 9, 10). Implement the audit with retry-with-feedback and the `judge_prompt` `feedback` argument (decision 11).
- Write the `PERFORMANCE.md` matrix on PASS (decision 13).
- Print the `## Performance changes` summary, and capture it in `tools/overnight.sh` (decision 14).
- Extend the `tools/stub-claude` `perf` action to take `perf <PASS|BOUNCE>`. It:
  - writes an executable `perf/bench_t.py` that prints `{"target": "add_one", "unit": "ms", "better": "lower", "value": V}`, where V is 8 for `pre-marestail`, 10 for `baseline` and 20 for `head` (read from `MARESTAIL_PERF_TREE`);
  - runs `marestail perf run perf/bench_t.py --tree <tree> --samples 10` for every tree in `.marestail/perf/trees.json`;
  - writes a verdict naming `add_one`, with a numbered finding for BOUNCE.
- In `tools/dryrun-plan.txt`, replace the single `perf PASS` line with the three lines `perf BOUNCE`, `code good`, `perf PASS`.
- Tests in `tools/test-perf.py`:
  - `perf run` exits 2 with no `trees.json`, and exits 1 on a failing, silent, or timed-out script;
  - log records have the fields from decision 8;
  - p50/p95 on a known list;
  - each decision-9 validation error;
  - classification for `lower`/`higher`, the threshold boundary (exactly 10% counts as changed), null baseline (`new`), null head (`removed`) and zero baseline;
  - the unflagged-target and missing-existing-column audits;
  - table writes:
    - a first write with a pre-marestail commit produces the header plus `pre-marestail` and task rows;
    - a second task with a new target appends its columns on the right and puts `—` in earlier rows;
    - re-running a task replaces its row in place;
    - a first write without a pre-marestail commit has no `pre-marestail` row;
    - each status suffix renders exactly as in decision 13;
    - the `Rows` cell is `—` when no `--db` sample was recorded, and the effective row count otherwise;
    - a pipe is escaped;
    - the prose above the table is kept byte-for-byte.

Deliverables: `trees`, `samples`, `results` and `table` in `marestail/perf/` and `marestail perf run` work; the dry run exercises a perf bounce and pass through the real wrapper.
Verify: `python3 tools/test-perf.py && python3 tools/test-agent-backends.py && tools/dryrun.sh && grep -q '^| pre-marestail |' /tmp/marestail-dryrun/repo/PERFORMANCE.md && grep -q '^| t |.*20ms (+100.0%) ⚠' /tmp/marestail-dryrun/repo/PERFORMANCE.md && git -C /tmp/marestail-dryrun/repo ls-files --error-unmatch perf/bench_t.py`

### Phase 4: The performance database
- Implement Postgres image detection (decision 15) in `marestail/perf/image.py`, recorded in `trees.json` and the prompt.
- Implement decisions 15–17: password file, helper container, the reflink check, golden hash and build (detached and `--wait`), `status`, `url`, `prune`, `down`, the runner pre-build, the `--db` reset per sample, and removing tree containers and work directories in the Phase 2 `finally`.
- Detection tests in `tools/test-perf.py` (no Docker), each in a temporary directory:
  - explicit `[perf.db] image` wins over a compose file;
  - `docker-compose.yml` with `image: postgres:16` gives `postgres:16`;
  - a workflow with `image: postgres:15-alpine` and no compose file gives `postgres:15-alpine`;
  - `.tool-versions` with `postgres 14.9` gives `postgres:14`;
  - nothing gives `postgres:18`;
  - the helper image for `postgres:16-alpine` is `postgres:16`.
- Row count and disk tests in `tools/test-perf.py` (no Docker):
  - the effective rows is 50000000 with nothing set, the `marestail.toml` value when set, and `MARESTAIL_PERF_DB_ROWS` when both are set;
  - `-1`, `1.5` and `"ten"` each fail with the decision-15 message;
  - the golden name differs between rows 10000000 and 50000000;
  - with rows 0, changing the seed bytes does not change the golden name;
  - the disk estimator with a prior 12500000000-byte golden at 50000000 rows estimates 3000000000 bytes for 10000000 rows;
  - with no prior golden it uses `min_free_gb`;
  - it refuses when free bytes are below the estimate, and never refuses at rows 0.
- Create `tools/test-perf-db.py` (requires Docker). It sets `MARESTAIL_PERF_DB_PREFIX=marestail-perf-test-`, `MARESTAIL_PERF_DB_VOLUME=marestail-perf-test-pgdata` and `MARESTAIL_PERF_DB_PORT=55532`. It builds a temporary git repo with:
  - a `docker-compose.yml` declaring `image: postgres:16`;
  - `[perf.db] rows = 1000`;
  - a `migrate` command creating tables `a` and `b` through `docker exec -i "$MARESTAIL_PERF_DB_CONTAINER" psql -U postgres -d bench`.

  It must not need `psql` on the host. It checks:
  - the resolved image is `postgres:16`, and `SHOW server_version` in a reset tree database starts with `16`;
  - a golden build with a `perf/seed.sql` using `generate_series` reaches `ready`;
  - a seed that leaves table `b` short fails the build with a message naming `b`, and leaves no `.tmp` directory or build container behind;
  - a bench that inserts one row into `a` and then prints `count(*)` of `a` as its value reports `1000` in both of two consecutive `--db` samples;
  - `reset_ms` is logged for `--db` samples;
  - changing the seed bytes changes the golden name;
  - `prune` deletes a golden not in `trees.json`;
  - `perf run --db` without `[perf.db] migrate` exits 2;
  - with `MARESTAIL_PERF_DB_ROWS=0` and no seed script, a golden builds, and a `--db` bench printing `count(*)` of `a` reports `0`;
  - with rows > 0 and no seed script, the build fails naming `perf/seed`;
  - a ready golden has a `META.json` with the rows it was built with.

  At the end it removes every `marestail-perf-test-` container and the test volume, also on failure, and prints `perf db ok`.

Deliverables: `marestail perf db …` works, the Postgres version matches the repo, and every `--db` sample starts from the golden.
Verify: `python3 tools/test-perf-db.py && python3 tools/test-perf.py && tools/dryrun.sh && ! docker ps -a --format '{{.Names}}' | grep -q '^marestail-perf-test-' && ! docker volume ls --format '{{.Name}}' | grep -q '^marestail-perf-test-'`

### Phase 5: Scale check at 50 million rows
- Create `tools/perf-db-scale.py`, using the test prefix, volume and port and the image `postgres:16`. It:
  - builds a golden for one table `(id bigint primary key, amount numeric, created_at timestamptz, note text)` with `rows = 50000000`, seeded by `generate_series`;
  - runs 10 full resets of the `head` tree from it (decision 17);
  - prints one line: `scale: image=postgres:16 rows=50000000 seed_s=<n> golden_bytes=<du -sb of the golden dir> reset_ms_median=<n> reset_ms_max=<n>`;
  - removes its containers and volume.
- Run it, and paste that line into PROGRESS-marestail-perf-tsar.md.
- If `reset_ms_median` is above 5000:
  - also write `RESET TOO SLOW` with your diagnosis (e.g. whether the copy reflinked, and how long Postgres takes to start);
  - stop, and do not start Phase 6 until a human replies.

Deliverables: measured seed and reset cost at the requested scale.
Verify: `grep -q '^scale: image=postgres:16 rows=50000000 ' PROGRESS-marestail-perf-tsar.md && ! grep -q 'RESET TOO SLOW' PROGRESS-marestail-perf-tsar.md`

### Phase 6: Install and gate exclusions
- Create `templates/PERFORMANCE.md`, add the `copy_if_missing` call to `install()`, and extend `GITIGNORE_GENERATED_LINES` (decision 18).
- Exclude the root `perf/` from every gate (decision 19).
- Tests in `tools/test-perf.py`:
  - `install()` into a temporary dir creates `PERFORMANCE.md` and does not overwrite an existing one;
  - with `gitignore_generated=True`, `.gitignore` contains `PERFORMANCE.md` and `perf/`, and without it contains neither;
  - `perf` is in each `SKIP_DIRS` set you changed, and `perf/**` is in the sonar default exclusions.

  Run `install()` with `GROK_HOME` pointed at a temporary dir so the real `~/.grok` is untouched.

Deliverables: install creates the table, and the gates ignore `perf/`.
Verify: `python3 tools/test-perf.py && python3 tools/test-agent-backends.py && tools/dryrun.sh`

### Phase 7: Documentation
- `README.md`:
  - add a `perf` row to the Pipeline table (kind judge, gate none) between architect and hardener;
  - add a `## Performance` section explaining:
    - the trees measured (baseline, head, once pre-marestail);
    - `perf/bench_*` and `perf/seed*`;
    - `marestail perf run` and why samples only go through it;
    - the one-row-per-task `PERFORMANCE.md` matrix and its cell suffixes;
    - the bounce vs flag rule;
    - the performance database: local Docker Postgres matching the repo's version and how that version is detected, the editable row count (default 50M per table, `0` for an empty database, `MARESTAIL_PERF_DB_ROWS` for one-off runs), golden data directories cached by schema hash and row count, a reflink copy and fresh Postgres before every sample, the btrfs/XFS requirement, the measured cost (about 150 s to seed and 12.5 GB per 50M-row table, and about 2 s per reset), the pre-build disk check, and `perf db prune`/`down`;
    - the `[perf]` and `[perf.db]` keys;
    - that it is on by default;
  - add `PERFORMANCE.md` and `perf/` to the `--gitignore-generated` sentences.
- Add commented-out `[perf]` and `[perf.db]` blocks to `templates/marestail.toml`, in the style of its `# [agent]` block, listing every key with its default.
- Update the pipeline order sentence in `templates/tasks-README.md`.
- Add one sentence to `roles/hardener.md` saying that `perf/**` and `PERFORMANCE.md` belong to the perf role and are not findings against the coder.

Deliverables: the docs describe the shipped behaviour.
Verify: `grep -q '| perf |' README.md && grep -q 'perf.db' templates/marestail.toml && grep -q perf templates/tasks-README.md && python3 tools/test-perf.py && tools/dryrun.sh`

## Success Criteria (all must be true)
- [ ] `python3 tools/test-perf.py` exits 0 and prints `perf ok`.
- [ ] `python3 tools/test-perf-db.py` exits 0 and prints `perf db ok`, and afterwards no container or volume whose name starts with `marestail-perf-test-` exists.
- [ ] `python3 tools/test-agent-backends.py` exits 0 and prints `agent backends ok`.
- [ ] `tools/dryrun.sh` exits 0 and prints `remaining plan lines: 0`.
- [ ] After the dry run, the table in `/tmp/marestail-dryrun/repo/PERFORMANCE.md` has exactly the header `| Task | Commit | Date | Rows | add_one p50 | add_one p95 |` and exactly two data rows:
  - first, `pre-marestail`, with Rows `—` and measurement cells `8ms` and `8ms`;
  - second, `t`, with Rows `—` and measurement cells `20ms (+100.0%) ⚠` and `20ms (+100.0%) ⚠`.
- [ ] The row count is editable: `[perf.db] rows` and `MARESTAIL_PERF_DB_ROWS` accept any integer ≥ 0, with the environment taking precedence, and `0` builds an empty migrated database without a seed script (covered in `tools/test-perf.py` and `tools/test-perf-db.py`).
- [ ] A golden build refuses to start when the disk estimate exceeds free space on the Docker data root, and the message names `marestail perf db prune` (covered in `tools/test-perf.py`).
- [ ] After the dry run, `git -C /tmp/marestail-dryrun/repo log --reverse --format=%s` contains a subject starting with `perf verdict: BOUNCE` on an earlier line than a subject starting with `perf verdict: PASS`, and `perf/bench_t.py` is tracked with mode `100755`.
- [ ] `git -C /tmp/marestail-dryrun/repo worktree list` shows exactly one worktree after the dry run.
- [ ] `marestail.pipeline.names()` equals `["specifier", "critic", "coder", "cleaner", "architect", "perf", "hardener", "qa"]`.
- [ ] With `[perf] enabled = false`, a pipeline run makes no perf commit and prints `perf disabled in marestail.toml; skipping` (covered in `tools/test-perf.py`).
- [ ] A perf verdict that omits a `degraded` or `improved` target, omits an existing table column, or rests on invalid samples is retried with the rejection feedback in the next prompt (covered in `tools/test-perf.py`).
- [ ] Postgres image detection returns the expected image for each of the five sources in decision 15 (covered in `tools/test-perf.py`), and a repo declaring `postgres:16` gets a database whose `server_version` starts with `16` (covered in `tools/test-perf-db.py`).
- [ ] Two consecutive `--db` samples of a bench that inserts a row and counts both report the golden's row count (covered in `tools/test-perf-db.py`).
- [ ] `PROGRESS-marestail-perf-tsar.md` contains a `scale: image=postgres:16 rows=50000000 …` line and no `RESET TOO SLOW`.
- [ ] `marestail install` into an empty temporary dir creates `PERFORMANCE.md`, and with `--gitignore-generated` the `.gitignore` lists `PERFORMANCE.md` and `perf/`.
- [ ] `grep -rnE '^\s*#' marestail/perf/ --include='*.py'` prints nothing, and every import under `marestail/perf/` is stdlib or `marestail.*`.
- [ ] `animus-chat-db-1`, `data_tracker-db-1`, `data_tracker-redis-1` and `marestail-sonarqube` are in the same running state after all tests as before.

## Out of Scope
- Database engines other than Postgres, Postgres-derived images (PostGIS, Timescale) unless named explicitly in `[perf.db] image`, and remote or hosted databases.
- A slow-copy fallback on filesystems without reflinks.
- Measuring each tree on the Postgres version it used at the time; one image per run is deliberate.
- CI integration, and comparing numbers across machines or across runs on different hardware.
- Automatic golden eviction by disk usage (only the manual `perf db prune`).
- Generating seed data automatically from a schema: the perf agent writes the seed.
- Starting or orchestrating the target app's servers from the runner: the agent does that per tree.
- Charts, a TUI panel for the table, and splitting or archiving the table when it grows wide.
- Backfilling pre-marestail values for targets first measured after the first run.
- Bouncing to the specifier, critic or architect; a cap on perf bounces.
- Changing how other judges bounce (bounces that skip cleaner/architect are existing behaviour).
- Migrating target repos that already use marestail.

## Rules for the Implementing Agent
- Never delete, skip, or weaken a test to make it pass; flag suspect tests in PROGRESS-marestail-perf-tsar.md instead.
- Record failed approaches and key decisions in PROGRESS-marestail-perf-tsar.md as you go, including the gate exclusion list (Phase 6) and the scale line (Phase 5).
- Commit after each completed phase.
- Stay inside this worktree. Do not touch sibling worktrees or the main checkout (`/home/max/workspace/marestail` is on `runtime-gate`, and other `marestail-*` worktrees belong to other runs).
- Do not run marestail gates or pipelines inside real target repos (e.g. StripeDonationPortal, animus-chat). Only use `tools/dryrun.sh` and temporary dirs.
- Never stop, remove, or connect to any Docker container or volume whose name does not start with `marestail-perf-`.
