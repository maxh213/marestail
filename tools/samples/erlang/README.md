# marestail erlang sample

A tiny plain-Erlang app (no rebar3) used to prove the erlang gates go red for
the right reason and green when fixed. The violations are planted on purpose;
see the header of marestail.toml for the list.

Layout: modules under src/, the eunit suite under test/.

Compile and run the suite:

```sh
mkdir -p ebin
erlc -o ebin src/*.erl test/*.erl
erl -noshell -pa ebin -eval 'eunit:test(box_tests)' -s init stop
```
