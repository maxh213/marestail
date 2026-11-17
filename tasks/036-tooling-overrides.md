+++
depends = []
+++
# 036 — `[tooling]` sets the versions marestail installs into `.marestail/tooling`

After this task, a repo can set the versions of marestail's own TypeScript tooling in `marestail.toml`, and `ts.tests` works with whichever Vitest the tooling then has. eu-parasol sets `vitest = "5.0.5"` and `"@vitest/coverage-v8" = "5.0.5"`, so the gate runs its tests on the Vitest the repo itself uses.

`marestail/_tooling.py` pins `vitest` and `@vitest/coverage-v8` to `^3.2.0`. eu-parasol uses Vitest 5.0.5 with Vite 8.3.5, and `packages/web/src/assets-routes.ts` calls `import.meta.resolve(...)` at module scope. Under the tooling's Vitest 3.2.7 and Vite 7, `ts.tests` fails 23 test files with `TypeError: __vite_ssr_import_meta__.resolve is not a function`, while the repo's own `vitest run` and Cloud Build pass. So every eu-parasol run failed `ts.tests`, and coder agents rewrote `assets-routes.ts`, or patched `.marestail/tooling`, to get past it.

## What changes

- **`[tooling]`.** An optional table in `marestail.toml`, one `package = "version"` line each. `install --scope hyper` merges it over the defaults when it writes `.marestail/tooling/package.json`. A listed package takes the given version, and a package the defaults lack is added.
- **A bad value.** A value that is not a non-empty string stops install before the tooling is written, with `marestail.toml [tooling] vitest must be a non-empty version string such as "5.0.5", got 5`. A `tooling` key that is not a table stops it with `marestail.toml [tooling] must be a table of package = "version" lines, got '5.0.5'`.
- **`ts.tests` follows the installed Vitest.** It reads `version` from `node_modules/vitest/package.json` in the tooling folder. Without `[ts] tooling`, it looks in `[ts] root` and each folder above it up to the repo root, nearest first. Below 4, or when it finds none, it passes `--coverage.all=true` as today. Vitest 4 removed that option, so from 4 on it passes `--coverage.include=<source>/**/*.{ts,tsx,mts,cts,js,jsx,mjs,cjs}` for each `[ts]` source instead, and files no test loads still appear in the report.
- **Mutation.** No code changes. Stryker 10's Vitest runner loads the repo's own Vitest when the repo has one, and the tooling's otherwise. With the override, `ts.tests` and `ts.mutation` both run 5.0.5 on eu-parasol. The remote box installs the tooling with `npm ci` from the synced `package.json` and `package-lock.json`. The local install rewrites the lock, so the box installs the override too.

## What must not change

- With no `[tooling]` table, `package.json` is byte for byte what it is today.
- For Vitest 3, `ts.tests` passes `--coverage.all=true` in the same place. Jest and Playwright runs do not change.
- The remote box's sync and prepare steps. `marestail/remote.py` and `marestail/gates/_stryker.py` are not part of this task.

## Tests

- `package_json` with `vitest`, `@vitest/coverage-v8` and `left-pad` overridden: the two versions replaced, `left-pad` added, the rest unchanged and sorted.
- `overrides` turns `None` and `{}` into `{}`. It refuses `5`, `""`, `"  "`, a list, `True` and a bare string, with the messages above.
- `install --scope hyper` with eu-parasol's table writes both versions. With `vitest = 5` it exits with the message and runs no npm. `tools/test-install-hyper.py` checks both through the CLI.
- `vitest_command` with a 5.0.5 tooling Vitest and sources `packages` and `apps`: two `--coverage.include` flags and no `--coverage.all`.
- Versions 3.2.7, 4.0.0, 4.0.0-beta.3, 10.1.0, empty, `next` and missing each pick the right flags. The tooling's Vitest wins over the repo's. Without tooling, the nearest Vitest under the repo root wins, and one above the repo root is ignored.

## Done when

The tests pass, and README describes `[tooling]` where it describes the tooling folder. On a fresh eu-parasol worktree with the table, `install --scope hyper` and `gate --only ts.tests` pass with no `import.meta.resolve` error.
