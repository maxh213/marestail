# marestail go sample

A tiny Go module used to prove the Go gates go red for the right reason and
green when fixed. The violations are planted on purpose; see the header of
marestail.toml for the list.

Layout: packages under internal/domain and internal/store.

```sh
go test ./...
marestail gate --tier full --focus internal
```

`--focus internal` puts every file in scope for `go.mutation`, which otherwise
mutates only files changed against `[git] base`.

Fixes, applied only in a scratch copy: walk a table in Classify and cover each
bound, drop the comment, the pass-through and the store import from domain,
format the store composite literal, return the length directly, give Store its
own label method and test it, delete the unused helper, and document the
environment variable the store reads.
