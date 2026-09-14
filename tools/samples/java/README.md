# marestail java sample

A tiny Maven project used to prove the java gates go red for the right reason and
green when fixed. The violations are planted on purpose; see the header of
marestail.toml for the list.

Layout: two packages under src/main/java, JUnit 5 tests under src/test/java.

```sh
mvn test
marestail gate --tier full --focus src
```

`--focus src` puts the whole sample in scope for `java.mutation`, which otherwise only
mutates files changed against `[git] base`.
