Feature: Hard-scoped install leaves CLAUDE.md and AGENTS.md alone

  After this task, `marestail install --scope hard .` in a shared repo adds no
  Gate section to CLAUDE.md and creates no AGENTS.md. Full install (no --scope,
  or --scope all / changed) still appends the Gate section as today.

  Background:
    Given an existing empty temporary directory as the install target
    And GROK_HOME points at a fresh empty directory (so trust may print once)

  Scenario: full install still creates the Gate section
    When I run `marestail install <target>` (or `install(target)` / `marestail install --scope all <target>`)
    Then exit code is 0
    And `<target>/CLAUDE.md` and `<target>/AGENTS.md` both exist and contain `marestail gate`
    And stdout ends with `installed into <target>; edit marestail.toml and sonar-project.properties\n`
    And that line does not contain `CLAUDE.md`
    When I empty the target and run `marestail install --scope changed <target>`
    Then exit code is 0
    And `<target>/CLAUDE.md` and `<target>/AGENTS.md` both exist and contain `marestail gate`
    And stdout ends with the same full-install closing line (no `CLAUDE.md` in that line)

  Scenario: hard install on an empty target creates neither agent-doc file
    When I run `marestail install --scope hard <target>` (same as `install(target, hard=True)`)
    Then exit code is 0
    And `<target>/CLAUDE.md` does not exist
    And `<target>/AGENTS.md` does not exist
    And stdout ends with `installed into <target>; left CLAUDE.md and AGENTS.md alone; edit marestail.toml and sonar-project.properties\n`

  Scenario: hard install leaves an existing CLAUDE.md byte-for-byte
    Given `<target>/CLAUDE.md` holds exactly `team rules\n`
    When I run `marestail install --scope hard <target>`
    Then `<target>/CLAUDE.md` is still exactly `team rules\n`
    And `<target>/AGENTS.md` does not exist

  Scenario: hard install leaves an existing AGENTS.md byte-for-byte
    Given `<target>/AGENTS.md` holds exactly `team rules\n`
    When I run `marestail install --scope hard <target>`
    Then `<target>/AGENTS.md` is still exactly `team rules\n`
    And `<target>/CLAUDE.md` does not exist

  Scenario: hard install implies --gitignore-generated
    When I run `marestail install --scope hard <target>` without `--gitignore-generated`
    Then `<target>/.gitignore` contains every path in `install.GITIGNORE_GENERATED_LINES`
    And the same lines appear when `install(target, hard=True)` is called with `gitignore_generated=False`

  Scenario: hard install still writes the rest of the install tree
    When I run `marestail install --scope hard <target>`
    Then these exist with their template / merged content: `marestail.toml`, `sonar-project.properties`, `tasks/README.md`, `PERFORMANCE.md`, `guidance/ts.md`
    And `.claude/settings.json` Stop hooks include `marestail gate --hook`
    And `.agents/hooks.json`, `.grok/hooks/marestail-gate.json`, and `.cursor/hooks.json` likewise carry the gate Stop hook
    And Grok folder trust still runs (stdout may contain `trusted <target> for grok project hooks` before the install line)

  Scenario: --gitignore-generated without --scope hard is unchanged
    When I run `marestail install --gitignore-generated <target>`
    Then `.gitignore` includes `GITIGNORE_GENERATED_LINES`
    And `CLAUDE.md` and `AGENTS.md` still contain `marestail gate`

  Scenario: full install still respects GATE_MARKER
    Given `<target>/CLAUDE.md` already contains `marestail gate`
    When I run `marestail install <target>` again
    Then `CLAUDE.md` is unchanged (no second Gate append)

  Scenario: hard install removes nothing from a target that already has the Gate
    Given a target previously full-installed so `CLAUDE.md` and `AGENTS.md` hold the Gate section
    When I run `marestail install --scope hard <target>`
    Then both files are byte-identical to before that hard install

  Scenario: invalid --scope is rejected
    When I run `marestail install --scope soft <target>`
    Then the exit code is non-zero and stderr names the allowed choices `all`, `changed`, `hard`

  Scenario: install --help lists --scope
    When I run `marestail install --help`
    Then the output lists `--scope` and `--gitignore-generated`
    And `--scope` choices are `all`, `changed`, `hard` (same `add_scope` choices as `gate` and `run`)

  Scenario: freeze and hard gate/run stay as they are
    Then `CLAUDE.md`, `AGENTS.md` and `GEMINI.md` remain in the freeze list
    And `python3 tools/test-scope-hard.py` still exits 0 (gate/run `--scope hard`, `hook_scope`, `MARESTAIL_SCOPE` / `MARESTAIL_FOCUS` unchanged)

  Scenario: README documents hard install
    Then the quick-start install lines name `marestail install --scope hard`
    And they say it leaves `CLAUDE.md` and `AGENTS.md` alone and implies `--gitignore-generated`
    And the `--gitignore-generated` paragraph says the same
    And the sentence that `CLAUDE.md` and `AGENTS.md` are shared documentation applies to a full install only

  Scenario: diagnostic install tests cover the cases
    Then `tools/test-practices.py` or `tools/test-install-hard.py` covers each case in the task's Tests list
    And `python3 tools/test-practices.py`, `python3 tools/test-perf.py` and `python3 tools/test-scope-hard.py` still pass
