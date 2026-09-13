# marestail rust sample

A tiny library crate used to prove the rust gates go red for the right reason and
green when fixed. The violations are planted on purpose; see the header of
marestail.toml for the list.

Layout: modules under src/, the integration tests under tests/.

```sh
cargo test
marestail gate --tier full
```
