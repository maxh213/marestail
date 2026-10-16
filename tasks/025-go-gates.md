+++
depends = ["017-architect-reaches-for-patterns-a-design-judge-checks", "023-one-knob-for-mutation-workers"]
stack = false
+++
# 025 — Go gates: `marestail gate` measures a Go module the way it measures Rust and Java

After this task, a repo with a `[go]` section in `marestail.toml` gets the same gauntlet as the other eight languages: `go.tests` (every statement run by a test in its own package), `go.crap`, `go.lint` (golangci-lint with marestail's pinned config, plus gofmt), `go.deps` (`.go-layers.json`), `go.mutation` (gremlins), and Go branches in `comments`, `depth`, `deadcode`, `docs`, `marestail graph` and the Sonar gate. `tools/samples/go` goes red for every gate for the right reason and green when fixed. A repo without `[go]` sees no change at all.

Today green has no Go support: `grep -rn '"go"\|golang' marestail/` finds nothing, the registry stops at Java (`marestail/gates/__init__.py:25`, `:68-71`, `:83`), and there is no `tools/samples/go`. A planned terminal UI in Go, and any other Go project a scheduler hands to marestail, cannot be gated.

Follow `.claude/skills/add-language/SKILL.md` throughout. Rust is the model for the per-repo scanner (`marestail/rust.py:130-201`, `marestail/rs/scan/`); Java, the most recent language, is the model for mutation scope (`marestail/gates/java_mutation.py:21-30`), the layers contract (`marestail/gates/java_deps.py:19-25`), the bundled lint ruleset (`marestail/jvm/pmd-ruleset.xml`, `marestail/java.py:17`) and fail-closed depth analysis (`marestail/depth.py:374-384`).

The work is split into eleven parts. Do them in order; each part names every file it creates or touches.

## Facts checked on this machine (2026-09-28)

- `go` is `/usr/bin/go`, `go env GOVERSION` prints `go1.27.0-X:nodwarf5` (note the suffix), `GOROOT=/usr/lib/go`, `GOBIN=/home/max/go/bin`, and `gofmt` is `/usr/lib/go/bin/gofmt`.
- golangci-lint is `/home/max/go/bin/golangci-lint`; `go version -m` on it prints `mod	github.com/golangci/golangci-lint/v2	v2.13.2`. `~/go/bin` is not on the queue drivers' `PATH` (`.marestail/phases/run-queue-all.sh:7`), so the gates must find tools there themselves.
- gremlins is not installed. Its latest release is v0.6.0 (6 December 2025); its `go.mod` says `go 1.25`. `go install github.com/go-gremlins/gremlins/cmd/gremlins@v0.6.0` builds it with go 1.27, and `go version -m` then prints `mod	github.com/go-gremlins/gremlins	v0.6.0` (its own `--version` prints `dev`, so only `go version -m` tells the version).
- On a scratch module: `go test -json -count=1 -covermode=set -coverprofile=F ./...` writes `mode: set` then one line per block, `<import path>/<file>.go:<startLine>.<startCol>,<endLine>.<endCol> <statements> <count>`. File names are import paths, not file paths. A package with no test files still appears, every block at 0. With `-coverpkg=./...` every block appears once per test binary, so the same block has several lines.
- golangci-lint v2.13.2 takes a machine-wide lock at `$TMPDIR/golangci-lint.lock` unless `--allow-parallel-runners` is passed, and a second concurrent run gives up after 5 seconds (`pkg/commands/run.go:485-508` in the v2.13.2 module source). With `--path-mode abs` and `--output.json.path FILE` it writes `{"Issues": [{"FromLinter", "Text", "Pos": {"Filename", "Line", "Column"}}, ...]}` with absolute file names. Without `--path-mode abs`, file names are relative to the config file's folder. Compile errors arrive as issues from the `typecheck` linter.
- gremlins v0.6.0 `unleash [path]` with `-o FILE` writes `{"go_module", "files": [{"file_name", "mutations": [{"type", "status", "line", "column"}]}], "mutants_total", ...}`. `file_name` is relative to the path given (use `.`, the module root). It exits 0 whatever survives. When it finds no mutants it prints `No results to report.` and writes no file. `-E REGEX` excludes files by that relative path. It never mutates `_test.go` files. `--config /dev/null` works and stops it reading `./.gremlins.yaml`, `/etc/gremlins/`, `$XDG_CONFIG_HOME/gremlins/` and `~/.gremlins.yaml`. Its work copies are `os.MkdirTemp(os.TempDir(), "gremlins-")` (`cmd/unleash.go:116`) and `os.MkdirTemp(workDir, "wd-*")` (`internal/engine/workdir/workdir.go:77`), so concurrent runs never share a folder.
- `go mod edit -json` prints go.mod as JSON (`{"Module": {"Path": ...}, "Go": "1.25"}`) without touching the network. `go env -json GOVERSION GOROOT GOBIN GOPATH` prints one JSON object.
- `marestail.shell.run` returns stdout and stderr joined (`marestail/shell.py:38`). Anything the gates parse as JSON must therefore come from a file, or be read line by line keeping only lines that start with `{`, as `rs.lint` does (`marestail/gates/rs_lint.py:58-65`). This is why `go list -json` is not used: its download progress goes to stderr and would land in the middle of the JSON, and it has no import line numbers anyway. `go mod edit -json` supplies the module path, and the scanner supplies import lines.
- The sample below was built in a scratch folder and checked with the real tools: `go test`, `gofmt -l`, golangci-lint with the config in part 5, and gremlins with the flags in part 7 give exactly the red findings listed in part 11, and none in the fixed version.

## Before this task runs (human)

Green's own Sonar scan covers `sonar.sources=marestail` and excludes each scanner folder (`sonar-project.properties:5` ends `marestail/rb/**,marestail/rs/**`). SonarQube's Go analyser would otherwise analyse `marestail/go/scan/main.go`, which has no coverage import, and the `sonar` tier the cleaner, architect and hardener run would fail on it. `sonar-project.properties` is frozen for every role (`marestail/freeze.py:8`), so a human appends `,marestail/go/**` to that line before this task is queued. If it is missing when the run starts, the coder makes the one-line edit, the runner records it as a proposal, and nobody works around it any other way.

## Part 1 — `marestail/golang.py` and the `[go]` section

Create `marestail/golang.py`, the Go adapter, shaped like `marestail/rust.py`. It is named `golang` because `marestail/go/` is the scanner folder, and a module `marestail/go.py` next to a folder `marestail/go/` is ambiguous to readers and tools.

Touch `marestail/context.py`: add `go(self, key, default=None)` and `go_root(self)` after `java_root` (`context.py:125-129`), the same shape as `rust`/`rust_root` (`context.py:119-123`).

`[go]` keys, with defaults living in `golang.py` and the gate modules:

| key | default | meaning |
|---|---|---|
| `root` | `"."` | the folder that holds `go.mod` |
| `go` | `"go"` | the go command, as `[rust] cargo` |
| `crap_max` | `4` | CRAP limit |
| `build_tags` | `[]` | passed as `-tags a,b` to go test, `--build-tags a,b` to golangci-lint, `--tags a,b` to gremlins |
| `test_args` | `[]` | extra `go test` arguments |
| `coverage_exclude` | `[]` | repo-relative globs dropped from coverage; also skipped by `go.crap`, and by `go.mutation` unless `mutation_exclude` is set (Java's rule, `templates/marestail.toml:87`) |
| `mutation_exclude` | unset | repo-relative globs `go.mutation` never mutates |
| `mutation_scope` | `"changed"` | read by `ctx.mutation_files` (`context.py:229-237`) |
| `mutation_timeout` | `7200` | seconds |
| `golangci_config` | unset | repo-relative path to a golangci-lint v2 config; unset uses `marestail/go/golangci.yml` |
| `golangci_lint`, `gremlins` | unset | explicit paths to the two tools |
| `deadcode_ignore` | `[]` | exported names `deadcode` never reports |

What `golang.py` provides (names are a suggestion; keep one public surface per concern, as `rust.py` does):

- `MINIMUM = (1, 25)`. Go 1.25 is the floor because the pinned gremlins declares `go 1.25` and the scanner's `go.mod` does the same.
- `PINS`: `golangci-lint` → module `github.com/golangci/golangci-lint/v2` at `v2.13.2`; `gremlins` → `github.com/go-gremlins/gremlins` at `v0.6.0`.
- `INSTALL` messages, used word for word in findings:
  - go: `install Go 1.25 or newer from https://go.dev/dl or your package manager, or set [go] go`
  - golangci-lint: `go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@v2.13.2`
  - gremlins: `go install github.com/go-gremlins/gremlins/cmd/gremlins@v0.6.0`
- `toolchain(ctx)`: runs `<go> env -json GOVERSION GOROOT GOBIN GOPATH` in the go root (timeout 60) and returns the parsed object or a problem string:
  - exit 127: `go is not installed: <INSTALL go>`
  - non-zero exit or output that is not one JSON object: `go env failed: <last 300 characters>`
  - `GOVERSION` not matching `^go(\d+)\.(\d+)`, or below 1.25: `go 1.25 or newer is required, found <GOVERSION>: <INSTALL go>`
- `module(ctx)`: `<go root>/go.mod` missing gives `<rel root>/go.mod:1 missing; [go] root must name the folder that holds go.mod` (for root `.` that is `go.mod:1 missing; ...`). Otherwise it runs `<go> mod edit -json` and returns `Module.Path`, or `go mod edit -json failed: <tail>`.
- `preflight(ctx) -> str | None`: the first problem from `module` then `toolchain`. Every Go gate calls it after its own skip checks and before running anything, and fails with summary `go toolchain not ready` and the problem as its only finding.
- `tool(ctx, name)`: the configured path, else `shutil.which(name)`, else `<GOBIN>/<name>`, else `<first GOPATH entry>/bin/<name>`, when that file exists. Missing: `<name> is not installed: <INSTALL name>`. Then it runs `<go> version -m <path>` and reads the tab-separated `mod` line (the format `go version -m` defines, not prose). A different module version, `(devel)`, or no `mod` line gives `<name> at <path> is <found>; the gate is pinned to <pin>: <INSTALL name>`. The pin makes gate output reproducible across machines.
- File selection, all repo paths absolute `Path`s like `rust.sources`:
  - skipped folders: `vendor`, `testdata`, `.marestail`, `.git`, `node_modules`, any folder whose name starts with `.` or `_` (the go tool ignores those), any folder below the go root that holds its own `go.mod` (a separate module), and `perf/` through `under_benchmarks` (`context.py:37`).
  - generated files: a file with a line matching `^// Code generated .* DO NOT EDIT\.$` before its `package` line is skipped everywhere, as the Go convention defines.
  - `sources(ctx)`: `*.go` under the go root, not `_test.go`, not skipped, not generated.
  - `tests(ctx)`: the `_test.go` files under the same rules.
  - `files(ctx)`: both.
- `rel(ctx, path)`: as `rust.rel` (`rust.py:40-47`), relative to the go root when the path is relative.
- `excluded(ctx, relative, key)` for `coverage_exclude` and `mutation_exclude`, with the same glob semantics as `java.excluded` (`java.py:219-243`).
- `in_scope(ctx, paths)`, `listify(value)`: as in `rust.py:34-37` and `:110-113`.
- `tags(ctx, flag)`: `[flag, ",".join(build_tags)]` when `build_tags` is non-empty, else `[]`.
- Scanner build and run: part 2.
- `load_coverage(ctx)`: reads `.marestail/go-coverage.json` or returns `None`.

`.importlinter` (frozen for every role except the architect, `marestail/freeze.py:90-99`): the architect adds `marestail.golang` next to `marestail.rust` in the language-adapter layer (`.importlinter:15`) and in both lists that name `marestail.rust` (`.importlinter:76` and `:90`).

## Part 2 — the scanner, `marestail/go/scan`

Create `marestail/go/scan/go.mod` (`module marestail/goscan`, `go 1.25`, no `require`, so no `go.sum`) and `marestail/go/scan/main.go`, package `main`, standard library only (`go/ast`, `go/parser`, `go/token`, `encoding/json`, `os`, `sort`, `strings`, `regexp` as needed). It has no comments, is gofmt-formatted, and passes `go vet` and the config in part 5; the coder runs those by hand. There is no Go test file for it, as there is none for `marestail/rs/scan` or `marestail/jvm/Scan.java`; the sample in part 11 is its test.

It works on syntax alone. `go/types` is not used: cyclomatic complexity, comments, exports and import lines are all syntactic, and type-checking would need every dependency downloaded and would fail on code that does not compile, which is exactly the code a gate most needs to report on.

Usage: `marestail-go-scan comments|complexity|depth|imports|dead FILE... [--uses FILE...]`. It prints one JSON array on stdout and exits 0. A file that does not parse prints `<file>:<line>: <error>` on stderr and exits 1. An unknown mode prints the usage line on stderr and exits 2. Every record's `file` is the path exactly as passed.

- `comments`: parse with `parser.ParseComments`; one record `{"file", "line", "text"}` per comment in `File.Comments` (every `//` line and every `/* */` block), `text` being the comment's first line trimmed to 80 characters. Not reported: directives (`//go:` with no space, `//line `, `//export `, `//extern `), the comment group directly above `import "C"` (that is C code), and the whole file when it is generated. `//nolint` is a comment and is reported, so a lint suppression cannot survive the comments gate.
- `complexity`: one record `{"file", "name", "line", "end", "complexity"}` per `FuncDecl` with a body. `name` is `F` for a function and `T.M` for a method (receiver type without `*` or type parameters). `line` is the line of the name, `end` the line of the closing brace. `complexity` is 1 plus one for each `IfStmt`, `ForStmt`, `RangeStmt`, `CaseClause` with a non-empty list, `CommClause` with a non-nil `Comm`, and `BinaryExpr` whose operator is `&&` or `||`, counting inside function literals as part of the enclosing function. These are gocyclo's rules, so the scanner and golangci-lint's gocyclo agree.
- `depth`: one record per file, `{"file", "public", "statements", "pass_throughs"}`, the shape `rs/scan` gives (`marestail/rs/scan/main.rs:393`). `public`: exported top-level functions, types, vars and consts, and exported methods as `T.M`. `statements`: the number of `ast.Stmt` nodes other than `*ast.BlockStmt`, plus one per top-level declaration, at least 1. `pass_throughs`: `{"line", "name", "target"}` for each plain function (no receiver) with at least one named parameter whose body is exactly one statement, `return <call>` or `<call>`, where the call's arguments are exactly the parameters in order (a final `xs...` is allowed for a variadic last parameter). `target` is the called expression as written (`describe`, `fmt.Sprint`, `s.inner.Write`). Methods are never pass-throughs: Go has no `implements`, so a forwarding method may exist to satisfy an interface, the same reason Rust skips trait impls.
- `imports`: `{"file", "line", "path"}` per import spec.
- `dead`: definitions come from the files before `--uses`; identifier counts come from every file. A definition is an exported top-level function (not `main`, not `init`), type, var or const in a file whose package is not `main` (golangci-lint's `unused` already covers those). It is reported as `{"file", "line", "kind", "name"}` (`kind` one of `function`, `type`, `var`, `const`) when its name occurs as an `*ast.Ident` no more often across all files than it is defined. Methods are never reported, because an exported method may satisfy an interface that no source names (`String`, `Error`, `ServeHTTP`). It is a name match, like Rust's (`README.md:27`).

In `golang.py`:

- `SCAN_DIR = PACKAGE / "go" / "scan"`, `SCAN_INPUTS = ("main.go", "go.mod")`, binary `.marestail/go-scan/marestail-go-scan`, stamp `.marestail/go-scan/stamp` holding the sha256 of the inputs joined, as `rust.scanner_digest` does (`rust.py:139-144`).
- `build_scanner(ctx)`: when the binary exists and the stamp matches, do nothing. Otherwise run `<go> build -trimpath -o <binary> .` with `cwd=SCAN_DIR`, `env={"GOTOOLCHAIN": "local", "GOWORK": "off", "GOFLAGS": "", "CGO_ENABLED": "0"}` and timeout 300, then write the stamp. The env pins the build to the installed toolchain and away from the target's workspace and flags; there is nothing to download. Exit 127 gives the go-missing message; any other failure, or no binary afterwards, gives `go scanner build failed: <last 300 characters>`.
- `scan(ctx, mode, paths, uses=None)`: `([], None)` for no paths; else build, then run `[binary, mode, *paths, *(["--uses", *uses] if uses else [])]` with `cwd=ctx.root`, timeout 600, and return `(json.loads(output or "[]"), None)` or `(None, "go scanner failed (<mode>): <last 300 characters>")`. Same signature and return shape as `rust.scan` (`rust.py:193-201`), so `comments.structured` (`gates/comments.py:203`) and `deadcode.structured` (`gates/deadcode.py:182`) take `golang` as they take `rust`.

## Part 3 — `go.tests` (`marestail/gates/go_tests.py`)

1. Delete `.marestail/go-cover.out`, `.marestail/go-coverage.json` and `.marestail/go-sonar-cover.out`, so a failed run never leaves stale coverage for `go.crap` or Sonar to read.
2. `golang.preflight`.
3. Run `<go> test -json -count=1 -covermode=set -coverprofile=<work>/go-cover.out <tags -tags> <test_args> ./...` in the go root, timeout 3600.
   - No `-coverpkg`: a statement counts as covered only when a test in its own package runs it. That matches how gremlins runs each mutant against its own package's tests (part 7), so `go.tests` and `go.mutation` never disagree about what a test reaches. It also means a package with no tests shows every block at 0 and fails.
   - `-count=1`: the gate runs the tests, never replays the test cache.
4. Parse the output line by line, keeping lines that start with `{` and parse as JSON. `passed` is the number of events with `"Action": "pass"` and a `"Test"` key (tests and subtests).
5. Exit 127: go-missing message. Other non-zero exit: summary `tests failed`, findings `tail()` (`marestail/shell.py:45`) of the text made from every event's `Output` field in order plus every line that is not JSON.
6. Missing profile: `go test wrote no coverage profile`, failing.
7. Merge the profile. Skip `mode:`. Parse each line by the regex `^(.+):(\d+)\.(\d+),(\d+)\.(\d+) (\d+) (\d+)$`. Keep entries whose import-path file starts with `<module path>/`, map them to `<go root>/<rest>` and then `golang.rel`, and drop generated files and `coverage_exclude` matches. The key of a block is its five position numbers per file; its count is the maximum over duplicate lines, its statements as given.
8. No files left: `coverage report lists no source files`, failing, with the output tail.
9. Write `.marestail/go-coverage.json`:

```json
{"files": {"internal/domain/domain.go": {"blocks": [[11, 2, 11, 13, 1, 1]], "lines": {"11": 1}}}, "totals": {"percent_covered": 71.4}}
```

   `blocks` are `[startLine, startCol, endLine, endCol, statements, count]`. `lines` maps each block's start line to the lowest count of the blocks that start on it, so an uncovered block sharing a line with a covered one still shows 0. `percent_covered` weights by statements, as Go's own figure does.
10. Write `.marestail/go-sonar-cover.out`: `mode: set`, then every merged block with its repo-relative path. SonarQube accepts absolute paths or paths relative to the project base directory, not Go import paths (Sonar community thread 15324, confirmed by a SonarSource engineer).
11. Findings: for each file in scope (`ctx.in_scope`), in path order, one `<file>:<startLine> not covered` per zero-count block, one per line, kept when any line from the block's start to its end is in `ctx.gated_lines(file)` (`context.py:163-168`; `None` keeps all). This is how the Java, C#, Ruby and TypeScript tests gates respect `changed` and focus (`gates/java_tests.py:202`).
12. Summary: `<passed> passed, statement coverage <p:.1f>%, <n> gaps<" on changed files" when scoped> (need 0)`.

Go measures statements, not branches. An `if` with no `else` whose condition is always true, or the second operand of `&&` that never decides, leaves no uncovered block. `go.mutation` catches those (`CONDITIONALS_NEGATION`, `INVERT_LOGICAL`), which is why both run in a full pipeline. The README says so (part 10).

## Part 4 — `go.crap` (`marestail/gates/go_crap.py`)

Shaped like `marestail/gates/rs_crap.py`.

- No coverage JSON: `no coverage data; go.tests must run first`, failing.
- Files: `golang.in_scope(ctx, golang.sources(ctx))` minus `coverage_exclude`. None: `Result.skipped(GATE, "no files in scope")`.
- `golang.scan(ctx, "complexity", files)`; an error fails with summary `complexity scanner failed` and the error as the finding.
- Coverage share of a function: statements in blocks whose start line lies in `[line, end]` with count > 0, divided by all statements in those blocks; 0.0 when there are none (a file missing from the profile fails closed, as in Rust). Score with `_crap.crap_score`, report with `_crap.describe` and `_crap.above` (`gates/_crap.py:12-24`). Summary `<n> functions, <k> above CRAP <limit:g>`, limit `[go] crap_max`.
- Hyper: `_hyper_crap.judged` with `Hyper(GATE, limit, base_units)`, exactly as `rs_crap.py:44-45` and `:77-94`; `base_units` scans the base copy with the same `complexity` mode, and `missing` comes from `unhit_lines` over the file's `lines` map.

## Part 5 — `go.lint` (`marestail/gates/go_lint.py`) and `marestail/go/golangci.yml`

Create `marestail/go/golangci.yml`, the pinned default config, with this content (checked with `golangci-lint config verify` and on the sample):

```yaml
version: "2"
run:
  tests: true
linters:
  default: standard
  enable:
    - asciicheck
    - bidichk
    - bodyclose
    - copyloopvar
    - durationcheck
    - errchkjson
    - errorlint
    - forcetypeassert
    - gocritic
    - gocyclo
    - gosec
    - intrange
    - makezero
    - nakedret
    - nilerr
    - nilnil
    - noctx
    - predeclared
    - reassign
    - rowserrcheck
    - sqlclosecheck
    - unconvert
    - unparam
    - usestdlibvars
    - wastedassign
  settings:
    gocyclo:
      min-complexity: 10
    nakedret:
      max-func-lines: 0
    govet:
      enable-all: true
      disable:
        - fieldalignment
issues:
  max-issues-per-linter: 0
  max-same-issues: 0
```

`default: standard` is errcheck, govet, ineffassign, staticcheck and unused. Leave out revive and staticcheck's `ST1000`/`ST1020`-`ST1022` (off by default), because they demand doc comments and the comments gate forbids comments. gocyclo at 10 matches the PMD limit Java uses (`marestail/jvm/pmd-ruleset.xml:12`); CRAP ≤ 4 is the tight bound. `fieldalignment` rewrites struct layout for memory and is noise here.

The gate:

1. When `ctx.scope_changed` and `ctx.changed_under(go_root, (".go", "go.mod", "go.sum"))` is empty: `Result.skipped(GATE, "no changed Go files")`.
2. `golang.preflight`, then `golang.tool(ctx, "golangci-lint")`; a problem fails with summary `golangci-lint missing` and the message.
3. Delete `.marestail/go-lint.json`, then run in the go root, timeout 1800, env `{"GOLANGCI_LINT_CACHE": "<work>/golangci-cache"}`:

```
golangci-lint run --config <cfg> --allow-parallel-runners --path-mode abs --show-stats=false --max-issues-per-linter=0 --max-same-issues=0 --output.json.path <work>/go-lint.json <--build-tags a,b> ./...
```

   `<cfg>` is `[go] golangci_config` resolved against the repo root, else the bundled file. A repo's own `.golangci.yml` is read only when `golangci_config` names it. `--allow-parallel-runners` and the per-worktree cache keep two runs on one machine from colliding on `$TMPDIR/golangci-lint.lock` (see "Other runs on the same machine" below).
4. Exit 0 or 1 with the report present: findings are `<rel file>:<Line> <FromLinter>: <first line of Text>`, deduplicated, kept when `ctx.in_scope(file)`, then `ctx.on_changed_lines`. Any other exit, or no report: fail with `golangci-lint failed (exit <n>)` and the output tail.
5. gofmt: `<GOROOT>/bin/gofmt -l <in-scope Go files, tests included>`, timeout 300. Each listed path gives `<rel>:<ctx.placeholder_line> not gofmt formatted; run gofmt -w <rel>`, through `ctx.on_changed_lines`, as `rs_lint.unformatted` does (`gates/rs_lint.py:122-126`). A non-zero exit fails with `gofmt failed: <tail>`.
6. Summary `golangci-lint and gofmt clean`, or `<n> problems`; findings capped at 60.

## Part 6 — `go.deps` (`marestail/gates/go_deps.py`) and `templates/go-layers.json`

The Go toolchain already refuses the other two dependency rules the other languages check: import cycles between packages (`import cycle not allowed`) and imports of another tree's `internal/` package (`use of internal package ... not allowed`). Both are build errors, so they fail `go.tests` and `go.lint`. `go.deps` checks only the layer contract, and its passing summary says why nothing else is checked.

1. No `.go-layers.json` at the repo root: fail with `no .go-layers.json; copy templates/go-layers.json and name the layers` and finding `.go-layers.json:1 missing`, as Java does (`gates/java_deps.py:22-23`). A Go deps gate with no contract would check nothing, and a missing gate must be visible.
2. `golang.sources(ctx)` empty: `Result.skipped(GATE, "no Go sources")`.
3. `golang.preflight`; `golang.module(ctx)` gives the module path.
4. `golang.scan(ctx, "imports", sources)`. For each import, `to` is the repo-relative folder when the path is the module path or starts with `<module>/`, else the import path itself.
5. Contract, `templates/go-layers.json`:

```json
{
  "layers": [
    {"from": "internal/domain", "forbid": ["internal/store", "internal/web"], "forbid_external": ["net/http", "database/sql", "github.com/jackc/pgx/*"]},
    {"from": "internal/store", "forbid": ["internal/web"], "forbid_external": ["net/http"]}
  ]
}
```

   `from` and `forbid` are repo-relative folders and cover their subfolders (`_cycles.under` semantics, `gates/rs_deps.py:49-51`). `forbid_external` entries are `fnmatch` globs on import paths.
6. Finding per break: `<file>:<line> <from> must not depend on <forbid entry> (<import path>)`. Keep findings whose file is in scope, then `ctx.on_changed_lines`, cap at 60.
7. Summary: `<n> layer breaks`, or `layer contracts kept; the go toolchain rejects import cycles and internal/ imports itself`.

## Part 7 — `go.mutation` (`marestail/gates/go_mutation.py`)

**Tool choice: gremlins v0.6.0.**

- gremlins: a standalone CLI with a documented JSON report per mutant (file, line, column, type, status), a `--workers` flag, file exclusion by regex, and releases through December 2025. It runs on go 1.27 (checked above).
- go-mutesting (avito-tech fork): has a JSON report, but it is a fork of an abandoned tool with no parallelism flag.
- ooze: a library you call from a `//go:build mutation` test, so the target has to add a dependency and a test file, and its report is printed text. Rule 1 of the skill rules it out.

The gate, following `java_mutation.py`:

1. `scope = ctx.mutation_files("go", go_root, (".go",))`. Mode `error` fails with `scope.note`. Targets are `golang.sources(ctx)` that are in `scope.files` (all of them when `files` is `None`), minus `mutation_exclude` (or `coverage_exclude` when `mutation_exclude` is unset). None: `Result.skipped(GATE, "no changed Go sources")` when `files` was given, else `"no Go sources"`.
2. `golang.preflight`, then `golang.tool(ctx, "gremlins")`; a problem fails with summary `gremlins missing`.
3. Delete `.marestail/go-mutation.json`, then run in the go root, timeout `[go] mutation_timeout`:

```
gremlins unleash --config /dev/null --output <work>/go-mutation.json
  --arithmetic-base=true --conditionals-boundary=true --conditionals-negation=true
  --increment-decrement=true --invert-negatives=true --invert-logical=true --invert-loopctrl=true
  --invert-assignments=false --invert-bitwise=false --invert-bwassign=false --remove-self-assignments=false
  <--workers N> <--tags a,b> <-E ^<escaped path>$ ...> .
```

   - Every mutant type is set by flag, and flags beat gremlins' environment variables and config files, so nothing ambient changes what is tested. The set is gremlins' five defaults plus `invert-logical` (`&&`↔`||`) and `invert-loopctrl` (`break`↔`continue`), which are what the other languages' tools mutate for boolean and loop logic.
   - `-E`: one anchored, `re.escape`d regex per Go source under the go root that is not a target, paths relative to the go root. This narrows the run to the targets, as `--file` does for cargo-mutants (`gates/rs_mutation.py:48`).
   - No `--integration` and no `--coverpkg`: each mutant runs against its own package's tests, the same rule as `go.tests`.
   - `--workers`: part "Other runs on the same machine".
4. No report: fail with `gremlins wrote no report (exit <n>); it writes none when it finds no mutants or its test run fails` and the output tail. It prints `No results to report.` in the no-mutant case, but the gate reads files, never prose, so both cases share one failing message. Zero mutants fails in every other language too (`gates/java_mutation.py:18`, `gates/rs_mutation.py:99`).
5. Statuses: `KILLED` and `TIMED OUT` are killed; `NOT VIABLE` and `SKIPPED` are ignored; `LIVED`, `NOT COVERED` and anything else (for example `RUNNABLE`) are not killed. No viable mutant: `no mutants were generated`, failing.
6. Finding per mutant not killed: `<rel file>:<line> <TYPE> at column <column> survived`, or `... not covered by its package's tests` for `NOT COVERED`. `ctx.on_changed_lines` first; nothing left gives a pass with `NO_CHANGED_MUTANTS` (`context.py:13`). Summary `<k> of <n> mutants not killed<note>` or `all <n> mutants killed<note>`, with `scope.note` as in Java. Findings capped at 60.

gremlins' own `--diff` is not used. marestail's scope code decides which files and lines count, as it does for every language.

## Part 8 — the language-neutral gates, `marestail depth` and `marestail graph`

Each branch runs only when `[go]` exists, like the Rust ones.

- `marestail/gates/comments.py`: `GO_SUFFIXES = (".go",)` beside `RUST_SUFFIXES` (`:43`); `go_findings` calls `structured(ctx, golang, <files(ctx, GO_SUFFIXES) minus golang-skipped and generated>, "go comment scanner failed", partial(golang.rel, ctx))`, as `rust_findings` does (`:220-225`). Add it to `SCANNERS` (`:251`) after `java_findings`. Test files are included: every comment is a finding in every language.
- `marestail/gates/deadcode.py`: `go_findings` calls `structured(ctx, golang, "go deadcode scanner failed", partial(golang.rel, ctx), uses=golang.tests(ctx))` (`:182`, `:200-205`) and drops findings whose name is in `[go] deadcode_ignore`. Add it to `SCANNERS` (`:289`). Unexported dead code is golangci-lint's `unused`, in `go.lint`.
- `marestail/depth.py`: `("go", go_modules)` in `scanners()` (`:79-89`). `go_modules` follows `java_modules` (`:374-384`), not `rust_modules` (`:361-371`): a scanner error raises `SystemExit(f"go depth analysis failed: {error}")`. `rust_modules` returning `[]` on error lets a broken scanner pass the depth gate; do not copy that.
- `marestail/graph.py`: `go_graph` in `render` (`:31-45`) after `java_graph`, through `scanned_graph(config, "go", "Go", golang.sources, go_body)` (`:95-99`). Lines are `<file> -> <folder> (<import path>)` for imports inside the module; an error prints as the body, as Rust's does.
- `marestail/gates/docs.py`: only when `[go]` exists, `.go` files other than `_test.go` are documented sources, and they are searched with `GO_ROUTE_PATTERNS` and `GO_ENV_PATTERNS` on top of the configured patterns:
  - routes: `r'\.(?:HandleFunc|Handle)\(\s*"(?:[A-Z]+ )?(/[^"]*)"'` (net/http, including Go 1.22 `"GET /path"` patterns) and `r'\.(?:GET|POST|PUT|DELETE|PATCH|Get|Post|Put|Delete|Patch)\(\s*"(/[^"]*)"'` (gin, echo, chi).
  - env: `r'os\.(?:Getenv|LookupEnv)\(\s*"([A-Z][A-Z0-9_]+)"'`.

  The Go patterns apply to `.go` files only, and without `[go]` the gate reads exactly the files it reads today (`:27`, `:51-57`). A repo that happens to hold `.go` files but no `[go]` section sees no new findings.

## Part 9 — Sonar (`marestail/gates/sonar.py`)

SonarQube Community Build, the `sonarqube:community` image marestail runs (`marestail/sonar/docker-compose.yml:3`), includes the Go analyser. No plugin is needed.

- `go_properties(ctx)` beside `java_properties` (`:170-178`), added to `scanner_properties` (`:148-156`): when `[go]` exists, `-Dsonar.go.coverage.reportPaths=<work>/go-sonar-cover.out`, and `-Dsonar.coverage.exclusions=<coverage_exclude globs joined by commas>` when that list is non-empty, each only when `sonar-project.properties` does not declare the key (`declared_properties`, `:181-182`).
- `scanner_exclusions` (`:185-187`): when `[go]` exists, add `**/*_test.go`, `**/vendor/**` and `**/testdata/**` to the patterns when they are not already there. Go keeps tests beside the code, so without this SonarQube would treat test files as uncovered production code.
- `LANGUAGE_CHECKS` (`:336-369`): add, after Java:

```python
LanguageCheck(
    "go",
    "go=",
    "Go",
    go_where,
    "; put the module in sonar.sources",
    "marestail.toml:1 SonarQube imported no go coverage; run go.tests first so .marestail/go-sonar-cover.out exists",
)
```

  with `go_where(ctx)` returning `golang.rel(ctx, ctx.go_root())`. The gate then fails closed when SonarQube shows no Go lines or no coverage metric.
- Nothing changes for a `[dotnet]` repo, which scans through `dotnet_scan` (`:223-234`).

## Part 10 — registry, freeze, templates, README

- `marestail/gates/__init__.py`: `GO = "go"`; `("go.tests", FAST, GO, "go_tests")`, `("go.crap", FAST, GO, "go_crap")`, `("go.lint", FAST, GO, "go_lint")`, `("go.deps", FAST, GO, "go_deps")` straight after `java.deps` (`:71`); `("go.mutation", FULL, GO, "go_mutation")` straight after `java.mutation` (`:83`). Nothing else moves.
- `marestail/freeze.py`, `GATE_CONFIG` (`:6-83`), after `.java-layers.json` (`:70`): `"**/go.mod"`, `"**/go.sum"`, `"go.work"`, `"go.work.sum"`, `"**/.golangci.yml"`, `"**/.golangci.yaml"`, `"**/.golangci.toml"`, `"**/.golangci.json"`, `".go-layers.json"`. `ALLOWED["architect"]` (`:90-99`): add `".go-layers.json"`. go.mod and go.sum are frozen for the same reason `**/Cargo.toml` and `Cargo.lock` are (`:48-49`): a dependency is a human's decision. `.gremlins.yaml` is not listed, because the gate passes `--config /dev/null` and never reads one. This task creates `marestail/go/scan/go.mod` and `tools/samples/go/go.mod`; the running runner's freeze list has no Go entries, so creating them is expected and is not a frozen edit.
- `templates/marestail.toml`: after `[java]` (`:83-93`):

```toml
[go]
root = "."  # the folder that holds go.mod
crap_max = 4
coverage_exclude = []  # repo-relative globs, e.g. ["cmd/app/main.go"]; also skipped by go.crap, and by go.mutation unless mutation_exclude is set
# build_tags = []  # passed to go test, golangci-lint and gremlins
# test_args = []  # extra go test arguments
# golangci_config = ""  # default: marestail's pinned marestail/go/golangci.yml
# deadcode_ignore = []  # exported names deadcode never reports
# mutation_timeout = 7200
```

- `templates/go-layers.json`: part 6.
- No rulebook work. Phase 017 already ships `templates/guidance/go.md` (practices, `GO-n`) and `templates/guidance/patterns/go.md` (design, `GO-P<n>`), transformed from Max's cheat sheet, plus the `uses_go` install check (a `go.mod` at the root). This task writes no Go rules and leaves `marestail install`'s guidance copying alone. If 017's files are missing when this task starts, stop and say so; don't write them here.
- `.gitignore`: no change. Everything the gates write is under `.marestail/`, and `tools/samples/**/.marestail/` is already ignored.
- `README.md`:
  - The gates table (`:11-23`) gets a `Go` column after `Java`, so the header reads `| Gate | Python | TypeScript | Elixir | Ruby / Rails | C# / .NET | Erlang | Rust | Java | Go |` with a matching separator. Cells, top to bottom: `go test -coverprofile, statement blocks`; `go/ast scanner + coverprofile`; `gremlins`; ``imports vs `.go-layers.json` (the go tool rejects cycles and internal/)``; ``golangci-lint (pinned config), gofmt``; `go/ast comment groups, directives exempt`; `go/ast scanner (pass-throughs); internal/ enforced by the go tool`; `golangci-lint unused, unreferenced exported names`; `regex over sources`; `—`; `local SonarQube, built-in Go analyzer + coverprofile`.
  - A paragraph after the Java paragraph (`:29`), covering in this order: Go 1.25 or newer (`go env GOVERSION` is checked) and what happens when go is missing; `go install ...@v2.13.2` and `go install ...@v0.6.0`, that both versions are pinned and checked with `go version -m`, and that the gates also look in `GOBIN` and `GOPATH/bin`; the scanner (`marestail/go/scan`, standard library only) built once per repo into `.marestail/go-scan` with the installed toolchain and no network, rebuilt when its source changes; that `go.tests` counts a statement as covered only when a test in its own package runs it, that Go has no branch coverage and `go.mutation` covers what that misses; golangci-lint with `marestail/go/golangci.yml` unless `[go] golangci_config` names another, run with `--allow-parallel-runners` and a cache under `.marestail/`, plus `gofmt -l`; `.go-layers.json` (`templates/go-layers.json`) with `forbid_external`, and that cycles and `internal/` are the toolchain's; dead code as `unused` plus exported names found nowhere else, methods never reported; pass-through detection skipping methods; comments including `//nolint`, directives exempt; generated files skipped everywhere; gremlins run per package with every mutant type set by flag and `--config /dev/null`, a timed-out mutant counts as killed and a non-viable one is ignored; Sonar gets `sonar.go.coverage.reportPaths=.marestail/go-sonar-cover.out` (repo-relative paths, which SonarQube needs instead of Go import paths) unless `sonar-project.properties` sets it, and test, vendor and testdata files are excluded.
  - Name no path in backticks that does not exist: the docs gate checks backticked paths under `marestail/`, `tools/` and `templates/` (`marestail.toml:27`).

## Part 11 — `tools/samples/go`

A module that ships red on purpose, like `tools/samples/rust` and `tools/samples/java`. Files, verbatim:

`tools/samples/go/go.mod`

```
module example.com/marestail/sample

go 1.25
```

`tools/samples/go/marestail.toml`

```toml
# Shipped red on purpose, like the rust and java samples: a // comment above domain.Classify,
# Classify at complexity 5 with its big, mid and small arms uncovered and boundary mutants no
# test kills, the untested default arm of domain.Tag, a pass-through domain.Label forwarding to
# describe, domain -> store breaking .go-layers.json, a wasted assignment in store.Size, an
# unformatted composite literal in store.New, an unused exported store.UnusedHelper, and
# SAMPLE_GREETING read but not documented in README.md.

[git]
base = "HEAD"

[go]
root = "."
crap_max = 4

[comments]
paths = ["internal"]

[docs]
files = ["README.md"]
sources = ["internal"]

[sonar]
project_key = "marestail-go-sample"
project_name = "marestail go sample"
```

`tools/samples/go/.go-layers.json`

```json
{
  "layers": [
    {"from": "internal/domain", "forbid": ["internal/store"]}
  ]
}
```

`tools/samples/go/internal/domain/domain.go` (tabs, as gofmt writes them)

```go
package domain

import (
	"fmt"

	"example.com/marestail/sample/internal/store"
)

// Classify names the size of n.
func Classify(n int) string {
	if n >= 10 {
		if n > 20 {
			if n > 30 {
				if n > 40 {
					return "huge"
				}
				return "big"
			}
			return "mid"
		}
		return "small"
	}
	return "tiny"
}

func Tag(n int) string {
	switch n {
	case 0:
		return "zero"
	default:
		return "other"
	}
}

func Label(s store.Store) string {
	return describe(s)
}

func describe(s store.Store) string {
	return fmt.Sprintf("box %d", s.Size())
}
```

`tools/samples/go/internal/domain/domain_test.go`

```go
package domain

import (
	"testing"

	"example.com/marestail/sample/internal/store"
)

func TestClassifyExtremes(t *testing.T) {
	if Classify(5) != "tiny" {
		t.Fatal("5")
	}
	if Classify(50) != "huge" {
		t.Fatal("50")
	}
}

func TestTagZero(t *testing.T) {
	if Tag(0) != "zero" {
		t.Fatal("0")
	}
}

func TestLabel(t *testing.T) {
	if Label(store.New([]int{0, 1})) != "box 2" {
		t.Fatal("label")
	}
}
```

`tools/samples/go/internal/store/store.go` (line 10 is deliberately not gofmt-formatted)

```go
package store

import "os"

type Store struct {
	items []int
}

func New(items []int) Store {
	return Store{items:items}
}

func (s Store) Size() int {
	size := 0
	size = len(s.items)
	return size
}

func Greeting() string {
	return os.Getenv("SAMPLE_GREETING")
}

func UnusedHelper() int {
	return 1
}
```

`tools/samples/go/internal/store/store_test.go`

```go
package store

import "testing"

func TestSize(t *testing.T) {
	if New([]int{1, 2}).Size() != 2 {
		t.Fatal("size")
	}
}

func TestGreeting(t *testing.T) {
	t.Setenv("SAMPLE_GREETING", "hi")
	if Greeting() != "hi" {
		t.Fatal("greeting")
	}
}
```

`tools/samples/go/README.md`: in the voice of `tools/samples/java/README.md`. It explains that the sample proves the Go gates go red for the right reason and green when fixed, the layout, `go test ./...` and `marestail gate --tier full --focus internal` (focus puts every file in scope for `go.mutation`, which otherwise mutates only files changed against `[git] base`), and the fixes below. It must not name `SAMPLE_GREETING`, since the undocumented variable is one of the planted defects.

**Red.** From `tools/samples/go`, `marestail gate --tier full --focus internal` must give these findings (the numbered lines are the files above):

| gate | findings |
|---|---|
| `go.tests` | `internal/domain/domain.go:17 not covered`, `:19`, `:21`, `:31`, `internal/store/store.go:24 not covered`; summary starts `5 passed` |
| `go.crap` | `internal/domain/domain.go:10 Classify crap=5.9 (cc=5, coverage=67%)` |
| `go.lint` | `internal/store/store.go:14 wastedassign: assigned to size, but reassigned without using the value` and `internal/store/store.go:1 not gofmt formatted; run gofmt -w internal/store/store.go` |
| `go.deps` | `internal/domain/domain.go:6 internal/domain must not depend on internal/store (example.com/marestail/sample/internal/store)` |
| `go.mutation` | four `CONDITIONALS_BOUNDARY ... survived` at `internal/domain/domain.go:11`, `:12`, `:13`, `:14` |
| `comments` | `internal/domain/domain.go:9 comment: // Classify names the size of n.` |
| `depth` | `internal/domain/domain.go:35 Label only forwards its arguments to describe` |
| `deadcode` | `internal/store/store.go:23 unused function 'UnusedHelper'` |
| `docs` | `internal/store/store.go:20 environment variable SAMPLE_GREETING is not documented` |
| `sonar` (when `marestail sonar setup` has been run) | `sonar coverage <x>% (need 100)`, and no `imported no go coverage` finding |

**Green.** In a scratch copy (`cp -r tools/samples/go /tmp/...`, never committed), apply these fixes and the same command passes every gate:

- `domain.go`: remove the comment; replace `Classify` with a table walk, which has complexity 3:

```go
type bound struct {
	least int
	name  string
}

var bounds = []bound{{41, "huge"}, {31, "big"}, {21, "mid"}, {10, "small"}}

func Classify(n int) string {
	for _, b := range bounds {
		if n >= b.least {
			return b.name
		}
	}
	return "tiny"
}
```

  delete `Label`, `describe` and the `store` and `fmt` imports.
- `domain_test.go`: test `Classify` at 9, 10, 20, 21, 30, 31, 40 and 41, test `Tag(1)`, drop `TestLabel` and the `store` import.
- `store.go`: `return Store{items: items}`; `Size` returns `len(s.items)`; add `func (s Store) Label() string { return fmt.Sprintf("box %d", s.Size()) }` (gofmt'd over three lines); delete `UnusedHelper`.
- `store_test.go`: add a `TestLabel` asserting `New([]int{0, 1}).Label() == "box 2"`.
- `README.md`: document `SAMPLE_GREETING`.

**Scope, on the sample,** matching the other languages:

- `--scope changed` after editing only `internal/store/store.go`: only `store.go` findings appear; `go.mutation` targets only `store.go` (every other source is passed to gremlins as an `-E` exclusion).
- `--scope hard --focus internal/store/store.go`: the same file, gated as fully changed.
- `--scope hyper` after changing line 15 of `store.go`: `go.lint` keeps the line-15 finding, if any, and drops the gofmt finding as file-level, counted in the summary; `go.crap` judges only `Size`.
- Fail closed: with gremlins off `PATH`, out of `GOBIN` and out of `GOPATH/bin`, `go.mutation` fails with the install line; with `.marestail/go-cover.out` deleted between the test run and the merge, `go.tests` fails. With `go` off `PATH`, every Go gate fails with the go install line.

The coder runs these by hand before handing off, installing gremlins with the pinned command if it is missing, and pastes each gate's result line, red and green, with its seconds, into the handoff. A gate that takes more than a minute on the sample is reported there, since the skill's rule is that such a gate moves to `FULL`.

## Other runs on the same machine (023 and 024)

- `[run] jobs` / `MARESTAIL_JOBS` (023): `go.mutation` passes `--workers <N>` when either is set, read with 023's `jobs.pick(ctx.config, "go", "mutation_workers", None)` from `marestail/jobs.py`, so `MARESTAIL_JOBS` wins, then a `[go] mutation_workers` key if a repo sets one, then `[run] jobs`. When neither is set it passes no `--workers` flag, so gremlins keeps its own default of every CPU. If 023 keeps a list of mutation tools and their worker flags, in code, a test or README, add `go.mutation` → `--workers` to it. Nothing else in the Go gates reads `jobs`, and it never removes or samples a mutant.
- `MARESTAIL_RUN_SUFFIX` (024): the Go gates add no machine-wide name, so 024's inventory needs no Go entry.
  - Everything they write is under the worktree's `.marestail/`: `go-scan/`, `go-cover.out`, `go-coverage.json`, `go-sonar-cover.out`, `go-lint.json`, `golangci-cache/`, `go-mutation.json`.
  - golangci-lint's `$TMPDIR/golangci-lint.lock` is avoided, not suffixed, by `--allow-parallel-runners`, and its cache is per worktree.
  - gremlins' temporary folders get unique names from `os.MkdirTemp`.
  - The Go build cache and module cache are shared, and `go help cache` states the build cache is safe for concurrent invocations.
  - The Sonar project key comes from `[sonar] project_key`, which 024 already suffixes; Go adds no Sonar name.
  - If 024 added a test that lists or greps for machine-wide names, it must still pass with the Go gates present.

## Tests

Pytest, in-process and hermetic, in the style of `tests/test_rust.py` and `tests/test_gates_rs_*.py`: `make_context` and the `fake_run` fixture (`tests/conftest.py:51-98`, `:132`), stubbing `golang.run`, `golang.scan`, `golang.tool` and `golang.preflight` where a test is not about them. Add `"go"`, `"gofmt"`, `"golangci-lint"` and `"gremlins"` to `FORBIDDEN_BINARIES` (`tests/conftest.py:136-138`) so no test can start a real one. Every new branch is covered.

- `tests/test_golang.py`: `toolchain` for exit 127, bad JSON, `go1.24.9`, `go1.25.0`, and `go1.27.0-X:nodwarf5`; `module` for missing go.mod at `.` and under a subfolder, success, and failure; `preflight` order; `tool` for a configured path, a `PATH` hit, the `GOBIN` and `GOPATH/bin` fallbacks, missing, the right pin, a wrong version, `(devel)`, and no `mod` line; file selection for each skipped folder kind, a nested `go.mod`, `_test.go`, a generated header before `package` and the same text after it, and `perf/`; `rel`; `excluded`; `tags`; `build_scanner` fresh, stale, exit 127, failure, and the exact command, cwd and env; `scan` with no paths, a build error, a scanner failure, `--uses`, and success.
- `tests/test_gates_go_tests.py`: the exact command with and without tags and test args; stale files deleted first; pass counting (a subtest counts, `fail` and non-JSON lines do not); exit 127; test failure findings built from `Output`; no profile; a profile with only `mode:`; duplicate blocks merged by maximum; entries outside the module dropped; generated and `coverage_exclude` files dropped; the JSON and Sonar files written byte for byte for a two-file fixture; `lines` taking the lowest count on a shared line; findings under `all`, `changed` (`gated_lines`), focus and hyper; the summary text.
- `tests/test_gates_go_crap.py`: no coverage; nothing in scope; a scanner error; statement-weighted shares including a function with no blocks; the limit from config; the hyper path through `judged`, with `base_units` calling the scanner on the base copy.
- `tests/test_gates_go_lint.py`: the skip; preflight and tool problems; the exact command with the bundled config, with `golangci_config`, and with tags; the cache env; exit 0, 1 and 3; a missing report; typecheck issues; dedupe; scope and hyper filtering; gofmt findings, gofmt failure, and the file-level drop under hyper; the cap.
- `tests/test_gates_go_deps.py`: no contract; no sources; internal and external edges; `forbid`, `forbid_external` globs and subfolder coverage; scope and hyper filtering; both summaries.
- `tests/test_gates_go_mutation.py`: the `mutation_files` modes `error`, `skip` (scoped with nothing changed), `scoped` and `full`, including a missing base note; target filtering by `mutation_exclude` and by the `coverage_exclude` fallback; the exact command (every mutant flag, `--config /dev/null`, `-E` regexes escaped and anchored, tags); `--workers` present when 023's setting is set and absent when unset; a missing report; each status; no viable mutants; findings; hyper with and without mutants on changed lines; the summary with the scope note.
- Extend `tests/test_gates_comments.py`, `test_gates_deadcode.py` (including `deadcode_ignore`), `test_depth.py` (`SystemExit` on error), `test_graph.py`, `test_gates_docs.py` (each Go pattern; no `.go` sources and no Go patterns without `[go]`; `_test.go` skipped), `test_gates_sonar.py` (`go_properties` with and without declared keys, the exclusions, the new `LanguageCheck`), `test_context.py` (`go`, `go_root`) and `test_freeze.py` (each new pattern frozen for the coder; `.go-layers.json` allowed for the architect).
- `tests/test_gates_init.py`: add `("go", "go")` to `LANGUAGES` (`:11-20`) after Java.
- `tests/test_green_repo.py`: add `marestail/go/scan/main.go` to `SCANNER_SOURCES` (`:88-96`) and `marestail/go/scan/go.mod` and `marestail/go/golangci.yml` to `FROZEN_SCANNER_PROJECTS` (`:97-102`). The tier lists for green's own config (`:54`, `:114-125`) do not change, because green's `marestail.toml` has no `[go]`.

## Must not break

- `marestail run` on a task file with no front matter, the existing `pipeline.log`, `timeline.json` and handoff layout, and the queue driver scripts in `.marestail/phases/`. Never edit, move or delete anything under `.marestail/phases/`, because a live driver runs from there.
- A repo without `[go]`: same gates, same order, same output, byte for byte. This includes the docs gate and the Sonar scanner arguments.
- Every existing language's gates, and the positions of every existing entry in `GATE_SPECS`.
- `rust_modules` keeps its current behaviour; only the new Go branch raises on error.
- Green's own gates: no Go file under `marestail/` is analysed by any of them except Sonar, which the human step above covers.
- No network in tests, and no real `go`, `gofmt`, `golangci-lint` or `gremlins` in pytest.

## Done when

The new and extended tests pass with every branch covered; `marestail gate --tier full --focus internal` in `tools/samples/go` gives the red table above and passes on the fixed scratch copy, with the results in the coder's handoff; the scope and fail-closed checks behave as listed; the README has the Go column and paragraph; `freeze.GATE_CONFIG` and `.importlinter` name the Go files and module; and every add-language checklist item holds: each gate fails closed, findings are repo-relative `path:line`, `--scope changed` filters, defaults live in the gate modules and `templates/marestail.toml`, and gate config files are frozen.
