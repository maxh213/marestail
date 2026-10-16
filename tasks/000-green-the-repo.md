# Bring marestail itself through its own gate

This is a refactor: nothing about marestail's behaviour may change. Every subcommand (`gate`, `run`, `install`, `sonar`, `route`, `watch`, `perf`), every flag, every gate name and verdict format, every prompt sent to a worker, the freeze rules, the handoff and run-log layout under `.marestail/`, and the wording of every log line stay exactly as they are. The existing scripts under `tools/test-*.py` must keep passing when run directly with `python3`, exactly as they do today.

Make `marestail gate --tier full` pass for the Python package under `marestail/`:

- 100% line and branch coverage of `marestail/` (excluding `marestail/tui/`, which is out of scope) from pytest tests under `tests/`. Subprocesses (git, docker, the agent CLIs, the gate toolchains) are faked at the `marestail.shell.run` seam or with temporary git repositories; no test may call a real agent CLI, a real SonarQube, or the network.
- Every function at CRAP 4 or below. Split long orchestration functions in `runner.py`, `install.py`, `context.py` and the gates into small named helpers.
- No comments or docstrings anywhere under `marestail/`. Where a comment carries knowledge, turn it into a name, a small function, or a test.
- `ruff check`, `ruff format --check` and `mypy --strict` clean under the configuration in `pyproject.toml`.
- The import contracts in `.importlinter` kept.
- No dead code: `vulture` at 60% confidence reports nothing under `marestail/`.
- Module depth contracts kept: no pass-through forwarders.
- Every environment variable the package reads is named in `README.md`.
- Sonar: quality gate green, zero open issues, zero hotspots to review, zero duplication.

Out of scope: `marestail/tui/`, `tools/`, `templates/`, `roles/`, `guidance/`, and the language scanners under `marestail/cs`, `marestail/erl`, `marestail/ex`, `marestail/js`, `marestail/jvm`, `marestail/rb`, `marestail/rs` that are not Python. Do not add runtime dependencies: the package must keep running on the standard library alone.
