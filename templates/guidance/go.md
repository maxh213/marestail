# Go best practices

The practices judge applies these rules to `*.go` changes.

- **GO-1 — errors are values.** Return `error` last; handle it or return it; wrap with `fmt.Errorf("...: %w", err)` and match with `errors.Is` and `errors.As`.
- **GO-2 — panic is for programmer errors.** `panic` only for programmer errors and unrecoverable state, and `recover` only at goroutine and handler boundaries.
- **GO-3 — context first.** `context.Context` is the first parameter, never stored in a struct, and carries request metadata only, never dependencies.
- **GO-4 — every goroutine has a known stop.** Never start a goroutine without a known stop: tie it to a context and wait for it before returning.
- **GO-5 — accept interfaces, return structs.** Keep interfaces small and declare them with the consumer; return concrete types from constructors.
- **GO-6 — the zero value is useful.** Design types that work without a constructor (`sync.Mutex`, `bytes.Buffer`); `nil` slices and maps are readable.
- **GO-7 — table-driven tests.** A slice of cases with `t.Run`, and run tests with `-race`.
- **GO-8 — structured logging.** Use `log/slog` (Go 1.21) and pass loggers explicitly; this rule applies only when `go.mod`'s `go` line is at least 1.21.
- **GO-9 — nothing in `init()`.** Wire dependencies at the call site; `init()` stays empty.
- **GO-10 — generics with restraint.** Type parameters only for containers and algorithms, never as an excuse for abstraction.
