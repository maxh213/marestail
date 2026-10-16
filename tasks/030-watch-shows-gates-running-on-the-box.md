+++
depends = ["027-mutation-gates-run-on-a-remote-box"]
stack = true
+++
# 030 — `marestail watch` shows which gates are running on the remote box

After this task, a bed whose gate is running on the remote box from 027 shows it at a glance. The bed reads `in gate: ☁ stryker on marestail-mutation-usw4 3m`. The header starts with `☁ 1 on vm ·`. A gate that fell back to the laptop shows its plain local label, because that is where it is running.

Without this, an offloaded gate's local process is `ssh`, `rsync` or `gcloud`. The TUI recognised none of them, so a bed waiting on the box showed no gate at all. The live marestail added this in commit cd3eea6. It depends on 027's process shapes.

## What changes

`classify_gate` (`marestail/tui/collect.py`) labels:
- **`gcloud compute instances start <name> …`** as `☁ starting <name>`.
- **An `rsync`** whose tokens contain `HostKeyAlias=<host>` (inside its `-e` string) as `☁ syncing to <host>`.
- **An `ssh` with `HostKeyAlias=<host>`** whose command mentions `busy.d` as `☁ <tool> on <host>`. The tool is the first of `muex`, `stryker`, `mutmut` or `pitest` in the command, or `gate` if none match.
- **The remote side of rsync** (`ssh … rsync --server`) as nothing.

The header counts beds whose gate activity starts with `☁`, and shows `☁ <n> on vm · ` before the bed count when n > 0.

## What must not change

- Local gate labels (`stryker`, `muex`, `mix test`, …) and every other TUI line.

## Tests

`tools/test-watch.py` gains cases for:
- a remote Stryker job and a remote muex job
- an rsync upload and the rsync server side
- a `gcloud start`
- a local `npx stryker run`, which stays `stryker`

## Done when

`tools/test-watch.py` passes, and README's `watch` section mentions the ☁ marker.
