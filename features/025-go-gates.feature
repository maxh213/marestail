Feature: marestail gate measures a Go module

  A repo with `[go]` gets `go.tests`, `go.crap`, `go.lint`, `go.deps` and
  `go.mutation`, plus Go branches of comments, depth, deadcode, docs, Sonar,
  `marestail depth` and `marestail graph`. A repo without `[go]` is unchanged.
  The adapter module is `marestail.golang` (`marestail/golang.py`). Context
  methods are `go` and `go_root`, the same shape as `java` and `java_root`,
  placed after `java_root`.

  `templates/guidance/go.md`, `templates/guidance/patterns/go.md` and the
  `uses_go` install check are already shipped. This task does not edit them
  and does not change `marestail install`.

  Background:
    Given the `[go]` defaults live in `marestail/golang.py` and the gate modules
    And `[go] root` defaults to `.`, `go` to `go`, `crap_max` to 4, `build_tags` to `[]`, `test_args` to `[]`, `coverage_exclude` to `[]`, `mutation_scope` to `changed` and `mutation_timeout` to 7200
    And `mutation_exclude`, `golangci_config`, `golangci_lint` and `gremlins` default to unset
    And `deadcode_ignore` defaults to `[]`
    And the floor is Go 1.25, because gremlins v0.6.0 and `marestail/go/scan/go.mod` both say `go 1.25`
    And the pins are golangci-lint module `github.com/golangci/golangci-lint/v2` at `v2.13.2`, and gremlins module `github.com/go-gremlins/gremlins` at `v0.6.0`
    And the install lines, used word for word, are:
      | tool | line |
      | go | install Go 1.25 or newer from https://go.dev/dl or your package manager, or set [go] go |
      | golangci-lint | go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@v2.13.2 |
      | gremlins | go install github.com/go-gremlins/gremlins/cmd/gremlins@v0.6.0 |

  Scenario: a repo without a go section does not change
    Given this repo's `marestail.toml` has no `[go]`
    When the fast, sonar, full, qa and all tiers are listed for this repo
    Then they stay `tests/test_green_repo.py` `FAST_GATES` and `test_every_tier_names_the_same_gates_in_the_same_order`
    And `marestail gate` output, the docs files it reads, and the Sonar scanner arguments are byte for byte what they are today
    And `tests/test_gates_docs.py` `test_source_files_skip_tests_benchmarks_and_other_suffixes` still returns `src/a.py` and `src/b.rs`
    And `tests/test_gates_sonar.py` `test_scanner_exclusions`, `test_java_properties`, `test_language_findings` and `test_languages_present` keep their current expected values
    And `tests/test_depth.py` `test_rust_modules_empty_and_error` still returns `[]` on a scanner error
    And `tests/test_run_suffix.py` still counts 38 import contracts
    And nothing under `.marestail/phases/` is edited

  Scenario: five gates join the registry and nothing else moves
    Given `tests/test_gates_init.py` `LANGUAGES` gains `("go", "go")` after `("java", "java")`
    When the registry is built
    Then `go.tests`, `go.crap`, `go.lint` and `go.deps` are fast and sit straight after `java.deps`, before `comments`
    And `go.mutation` is full and sits straight after `java.mutation`, before `sonar`
    And `tests/test_gates_init.py` `test_select_by_tier` counts 41 fast gates and 53 gates in `all`
    And `test_select_only` still returns `py.tests`, `py.mutation`, `sonar` in that order
    And `test_select_by_tier` still returns `qa` and `visual` as the only non-fast qa gates
    And a gate runs only when `[go]` exists, except comments, depth, deadcode, docs and sonar, which gain a Go branch only when `[go]` exists

  Scenario: the shipped sample is red for the right reason
    Given `tools/samples/go` is the verbatim tree from the task, module `example.com/marestail/sample`, with `[git] base` `HEAD` and `[comments] paths` `internal`
    And the working tree under that directory is clean and those files are committed
    When `marestail gate --tier full --focus internal` runs there
    Then the first line contains `+ focus: internal`
    And the findings, in this order inside each gate, are exactly:
      | gate | finding |
      | go.tests | internal/domain/domain.go:17 not covered |
      | go.tests | internal/domain/domain.go:19 not covered |
      | go.tests | internal/domain/domain.go:21 not covered |
      | go.tests | internal/domain/domain.go:31 not covered |
      | go.tests | internal/store/store.go:24 not covered |
      | go.crap | internal/domain/domain.go:10 Classify crap=5.9 (cc=5, coverage=67%) |
      | go.lint | internal/store/store.go:14 wastedassign: assigned to size, but reassigned without using the value |
      | go.lint | internal/store/store.go:1 not gofmt formatted; run gofmt -w internal/store/store.go |
      | go.deps | internal/domain/domain.go:6 internal/domain must not depend on internal/store (example.com/marestail/sample/internal/store) |
      | go.mutation | internal/domain/domain.go:11 CONDITIONALS_BOUNDARY at column 7 survived |
      | go.mutation | internal/domain/domain.go:12 CONDITIONALS_BOUNDARY at column 8 survived |
      | go.mutation | internal/domain/domain.go:13 CONDITIONALS_BOUNDARY at column 9 survived |
      | go.mutation | internal/domain/domain.go:14 CONDITIONALS_BOUNDARY at column 10 survived |
      | comments | internal/domain/domain.go:9 comment: // Classify names the size of n. |
      | depth | internal/domain/domain.go:35 Label only forwards its arguments to describe |
      | deadcode | internal/store/store.go:23 unused function 'UnusedHelper' |
      | docs | internal/store/store.go:20 environment variable SAMPLE_GREETING is not documented |
    And the summaries are `5 passed, statement coverage <p>%, 5 gaps on changed files (need 0)`, `8 functions, 1 above CRAP 4`, `2 problems`, `1 layer breaks`, `4 of <n> mutants not killed`, `1 comments or docstrings`, `2 modules, 1 shallow, 1 rule breaks`, `1 dead definitions` and `1 drift findings (global gate — scope: changed)`
    And `<p>` is `totals.percent_covered` from `.marestail/go-coverage.json`, one decimal, greater than 0 and less than 100
    And `<n>` is at least 4, and those four mutation lines are the whole findings list
    And the mutation columns are the 1-based byte columns of `>=` and the three `>` operators, tabs counting as one byte
    And `README.md` does not contain `SAMPLE_GREETING`
    And the run ends `GATE FAILED: go.tests, go.crap, go.lint, go.deps, comments, depth, deadcode, docs, go.mutation, sonar` when Sonar has been set up
    And when Sonar has not been set up, sonar's summary is `not set up` and its only finding is `run: marestail sonar setup`, and the other gates are unchanged
    And when Sonar has been set up, one finding matches `sonar coverage <x>% (need 100)` and none contains `imported no go coverage`

  Scenario: the same sample passes once the planted defects are fixed
    Given a scratch copy of `tools/samples/go`, not committed back to this repo
    And `Classify` is the table walk over bounds 41 huge, 31 big, 21 mid and 10 small, then `tiny`, and the comment, `Label`, `describe` and the `fmt` and `store` imports are gone
    And `TestClassifyExtremes` covers 9, 10, 20, 21, 30, 31, 40 and 41, `Tag(1)` is tested, and `TestLabel` and the store import are gone from the domain test
    And `New` returns `Store{items: items}`, `Size` returns `len(s.items)`, `Store.Label` returns `fmt.Sprintf("box %d", s.Size())`, and `UnusedHelper` is gone
    And the store test asserts `New([]int{0, 1}).Label()` equals `box 2`
    And `README.md` documents `SAMPLE_GREETING`
    When `marestail gate --tier full --focus internal` runs in that copy
    Then when Sonar has not been set up, sonar's summary is `not set up`, its only finding is `run: marestail sonar setup`, every other gate that runs is ok, and the report ends `GATE FAILED: sonar`
    And when Sonar has been set up, every gate that runs is ok, the report ends `GATE PASSED`, and no finding contains `imported no go coverage`

  Scenario: scope keeps only the file the user touched
    Given the committed red sample
    When only `internal/store/store.go` is modified and `marestail gate --tier full --scope changed` runs
    Then every finding is in `internal/store/store.go`
    And `go.mutation` is aimed only at that file: `internal/domain/domain.go` is passed to gremlins as `-E ^internal/domain/domain\.go$`, and `store.go` is not excluded
    When `marestail gate --scope hard --focus internal/store/store.go` runs
    Then the same file is gated as fully changed, and domain findings are absent
    When the only diff is line 15 of `internal/store/store.go` and `--scope hyper` runs
    Then `go.lint` does not report the line-14 wastedassign finding or the gofmt finding
    And the `go.lint` summary is `golangci-lint and gofmt clean; 1 file-level findings not gated under hyper`
    And `go.crap` scores only `Size` and passes
    And a `go.mutation` survivor on another line is dropped, and if none sit on line 15 the summary is `no mutants on changed lines`

  Scenario: a missing tool or a missing report fails closed
    When `go` is not on `PATH`, `go.tests` deletes `.marestail/go-cover.out`, `.marestail/go-coverage.json` and `.marestail/go-sonar-cover.out`, then fails
    And the summary is `go toolchain not ready` and the only finding is `go is not installed: install Go 1.25 or newer from https://go.dev/dl or your package manager, or set [go] go`
    And on this sample that finding is `<go> mod edit -json` exiting 127, because `go.mod` is present, and `go env` does not run
    And `go.lint`, `go.deps` and `go.mutation` fail the same way when they get past their earlier skip
    And `go.crap` with no `.marestail/go-coverage.json` still fails `no coverage data; go.tests must run first` and does not call preflight
    And `go.deps` with no `.go-layers.json` still fails `no .go-layers.json; copy templates/go-layers.json and name the layers` with finding `.go-layers.json:1 missing`
    And `go.lint` with `--scope changed` and no changed `.go`, `go.mod` or `go.sum` under the go root skips `no changed Go files` without calling preflight
    When go is present and gremlins is not on `PATH`, not at `GOBIN/gremlins` and not at the first `GOPATH` entry's `bin/gremlins`
    Then `go.mutation` fails with summary `gremlins missing` and finding `gremlins is not installed: go install github.com/go-gremlins/gremlins/cmd/gremlins@v0.6.0`
    When `go test` exits 0 and `.marestail/go-cover.out` is missing
    Then `go.tests` fails with summary `go test wrote no coverage profile`
    When the profile is only `mode: set`
    Then `go.tests` fails with summary `coverage report lists no source files`
    When gremlins exits without writing `.marestail/go-mutation.json`
    Then the summary is `gremlins wrote no report (exit <n>); it writes none when it finds no mutants or its test run fails`
    When the report exists and every mutant is `NOT VIABLE` or `SKIPPED`
    Then the summary is `no mutants were generated`

  Scenario: the toolchain and the two pinned tools
    When `<root>/go.mod` is missing
    Then the problem is `<rel>/go.mod:1 missing; [go] root must name the folder that holds go.mod`, which for root `.` is `go.mod:1 missing; [go] root must name the folder that holds go.mod`
    And neither `<go> mod edit` nor `<go> env` runs
    When `go.mod` is present and `<go> mod edit -json` exits 127
    Then the problem is the go install line, and `<go> env` does not run
    When `go.mod` is present and `<go> mod edit -json` exits with any other non-zero code, or the combined output is not one JSON object, or `Module.Path` is not a string
    Then the problem is `go mod edit -json failed: <last 300 characters>`, and `<go> env` does not run
    When that command returns `Module.Path` and `<go> env -json GOVERSION GOROOT GOBIN GOPATH` exits 127
    Then the problem is the go install line
    When that env command's exit is any other non-zero code, or its combined output is not one JSON object
    Then the problem is `go env failed: <last 300 characters>`
    And `go1.24.9` is `go 1.25 or newer is required, found go1.24.9: <go install line>`
    And `go1.25.0` and `go1.27.0-X:nodwarf5` are accepted
    And preflight stops at the first of these problems, so a missing `go.mod` or a `go mod edit` problem is never replaced by a `go env` problem
    When `golangci-lint` or `gremlins` is configured, else on `PATH`, else in `GOBIN`, else in the first `GOPATH` `bin`
    Then `<go> version -m <path>` must show a tab-separated `mod` line for the pinned module and version
    And any other module, version, `(devel)`, or no `mod` line fails `<name> at <path> is <found>; the gate is pinned to <module> <version>: <install line>`
    And `<found>` is the module and version joined by a space, or `no module version` when the `mod` line is absent

  Scenario: go.tests counts statements a test in the same package ran
    Given `go.tests` starts by deleting the three coverage files
    When it runs, the command in the go root, timeout 3600, is `<go> test -json -count=1 -covermode=set -coverprofile <work>/go-cover.out` plus `-tags a,b` when `build_tags` is `a` and `b`, plus `test_args`, plus `./...`
    And there is no `-coverpkg`
    And a package pass with no `Test` key does not count, a subtest pass does, and a `fail` event does not
    And lines that do not start with `{` are kept as text and are not parsed
    And a non-zero exit other than 127 has summary `tests failed` and findings `tail` of every event `Output` in order plus the non-JSON lines
    And profile lines outside `<module>/` are dropped, and duplicate blocks keep the greater count
    And a generated file and a `coverage_exclude` match are dropped
    And `.marestail/go-coverage.json` maps each block's start line to the lowest count of the blocks that start on it
    And `percent_covered` is covered statements over total statements, stored at one decimal
    And `.marestail/go-sonar-cover.out` starts `mode: set` and uses repo-relative paths, not import paths
    And a zero-count block is `<file>:<startLine> not covered`, one per block, in path order
    And under a scope it is kept when any line from the block start through the block end is gated, and the summary says `<n> gaps on changed files`
    And with no scope the summary says `<n> gaps` and does not say `on changed files`

  Scenario: go.crap uses statement coverage inside the function
    Given no coverage file fails before preflight, and no in-scope source after `coverage_exclude` skips `no files in scope`
    When the complexity scanner fails, the summary is `complexity scanner failed` and the finding is that error
    And a function's coverage is the covered statements in blocks whose start line lies in `[line, end]`, over all statements in those blocks, or 0 when there are none
    And the score is `_crap.crap_score` and the finding is `_crap.describe`
    And the limit is `[go] crap_max`
    And under hyper it calls `_hyper_crap.judged` with `Hyper("go.crap", limit, base_units)`, and `base_units` scans the base copy in `complexity` mode

  Scenario: go.lint runs the pinned config and gofmt
    Given the bundled config is `marestail/go/golangci.yml` with the task's content, and `[go] golangci_config` replaces it when set
    When the gate runs, it deletes `.marestail/go-lint.json` and runs in the go root, timeout 1800, with env exactly `GOLANGCI_LINT_CACHE` set to `<work>/golangci-cache`:
      ```
      golangci-lint run --config <cfg> --allow-parallel-runners --path-mode abs --show-stats=false --max-issues-per-linter=0 --max-same-issues=0 --output.json.path <work>/go-lint.json ./...
      ```
    And `build_tags` `a,b` adds `--build-tags a,b` before `./...`
    And exit 0 or 1 with a report yields `<rel>:<Line> <FromLinter>: <first line of Text>`, deduplicated, then `ctx.in_scope`, then `ctx.on_changed_lines`
    And a `typecheck` issue is that same shape
    And any other exit, or a missing report, fails `golangci-lint failed (exit <n>)` with the output tail
    And gofmt is `<GOROOT>/bin/gofmt -l` on the in-scope `.go` files, tests included, timeout 300
    And each listed file is `<rel>:<placeholder> not gofmt formatted; run gofmt -w <rel>`, and the placeholder is 1 except under hyper, where it is 0 and the finding is dropped as file-level
    And a non-zero gofmt exit fails `gofmt failed: <tail>`
    And the summary is `golangci-lint and gofmt clean` or `<n> problems`, and findings are capped at 60
    And a missing golangci-lint fails summary `golangci-lint missing` before gofmt

  Scenario: go.deps checks the layer file only
    Given no `.go-layers.json` fails before the empty-sources skip, and no sources skips `no Go sources`
    When `internal/domain` imports `example.com/marestail/sample/internal/store` and the contract forbids `internal/store`
    Then the finding is `<file>:<line> internal/domain must not depend on internal/store (<import path>)`
    And `from` and `forbid` cover subfolders
    And `forbid_external` is an `fnmatch` on the import path, so `github.com/jackc/pgx/*` matches `github.com/jackc/pgx/v5`
    And an import outside the module is named by its import path
    And findings go through `ctx.in_scope`, then `ctx.on_changed_lines`, and are capped at 60
    And the passing summary is `layer contracts kept; the go toolchain rejects import cycles and internal/ imports itself`
    And the failing summary is `<n> layer breaks`
    And `templates/go-layers.json` is the contract in the task

  Scenario: go.mutation runs gremlins on the scoped files
    Given `mutation_scope` `some` fails `[go] mutation_scope must be "changed" or "all", got 'some'` before preflight
    And a scoped run with no Go file skips `no changed Go sources`, and a full run with no sources skips `no Go sources`
    And targets are sources in the scope, minus `mutation_exclude`, or minus `coverage_exclude` when `mutation_exclude` is unset
    And an explicit `mutation_exclude` of `[]` does not fall back to `coverage_exclude`
    When gremlins runs, the command in the go root, timeout `[go] mutation_timeout`, ends in `.` and is:
      ```
      gremlins unleash --config /dev/null --output <work>/go-mutation.json --arithmetic-base=true --conditionals-boundary=true --conditionals-negation=true --increment-decrement=true --invert-negatives=true --invert-logical=true --invert-loopctrl=true --invert-assignments=false --invert-bitwise=false --invert-bwassign=false --remove-self-assignments=false
      ```
    And `--workers <N>` is inserted after those flags only when `jobs.pick(ctx.config, "go", "mutation_workers", None)` is not None
    And `MARESTAIL_JOBS` wins, then `[go] mutation_workers` as written, then `[run] jobs`
    And with none of them set, the command has no `--workers` flag
    And `MARESTAIL_JOBS=0` fails the gate `go.mutation crashed: SystemExit MARESTAIL_JOBS must be a whole number 1 or more, got '0'` and does not start gremlins
    And `[go] mutation_workers` of 0 is passed as `--workers 0`
    And `--tags a,b` follows the workers flag when `build_tags` is set
    And each non-target `.go` source adds `-E ^<re.escape(relative)>$`, in sorted order
    And there is no `--integration`, no `--coverpkg` and no `--diff`
    And `KILLED` and `TIMED OUT` are killed, `NOT VIABLE` and `SKIPPED` are ignored, and `LIVED`, `NOT COVERED` and `RUNNABLE` are not killed
    And a `LIVED` finding is `<rel>:<line> <TYPE> at column <column> survived`
    And a `NOT COVERED` finding ends `not covered by its package's tests`
    And the summary is `<k> of <n> mutants not killed` or `all <n> mutants killed`, plus the scope note, and findings are capped at 60
    And under hyper, no survivor on a changed line passes with exactly `no mutants on changed lines`

  Scenario: the scanner reads syntax and the other gates use it
    Given `marestail/go/scan` is module `marestail/goscan`, `go 1.25`, no `require`, package `main`, standard library only, no comments, and no `_test.go`
    And the binary is `.marestail/go-scan/marestail-go-scan` and the stamp is the sha256 of `main.go` then `go.mod`
    When the stamp matches, the build does not run
    And otherwise the build is `<go> build -trimpath -o <binary> .` with cwd `marestail/go/scan`, timeout 300, and env exactly `GOTOOLCHAIN=local`, `GOWORK=off`, `GOFLAGS` empty and `CGO_ENABLED=0`
    And exit 127 is the go install line, and any other failure is `go scanner build failed: <last 300 characters>`
    And `scan` with no paths returns an empty list and does not build
    And a scanner failure is `go scanner failed (<mode>): <last 300 characters>`
    And comments, deadcode and depth call it the way they call the Rust scanner
    And `go_findings` is registered after `java_findings` in both scanner lists, and before `markup_findings` in comments
    And a comment directive `//go:`, `//line `, `//export ` or `//extern ` is not a finding, and `//nolint` is
    And the comment group directly above `import "C"` is not a finding
    And a file whose header matches `// Code generated .* DO NOT EDIT.` before `package` is skipped everywhere, and the same text after `package` is not
    And `vendor`, `testdata`, `.marestail`, `.git`, `node_modules`, a dot or underscore folder, a nested module, and `perf/` are skipped
    And `_test.go` is a test, not a source, and comments still scan tests
    And a pass-through is a plain function whose body is one call of its parameters in order, and a method is never one
    And deadcode reports an exported function, type, var or const in a non-`main` package whose name is seen no more often than it is defined, never `main`, `init` or a method
    And `[go] deadcode_ignore` drops those names
    And a scanner error in `marestail depth` raises `SystemExit` `go depth analysis failed: go scanner failed (depth): <tail>`, and `rust_modules` still returns `[]`
    And `marestail graph` prints `## Go modules` after `## Java modules`, and a module import is `<file> -> <folder> (<import path>)`
    And on the red sample that line is `internal/domain/domain.go -> internal/store (example.com/marestail/sample/internal/store)`, and `fmt` and `os` are absent
    And a graph scanner error is the body of that section

  Scenario: docs and sonar grow a go branch and leave everyone else alone
    Given `[go]` is present, a non-test `.go` file is a documented source, and `_test.go` is not
    And the route patterns match `http.HandleFunc("/health"`, `mux.HandleFunc("GET /health"` and `r.GET("/items"`, and the env pattern matches `os.Getenv("SAMPLE_GREETING")` and `os.LookupEnv("SAMPLE_GREETING")`
    And those patterns are applied to `.go` files only
    And a repo with a `.go` file and no `[go]` does not report them, and a `.py` file containing `os.Getenv("SAMPLE_GREETING")` is not a Go finding
    When `[go]` exists and `sonar-project.properties` does not set the keys, the scanner arguments gain `-Dsonar.go.coverage.reportPaths=<work>/go-sonar-cover.out` after the Java arguments
    And a non-empty `coverage_exclude` also adds `-Dsonar.coverage.exclusions=` joined by commas, as written, with no root prefix
    And a key already in `sonar-project.properties` is not passed
    And the exclusions gain `**/*_test.go`, `**/vendor/**` and `**/testdata/**` when missing, after `perf/**`
    And a `[dotnet]` scan is unchanged
    And the Go language check is after Java: marker `go=`, label `Go`, where `golang.rel` of the go root, advice `; put the module in sonar.sources`, coverage finding `marestail.toml:1 SonarQube imported no go coverage; run go.tests first so .marestail/go-sonar-cover.out exists`

  Scenario: the readme, the template and the freeze list name go
    Given the gates table gains a `Go` column after `Java`
    And the header is `| Gate | Python | TypeScript | Elixir | Ruby / Rails | C# / .NET | Erlang | Rust | Java | Go |`
    And the separator has ten `---` cells
    And the Go cells, top to bottom, are `go test -coverprofile, statement blocks`, `go/ast scanner + coverprofile`, `gremlins`, `imports vs `.go-layers.json` (the go tool rejects cycles and internal/)`, `golangci-lint (pinned config), gofmt`, `go/ast comment groups, directives exempt`, `go/ast scanner (pass-throughs); internal/ enforced by the go tool`, `golangci-lint unused, unreferenced exported names`, `regex over sources`, `—`, `local SonarQube, built-in Go analyzer + coverprofile`
    And a paragraph after the Java paragraph covers, in order, the Go 1.25 check, the two pinned install lines, `go version -m`, `GOBIN` and `GOPATH/bin`, the scanner built under `.marestail/go-scan` with no network, statement coverage by the package's own tests, no branch coverage, the bundled lint config and `--allow-parallel-runners`, `gofmt -l`, `.go-layers.json`, deadcode, pass-throughs, comments, generated files, gremlins, and the Sonar cover profile
    And no backticked path under `marestail/`, `tools/` or `templates/` is one that does not exist
    And `tests/test_jobs.py` `PRECEDENCE_SENTENCE` becomes this one sentence, and the rest of that paragraph stays:

      ```
      The variable wins, then `[python] mutation_workers` (mutmut `--max-children`), `[elixir] muex_concurrency` (muex `--concurrency`), `[rust] mutation_jobs` (cargo-mutants `--jobs`), `[java] mutation_threads` (PIT `-Dthreads`) and `[go] mutation_workers` (gremlins `--workers`) for their own language, then `[run] jobs`, then each tool's default: StrykerJS takes `--concurrency`, mutant takes `--jobs`, Stryker.NET takes `--concurrency`, the Erlang gate sizes its own worker pool with it, and gremlins is passed `--workers` only when that count is set, and a value from the variable or `[run] jobs` is capped at the CPU count.
      ```
    And `PARAGRAPH_CLAUSES` stay present, the three template worker comments stay, and `qa/023-one-knob-for-mutation-workers.md` is not edited
    And `templates/marestail.toml` gains the task's `[go]` block after `[java]` and before `[sonar]`
    And `freeze.GATE_CONFIG` gains, after `.java-layers.json`, `**/go.mod`, `**/go.sum`, `go.work`, `go.work.sum`, `**/.golangci.yml`, `**/.golangci.yaml`, `**/.golangci.toml`, `**/.golangci.json` and `.go-layers.json`
    And `ALLOWED["architect"]` gains `.go-layers.json`
    And `.gitignore` is unchanged
    And `sonar-project.properties` exclusions gain `,marestail/go/**` at the end of that one line and nowhere else
    And that file is frozen, so the coder makes the edit and the runner records it as a proposal
    And the architect, not the coder, adds `marestail.golang` immediately after `marestail.rust` in the layers language line, in `foundations-import-nothing-above`, in `language-adapters-stay-below-gates`, and in `remote-box-knows-no-language`

  Scenario: the new tests are hermetic and the old assertions that move are named
    Given pytest never starts `go`, `gofmt`, `golangci-lint` or `gremlins`, which join `FORBIDDEN_BINARIES` in `tests/conftest.py`
    And the new files are `tests/test_golang.py`, `tests/test_gates_go_tests.py`, `tests/test_gates_go_crap.py`, `tests/test_gates_go_lint.py`, `tests/test_gates_go_deps.py` and `tests/test_gates_go_mutation.py`
    And `tests/test_context.py` `LANGUAGES` gains `go`, so `go` and `go_root` follow `java` and `java_root`
    And `tests/test_depth.py` `SCANNERS` gains `go` last, and `test_raw_modules_only_runs_configured_sections` still returns `python`, `rust`, `java`
    And `tests/test_green_repo.py` `SCANNER_SOURCES` gains `marestail/go/scan/main.go`, and `FROZEN_SCANNER_PROJECTS` gains `marestail/go/scan/go.mod` and `marestail/go/golangci.yml`
    And `test_package_mutation_patterns_cover_every_module` gains `marestail.golang.*` beside `marestail.rust.*`
    And comments, deadcode, depth, graph, docs, sonar and freeze gain the Go cases the task names
    And `tests/test_gates_comments.py` keeps `dotnet_findings`, `rust_findings` and `java_findings` as three empty lists when those sections are absent
    And `go_findings` is `[]` when `[go]` is absent
