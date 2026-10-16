+++
depends = ["023-one-knob-for-mutation-workers", "024-two-runs-share-a-machine"]
stack = false
+++
# 027 — mutation gates run on a fast remote box, and a run shows what that cost

After this task, a user who has configured a remote box sees `ts.mutation`, `ex.mutation`, `py.mutation` and `java.mutation` run on it instead of on their laptop. The gate result is the same as a local run's. The summary says where the gate ran and for how long. `marestail remote status` shows the box's state and what it has cost today and this month.

This fixes what happened on 2026-09-29. Three beds and a slop-cannon run shared one 24-core laptop, with a load average of 82–110. Stryker ran at `nice 19` with `concurrency: 4`. On virtual-adoptions it took about 2 hours, or aborted in its dry run. The same pass on a 64-core spot VM took 5 minutes. On a 192-core spot VM (`c4d-highcpu-192`, us-west4, $1.67/h), a full Stryker run on bindweed took 149 s. animus-chat's muex took 50 minutes, against 1.5–2.5 hours locally.

The live marestail already does this in `marestail/remote.py`, in commits 48e341b through 8945218. This task brings it into this rewrite behind the gate seam, with tests, rather than as a copy. The failures listed under "What must not happen" all occurred during that rollout.

It lands after 023 and 024. 023's `[run] jobs` is the local mutation-worker knob, and `[remote.workers]` here must sit beside it: jobs sizes a local run, and remote.workers sizes a run on the box. The box is also another place where 024's per-run Sonar key and perf namespace must hold.

## What changes

**`~/.config/marestail/remote.toml`** names the box:

```toml
[remote]
enabled = true
instance = "marestail-mutation-usw4"
project = "otwarte-klatki"
zone = "us-west4-a"
container = "mt"
gates = ["ts.mutation", "ex.mutation", "py.mutation", "java.mutation"]
usd_per_hour = 1.67

[remote.workers]
"ts.mutation" = 150
"ex.mutation" = 64
"py.mutation" = 180
"java.mutation" = 150

[remote.env]
PGPORT = "5432"
```

With the file present and a gate listed in `gates`, that gate:

- **Starts the box** if it is stopped (`gcloud compute instances start`), under a file lock so parallel beds start it once. It waits up to 240 s for `docker exec <container> true` over SSH. The external IP is looked up on every start.
- **Syncs the tree** to the same absolute path on the box with `rsync -a --delete`. The root-anchored excludes are `/.git/` and `/.marestail/`. It also skips `node_modules/`, `.next/`, `_build/`, `cover/`, `target/`, `.stryker-tmp/` and `erl_crash.dump` at any depth. `.venv/` is skipped except for `py.mutation`. For `py.mutation`, uv's Python store is synced when the venv's interpreter lives there. A Node version from `~/.nvm/versions/node/*/bin` on `PATH` is synced so the same `node` resolves. An unanchored `.git/` must not be used: it drops `deps/*/.git`, and mix then rejects git dependencies.
- **Installs Node dependencies on the box:** it runs `npm ci` when `package-lock.json`'s hash differs from `node_modules/.marestail-lock`.
- **Queues:** one job at a time on the box, behind `flock /var/lib/marestail/job.lock`. Each job sizes itself to the whole box, and two at once thrashed at a load of 560 on 64 cores. A queued job keeps a busy marker so the box stays up.
- **Runs the gate's command at box size:** the gate builds its command for a destination (`build(on_remote)`). On the box it uses `[remote.workers]` for Stryker `--concurrency`, muex `--concurrency`, mutmut `--max-children` and pitest `-Dthreads`. Only `PATH`, `HOME`, `LANG`, `TERM=dumb`, `NO_COLOR=1`, `[remote.env]` and the gate's own env are passed.
- **Pulls the gate's outputs back** and parses them exactly as the local gate does: `reports/mutation/mutation.json`, the Stryker incremental file, `mutants/` and `.marestail/pit`.
- **Appends ` (on <instance>, <n>s)`** to the summary and to every failure message.

`MARESTAIL_REMOTE=0`, or `[remote] enabled = false` in a target's `marestail.toml`, runs locally. `MARESTAIL_REMOTE_GATES=ex.mutation` offloads the listed gates for one command.

**Fallbacks:**

- **The box cannot start, or the sync fails:** the gate runs locally with **local** worker counts. It appends ` (remote unavailable: <reason>; ran locally)`.
- **The connection drops after the job started** (ssh exit 255 with no output): the job is retried on the box once. If it drops again, the gate fails with ` (connection to <instance> dropped twice mid-run; not rerun locally)`. It must never move a half-run mutation job to the laptop.

**A job dies with its gate.** Each job runs under `ssh -tt`, in its own session in the container (`setsid -w`). The box-side shell traps hangup and kills that session, whether the job is queued or running. Each job opens its own ssh connection, with no shared ControlMaster.

**The box stops itself.** A systemd timer on the box powers it off after 5 minutes with no live busy marker and no rsync, checked every minute (10 minutes cost about $2–3 a day more; 1 minute stopped the box between most jobs and added 1–2 minutes to each gate plus a spot-capacity risk on every start). The instance has `--max-run-duration=8h`. Neither depends on the laptop.

**Cost is recorded.** Every box start, and every remote job whether it succeeds, fails or is killed, appends a line to `~/.config/marestail/remote-usage.jsonl` (`ts`, `event`, `gate`, `repo`, `seconds`, `usd`). `marestail remote status` prints:
- the box's state
- each job running or queued on it right now, as `running: <tool> for <repo>, <age>`, with `(queued)` for one waiting on the lock. The usage log only gains a line when a job ends, so without this a stuck or orphaned job is invisible. With no live job it prints `no jobs: idle for <n> min, powers itself off after <limit> idle min (about HH:MM)`; a bare `RUNNING` read as work in progress.
- billed uptime today and this month, from the Cloud Monitoring metric `compute.googleapis.com/instance/uptime`, summed over every instance the usage log has named
- job count and busy hours

## What must not happen

These are the rollout failures on 2026-09-29, each pinned by a test:

- **Nothing may be orphaned on the box.** A gate killed on the laptop (a bed restart, a timeout) left its `docker exec` running without the lock, and load reached 400–800.
- **A fallback may not keep box sizing.** A dropped connection sent muex back to the laptop at `--concurrency 64`, and the OOM killer took it at 15 GB.
- **A test client may not take other jobs down.** One killed test client took the shared SSH master, and every job on it, down.

## What must not change

- With no `remote.toml`, every gate's command, cwd, env, timeout, summary and findings are today's.
- Findings and pass/fail from a remote run are identical to a local run of the same tree.
- Gates not listed in `gates` never touch the network.

## Tests

`tools/test-remote-mutation.py` stubs `gcloud`, `ssh` and `rsync` with scripts on `PATH` that log their argv:

- **A listed gate with a stopped box:** it calls `start` once, syncs with the root-anchored excludes, runs with `--concurrency 150`, pulls the report, and its summary ends ` (on marestail-mutation-usw4, <n>s)`.
- **Two gates starting together:** `start` is called once.
- **`start` fails:** the command runs locally with the local concurrency, and the summary says why.
- **ssh exits 255 with no output once:** the job is retried on the box. Twice: the gate fails without running locally.
- **An unlisted gate, or `MARESTAIL_REMOTE=0`:** no `gcloud`, `ssh` or `rsync` call is made.
- **The usage log:** each job appends one `job` line with `usd = seconds / 3600 * usd_per_hour`, including a failed job.
- **`marestail remote status`,** given a canned `describe`, usage log and Monitoring response, prints the state and the day and month totals.
- **The host-side script:** it traps HUP and kills the container session. Assert this on the generated script text.

## Done when

- The new test passes, and the other `tools/test-*.py` still pass.
- A real `marestail gate --tier full --only ts.mutation` on virtual-adoptions runs on the box and matches a local run's survivors.
- README gains a "Remote gates" section covering the config, the fallbacks, the queue, the self-stop and the cost log.
