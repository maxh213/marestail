# QA: 025 — Go gates

Run from a scratch git copy of `tools/samples/go`, with the venv's `marestail` on `PATH`. Call that copy `SAMPLE`. Commit the red tree first, so `[git] base` is `HEAD` and the copy is clean. Do not commit the copy back into marestail-green.

`go` is on `PATH` for the red and green runs. golangci-lint v2.13.2 and gremlins v0.6.0 are installed (`go version -m` shows those modules). If gremlins is missing, install it with `go install github.com/go-gremlins/gremlins/cmd/gremlins@v0.6.0` and put that bin on `PATH`.

## What you see

From a clean `SAMPLE`, `marestail gate --tier full --focus internal` prints a scope line that contains `+ focus: internal`, then one block per gate. The gate name is padded to 14 characters. Seconds vary. The findings under each failing gate are exactly these lines, in this order:

```
       internal/domain/domain.go:17 not covered
       internal/domain/domain.go:19 not covered
       internal/domain/domain.go:21 not covered
       internal/domain/domain.go:31 not covered
       internal/store/store.go:24 not covered
```

```
       internal/domain/domain.go:10 Classify crap=5.9 (cc=5, coverage=67%)
```

```
       internal/store/store.go:14 wastedassign: assigned to size, but reassigned without using the value
       internal/store/store.go:1 not gofmt formatted; run gofmt -w internal/store/store.go
```

```
       internal/domain/domain.go:6 internal/domain must not depend on internal/store (example.com/marestail/sample/internal/store)
```

```
       internal/domain/domain.go:11 CONDITIONALS_BOUNDARY at column 7 survived
       internal/domain/domain.go:12 CONDITIONALS_BOUNDARY at column 8 survived
       internal/domain/domain.go:13 CONDITIONALS_BOUNDARY at column 9 survived
       internal/domain/domain.go:14 CONDITIONALS_BOUNDARY at column 10 survived
```

```
       internal/domain/domain.go:9 comment: // Classify names the size of n.
```

```
       internal/domain/domain.go:35 Label only forwards its arguments to describe
```

```
       internal/store/store.go:23 unused function 'UnusedHelper'
```

```
       internal/store/store.go:20 environment variable SAMPLE_GREETING is not documented
```

The summaries on those gates are:

```
5 passed, statement coverage <p>%, 5 gaps on changed files (need 0)
8 functions, 1 above CRAP 4
2 problems
1 layer breaks
4 of <n> mutants not killed
1 comments or docstrings
2 modules, 1 shallow, 1 rule breaks
1 dead definitions
1 drift findings (global gate — scope: changed)
```

`<p>` is the one-decimal `totals.percent_covered` in `SAMPLE/.marestail/go-coverage.json`, greater than 0 and less than 100. `<n>` is at least 4. `README.md` does not contain `SAMPLE_GREETING`.

`marestail graph` prints this section and no `fmt` or `os` edge:

```
## Go modules
internal/domain/domain.go -> internal/store (example.com/marestail/sample/internal/store)
```

`marestail depth` includes this hard-rules line:

```
internal/domain/domain.go:35 Label only forwards its arguments to describe
```

When Sonar has been set up, the sonar findings include a line of the form `sonar coverage <x>% (need 100)` and do not include `imported no go coverage`. The report ends:

```
GATE FAILED: go.tests, go.crap, go.lint, go.deps, comments, depth, deadcode, docs, go.mutation, sonar
```

When Sonar has not been set up, sonar's summary is `not set up`, its only finding is `run: marestail sonar setup`, and the line above lists sonar in that same place. The other gates do not change.

A missing `go` fails before any of that. With `PATH` reduced to the venv `bin` and `/bin` (no `/usr/bin`, so no `go`):

```
[FAIL] go.tests       go toolchain not ready  (0.0s)
       go is not installed: install Go 1.25 or newer from https://go.dev/dl or your package manager, or set [go] go
```

The seconds can be larger than 0.0. `go.lint`, `go.deps` and `go.mutation` use that same summary and that same finding when they are not skipped.

## Steps

1. From the marestail-green root, run `marestail gate --only docs`.
   Expected: the report has no `go.tests`, `go.crap`, `go.lint`, `go.deps` or `go.mutation` line. This repo has no `[go]`.

2. In the clean committed `SAMPLE`, run `marestail gate --tier full --focus internal`.
   Expected: the red findings, summaries and `GATE FAILED` line above. `.marestail/go-coverage.json` exists and its `percent_covered` matches `<p>`. `.marestail/go-sonar-cover.out` starts with `mode: set` and uses `internal/domain/domain.go`, not the import path.

3. Run `marestail graph` and `marestail depth` in `SAMPLE`.
   Expected: the Go graph section and the Label hard-rules line above.

4. Copy `SAMPLE` to a second folder. Apply the green edits from the feature's "the same sample passes" scenario. Run `marestail gate --tier full --focus internal`.
   Expected: every gate that prints is `[ok  ]`, and the report ends `GATE PASSED`. `README.md` now contains `SAMPLE_GREETING`.

5. Back in the red `SAMPLE`, change only `internal/store/store.go` and run `marestail gate --tier full --scope changed`. Restore the file afterwards.
   Expected: every finding path is `internal/store/store.go`. Domain lines from the red list are absent.

6. Run `marestail gate --scope hard --focus internal/store/store.go` on the restored red tree.
   Expected: findings are only in `internal/store/store.go`.

7. Change only line 15 of `internal/store/store.go` (the `size = len(s.items)` line) and run `marestail gate --only go.lint --scope hyper`, then `marestail gate --only go.crap --scope hyper`. Restore the file.
   Expected: `go.lint` does not show the wastedassign line or the gofmt line, and its summary is `golangci-lint and gofmt clean; 1 file-level findings not gated under hyper`. `go.crap` passes and its summary names 1 innermost changed function.

8. Rename the `gremlins` binary aside (PATH, `GOBIN` and `GOPATH/bin`) and run `marestail gate --only go.mutation` in `SAMPLE`. Put the binary back.
   Expected: summary `gremlins missing`, finding `gremlins is not installed: go install github.com/go-gremlins/gremlins/cmd/gremlins@v0.6.0`.

9. Delete `SAMPLE/.marestail/go-coverage.json` and run `marestail gate --only go.crap`.
   Expected: summary `no coverage data; go.tests must run first`.

10. In marestail-green's `README.md`, confirm the gates table header is `| Gate | Python | TypeScript | Elixir | Ruby / Rails | C# / .NET | Erlang | Rust | Java | Go |` and the Go column's last cell is `local SonarQube, built-in Go analyzer + coverprofile`.
    Expected: that header, a separator of ten `---` cells, and a Go paragraph after the Java paragraph. `marestail gate --only docs` from the green root still passes.
