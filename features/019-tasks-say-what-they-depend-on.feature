Feature: Task files say what they depend on

  A task file may open with a TOML block. The block is present only when line 1
  is `+++` with an optional trailing CR. It ends at the next line that is exactly
  `+++`, again with an optional trailing CR. The bytes between those lines are
  parsed with `tomllib.loads`. The bytes after the closing line's line break are
  the body. Any other first line means there is no block, and a later `+++` is
  body text.

  The id is the file name without `.md`. Inside a block the only keys are
  `depends` (required; an array of task ids; `[]` is allowed) and `stack`
  (a bool, required when `depends` is not empty, forbidden when it is empty).
  `stack = true` needs exactly one dependency. `marestail tasks check` and
  `marestail run` only check `stack`; they do not switch branches.
  `marestail run` checks that one file's own block. A dependency file that
  is missing does not stop the run.

  `marestail tasks check` writes nothing and exits 0 when the paths are clean,
  including an empty folder. Otherwise it prints one `<path>: <problem>` line
  per problem on stdout, leaves stderr empty, and exits 1. It does not read
  `marestail.toml`. Paths are printed as `str(Path(the argument))`, so a
  trailing slash is dropped and `./tasks/x.md` is printed `tasks/x.md`. The
  same file reached twice is reported once, under the first spelling. Two
  different files with one id are ordered by path string, not by argument
  order: each later path is a duplicate of the earliest.

  In a quoted byte string or an Examples cell, `\n` is one LF and `\r` is one
  CR. There is no other escape. A docstring given as exact stdout or stderr is
  those lines joined by LF, plus one trailing LF, and no extra blank line.
  The current parser already has `visual` between `perf` and `route`; `tasks`
  is inserted directly after `run`.

  Scenario: a well-formed folder checks clean, with or without marestail.toml
    Given an empty directory "empty/"
    When I run `marestail tasks check empty`
    Then the exit code is 0
    And stdout is empty
    And stderr is empty
    Given a directory "notoml/" with no marestail.toml and these files:
      | path | bytes |
      | notoml/tasks/018-a.md | +++\ndepends = []\n+++\n# 018\n |
      | notoml/tasks/019-b.md | +++\ndepends = ["018-a"]\nstack = true\n+++\n# 019\n |
      | notoml/tasks/020-c.md | +++\ndepends = ["018-a", "019-b"]\nstack = false\n+++\n# 020\n |
      | notoml/tasks/021-crlf.md | +++\r\ndepends = []\r\n+++\r\n# ok\n |
    When I run `marestail tasks check` with the current directory "notoml/"
    Then the exit code is 0
    And stdout is empty
    And stderr is empty
    When I run `marestail tasks check notoml/tasks` from this checkout
    Then the exit code is 0
    And stdout is empty

  Scenario: a folder skips READMEs in any case, ignores other files, and is not recursive
    Given a directory "named/" containing:
      | path | bytes |
      | named/README.md | # guide\n |
      | named/readme.md | # lower\n |
      | named/Readme.MD | # mixed\n |
      | named/notes.txt | not a task\n |
      | named/018-bad.md | # no block\n |
      | named/nested/019-bad.md | +++\ndepends = []\n |
    When I run `marestail tasks check named`
    Then the exit code is 1
    And stdout is exactly "named/018-bad.md: no front matter: line 1 must be +++\n"
    And stderr is empty
    When I run `marestail tasks check named/README.md`
    Then the exit code is 1
    And stdout is exactly "named/README.md: no front matter: line 1 must be +++\n"
    When I run `marestail tasks check named/readme.md`
    Then the exit code is 1
    And stdout is exactly "named/readme.md: no front matter: line 1 must be +++\n"
    When I run `marestail tasks check named/notes.txt`
    Then the exit code is 1
    And stdout is exactly "named/notes.txt: not a .md file\n"
    When I run `marestail tasks check named/nested/019-bad.md`
    Then the exit code is 1
    And stdout is exactly "named/nested/019-bad.md: front matter is not closed: no +++ line after line 1\n"
    When I run `marestail tasks check missing_dir/`
    Then the exit code is 1
    And stdout is exactly "missing_dir: no such file or folder\n"
    When I run `marestail tasks check` in an empty directory
    Then the exit code is 1
    And stdout is exactly "tasks: no such file or folder\n"

  Scenario: the same file is checked once, under the first spelling
    Given "once/019-bad.md" whose bytes are "# title\n"
    When I run `marestail tasks check once/sub/../019-bad.md once`
    Then the exit code is 1
    And stdout is exactly "once/sub/../019-bad.md: no front matter: line 1 must be +++\n"
    And stderr is empty

  Scenario Outline: one front-matter problem is one stdout line
    Given a directory whose only file is "tasks/019-test.md" with the bytes <bytes>
    When I run `marestail tasks check tasks/019-test.md`
    Then the exit code is 1
    And stdout is exactly "tasks/019-test.md: <problem>\n"
    And stderr is empty

    Examples:
      | bytes | problem |
      | # 019 title\n | no front matter: line 1 must be +++ |
      | \n+++\ndepends = []\n+++\n# x\n | no front matter: line 1 must be +++ |
      | +++ \ndepends = []\n+++\n# x\n | no front matter: line 1 must be +++ |
      | +++ \r\ndepends = []\r\n+++\r\n# x\n | no front matter: line 1 must be +++ |
      | +++\ndepends = ["018-a"]\nstack = true\n# no close\n | front matter is not closed: no +++ line after line 1 |
      | +++\ndepends = []\n+++ \n# x\n | front matter is not closed: no +++ line after line 1 |
      | +++\ndepends = [broken\n+++\n# x\n | front matter is not valid TOML: Invalid value (at line 1, column 12) |
      | +++\ndepends = []\n=\n+++\n# x\n | front matter is not valid TOML: Invalid statement (at line 2, column 1) |
      | +++\ndepends = "018-a"\n+++\n# x\n | depends must be an array of strings |
      | +++\ndepends = [1]\nstack = true\n+++\n# x\n | depends must be an array of strings |
      | +++\ndepends = [1, "018-a"]\nstack = true\n+++\n# x\n | depends must be an array of strings |
      | +++\ndepends = [""]\nstack = true\n+++\n# x\n | depends entry '' is not a task id; use the file name without .md |
      | +++\ndepends = ["sub/018-a"]\nstack = true\n+++\n# x\n | depends entry 'sub/018-a' is not a task id; use the file name without .md |
      | +++\ndepends = ["018-a.md"]\nstack = true\n+++\n# x\n | depends entry '018-a.md' is not a task id; use the file name without .md |
      | +++\ndepends = ["018-a", "018-a"]\nstack = false\n+++\n# x\n | depends lists 018-a twice |
      | +++\ndepends = ["019-test"]\nstack = true\n+++\n# x\n | depends on itself |
      | +++\ndepends = ["018-a"]\nstack = "yes"\n+++\n# x\n | stack must be true or false, got 'yes' |
      | +++\ndepends = ["018-a"]\nstack = 1\n+++\n# x\n | stack must be true or false, got 1 |
      | +++\ndepends = ["018-a"]\n+++\n# x\n | stack is required when depends is not empty |
      | +++\ndepends = []\nstack = false\n+++\n# x\n | stack is not allowed when depends is empty |
      | +++\ndepends = ["018-a", "018-b"]\nstack = true\n+++\n# x\n | stack = true needs exactly one dependency, got 2 |
      | +++\n+++\n# x\n | front matter has no depends; write depends = [] for a task with no dependencies |

  Scenario: an empty depends with stack true reports both rows, and a bad type skips rows that read depends
    Given "tasks/019-empty.md" whose bytes are:
      """
      +++
      depends = []
      stack = true
      +++
      # x
      """
    When I run `marestail tasks check tasks/019-empty.md`
    Then the exit code is 1
    And stdout is exactly:
      """
      tasks/019-empty.md: stack is not allowed when depends is empty
      tasks/019-empty.md: stack = true needs exactly one dependency, got 0
      """
    And stderr is empty
    Given "tasks/019-type.md" whose bytes are:
      """
      +++
      depends = "018-a"
      stack = "yes"
      +++
      # x
      """
    When I run `marestail tasks check tasks/019-type.md`
    Then the exit code is 1
    And stdout is exactly:
      """
      tasks/019-type.md: depends must be an array of strings
      tasks/019-type.md: stack must be true or false, got 'yes'
      """
    And stdout does not contain "stack is required"

  Scenario: a backslash in an id is not a task id
    Given "tasks/019-slash.md" has five LF-terminated lines: "+++", a depends line, "stack = true", "+++", and "# x"
    And the depends line equals depends = ["018, two bytes of value 92, a"] with nothing between 018 and a except those two bytes
    When I run `marestail tasks check tasks/019-slash.md`
    Then the exit code is 1
    And stdout is one line whose text is "tasks/019-slash.md: depends entry '018", then one 0x5C byte, then "a' is not a task id; use the file name without .md"
    And stderr is empty

  Scenario: several problems on one file stay in table order and invalid ids still count
    Given "tasks/019-test.md" whose bytes are:
      """
      +++
      depends = ["", "sub/018", "018-a", "018-a"]
      stack = true
      +++
      # x
      """
    When I run `marestail tasks check tasks/019-test.md`
    Then the exit code is 1
    And stdout is exactly:
      """
      tasks/019-test.md: depends entry '' is not a task id; use the file name without .md
      tasks/019-test.md: depends entry 'sub/018' is not a task id; use the file name without .md
      tasks/019-test.md: depends lists 018-a twice
      tasks/019-test.md: stack = true needs exactly one dependency, got 4
      """
    And stderr is empty

  Scenario: unknown keys are sorted, and a missing depends follows them
    Given "tasks/021-keys.md" whose bytes are:
      """
      +++
      zebra = 1
      alpha = 2
      +++
      # x
      """
    When I run `marestail tasks check tasks/021-keys.md`
    Then the exit code is 1
    And stdout is exactly:
      """
      tasks/021-keys.md: unknown front matter key 'alpha'; allowed keys are depends and stack
      tasks/021-keys.md: unknown front matter key 'zebra'; allowed keys are depends and stack
      tasks/021-keys.md: front matter has no depends; write depends = [] for a task with no dependencies
      """

  Scenario: an unknown key and a missing stack are both reported, and the missing file is not
    Given "tasks/021-extra.md" whose bytes are:
      """
      +++
      depends = ["018-a"]
      extra = 1
      +++
      # x
      """
    And "tasks/018-a.md" does not exist
    When I run `marestail tasks check tasks/021-extra.md`
    Then the exit code is 1
    And stdout is exactly:
      """
      tasks/021-extra.md: unknown front matter key 'extra'; allowed keys are depends and stack
      tasks/021-extra.md: stack is required when depends is not empty
      """

  Scenario: the published folder prints the stack line, then the typo, then the missing depends
    Given "tasks/020-run-events.md" whose bytes are:
      """
      +++
      depends = ["018-runs-stay-nice", "019-tasks"]
      stack = true
      +++
      # 020
      """
    And "tasks/021-status.md" whose bytes are:
      """
      +++
      depend = []
      +++
      # 021
      """
    When I run `marestail tasks check tasks`
    Then the exit code is 1
    And stdout is exactly:
      """
      tasks/020-run-events.md: stack = true needs exactly one dependency, got 2
      tasks/021-status.md: unknown front matter key 'depend'; allowed keys are depends and stack
      tasks/021-status.md: front matter has no depends; write depends = [] for a task with no dependencies
      """
    And stderr is empty

  Scenario: missing dependencies stay in depends order, and another folder does not satisfy them
    Given "ord/019-a.md" with `depends = ["099-z", "098-a"]` and `stack = false`
    When I run `marestail tasks check ord/019-a.md`
    Then stdout is exactly:
      """
      ord/019-a.md: depends on 099-z, but ord/099-z.md does not exist
      ord/019-a.md: depends on 098-a, but ord/098-a.md does not exist
      """
    Given "left/019-a.md" with `depends = ["020-b"]` and `stack = true`
    And "right/020-b.md" with `depends = []`
    When I run `marestail tasks check left right`
    Then stdout is exactly "left/019-a.md: depends on 020-b, but left/020-b.md does not exist\n"
    Given "left/sub/../019-a.md" is that same file, addressed with the `..` spelling, and its depends is changed to `["020-gone"]`
    When I run `marestail tasks check left/sub/../019-a.md`
    Then stdout is exactly "left/sub/../019-a.md: depends on 020-gone, but left/sub/../020-gone.md does not exist\n"

  Scenario: a dependency that exists on disk but was not checked is enough, and a cycle through it is not reported
    Given "box/018-prev.md" with `depends = ["019-next"]` and `stack = true`
    And "box/019-next.md" with `depends = ["018-prev"]` and `stack = true`
    When I run `marestail tasks check box/019-next.md`
    Then the exit code is 0
    And stdout is empty
    And stderr is empty
    When I run `marestail tasks check box`
    Then the exit code is 1
    And stdout is exactly "box/018-prev.md: dependency cycle: 018-prev -> 019-next -> 018-prev\n"

  Scenario: each later copy of an id is a duplicate, and the earliest path is the one named
    Given "alpha/019-dup.md", "beta/019-dup.md" and "mid/019-dup.md", each with `depends = []`
    When I run `marestail tasks check alpha beta mid`
    Then the exit code is 1
    And stdout is exactly:
      """
      beta/019-dup.md: duplicate task id 019-dup: also alpha/019-dup.md
      mid/019-dup.md: duplicate task id 019-dup: also alpha/019-dup.md
      """
    When I run `marestail tasks check mid alpha`
    Then the exit code is 1
    And stdout is exactly "mid/019-dup.md: duplicate task id 019-dup: also alpha/019-dup.md\n"

  Scenario: a cycle is one line on the smallest id, and a file that only depends on it is quiet
    Given "cycle/020-b.md" with `depends = ["021-c"]` and `stack = true`
    And "cycle/021-c.md" with `depends = ["019-a"]` and `stack = true`
    And "cycle/019-a.md" with `depends = ["020-b"]` and `stack = true`
    And "cycle/022-d.md" with `depends = ["021-c"]` and `stack = true`
    When I run `marestail tasks check cycle`
    Then the exit code is 1
    And stdout is exactly "cycle/019-a.md: dependency cycle: 019-a -> 020-b -> 021-c -> 019-a\n"
    Given "twin/019-a.md" with `depends = ["020-b", "021-c"]` and `stack = false`
    And "twin/020-b.md" with `depends = ["019-a"]` and `stack = true`
    And "twin/021-c.md" with `depends = ["019-a"]` and `stack = true`
    When I run `marestail tasks check twin`
    Then stdout is exactly:
      """
      twin/019-a.md: dependency cycle: 019-a -> 020-b -> 019-a
      twin/019-a.md: dependency cycle: 019-a -> 021-c -> 019-a
      """

  Scenario: set problems and a broken file in the same folders come out by path
    Given "alpha/018-bad.md" whose bytes are "+++\ndepends = []\n"
    And "alpha/019-a.md" with `depends = ["099-missing", "020-b"]` and `stack = false`
    And "alpha/020-b.md" with `depends = ["019-a"]` and `stack = true`
    And "alpha/022-d.md" with `depends = ["019-a"]` and `stack = true`
    And "alpha/023-on-bad.md" with `depends = ["018-bad"]` and `stack = true`
    And "beta/019-a.md" with `depends = []`
    When I run `marestail tasks check alpha beta`
    Then the exit code is 1
    And stdout is exactly:
      """
      alpha/018-bad.md: front matter is not closed: no +++ line after line 1
      alpha/019-a.md: depends on 099-missing, but alpha/099-missing.md does not exist
      alpha/019-a.md: dependency cycle: 019-a -> 020-b -> 019-a
      beta/019-a.md: duplicate task id 019-a: also alpha/019-a.md
      """
    And stdout does not contain "023-on-bad"
    And stderr is empty

  Scenario: tasks check is a command directly after run
    When I run `marestail --help`
    Then stdout contains "{gate,run,tasks,install,sonar,watch,perf,visual,route,graph,depth}"
    And stdout contains "check task files"
    When I run `marestail tasks --help`
    Then stdout contains "{check}"
    And stdout contains "check the front matter and dependencies of task files"
    When I run `marestail tasks check --help`
    Then stdout contains "usage: marestail tasks check [-h] [PATH ...]"
    And stdout contains "a folder of task files or a .md file (default: tasks/)"
    When I run `marestail tasks`
    Then the exit code is 2
    And stdout is empty
    And stderr is exactly:
      """
      usage: marestail tasks [-h] {check} ...
      marestail tasks: error: the following arguments are required: tasks_command
      """
    When I run `marestail run --help`
    Then the exit code is 0
    And stdout contains "--from" and "--model"
    And stdout does not contain "front matter"
    When I run `marestail gate --help`
    Then the exit code is 0
    And stdout contains "--tier"
    When I run `marestail watch --help`, `marestail perf --help`, `marestail visual --help`, `marestail install --help`, `marestail sonar --help`, `marestail graph --help` and `marestail depth --help`
    Then each exit code is 0
    When I run `marestail route --help` with MARESTAIL_DANDELION unset and PATH set to "/usr/bin:/bin"
    Then the exit code is 127
    And stderr contains "dandelion is not installed"

  Scenario: a broken block stops the run before an agent, after nice and before the model
    Given a git repo with `[git] base = "main"`, `[perf] enabled = false` and `[practices] enabled = false`, and a stub claude on MARESTAIL_CLAUDE that records its prompt and writes `VERDICT: PASS`
    And "tasks/020-run-events.md" has `depends = ["018-runs-stay-nice", "019-tasks"]` and `stack = true`
    When I run `marestail run tasks/020-run-events.md --from critic --to critic --auto --retries 1`
    Then the exit code is 1
    And stdout is empty
    And stderr is exactly "tasks/020-run-events.md: stack = true needs exactly one dependency, got 2\n"
    And the stub was not started
    And ".marestail/runs/020-run-events" does not exist
    Given "tasks/021-status.md" whose block is `depend = []` and nothing else
    When I run `marestail run tasks/021-status.md --from critic --to critic --auto --retries 1`
    Then the exit code is 1
    And stdout is empty
    And stderr is exactly:
      """
      tasks/021-status.md: unknown front matter key 'depend'; allowed keys are depends and stack
      tasks/021-status.md: front matter has no depends; write depends = [] for a task with no dependencies
      """
    And the stub was not started
    When MARESTAIL_NICE=high and I run the 020 command again
    Then the exit code is 1
    And stderr is exactly "nice must be an integer 0-19, got 'high'\n"
    And stderr does not contain "stack = true"
    When MARESTAIL_NICE is unset and I run the 020 command with `--model dandelion/route --agent grok`
    Then the exit code is 1
    And stderr is exactly "tasks/020-run-events.md: stack = true needs exactly one dependency, got 2\n"
    And stderr does not contain "drop --agent"
    When I run `marestail run tasks/missing.md --from critic --to critic --auto --retries 1`
    Then the exit code is 1
    And stdout is empty
    And stderr contains "FileNotFoundError" and "tasks/missing.md"
    And ".marestail/runs/missing" does not exist
    When I run that 020 command with the current directory "/tmp/qa-019-empty", which has no marestail.toml
    Then the exit code is 1
    And stderr is exactly "no marestail.toml found above /tmp/qa-019-empty\n"
    And stderr does not contain "front matter"

  Scenario: a blockless file and a valid block still run, and the run folder is named by the stem
    Given the same stub repo
    And "tasks/t.md" whose bytes are "# Add one\n"
    When I run `marestail run tasks/t.md --from critic --to critic --auto --retries 1`
    Then the exit code is 0
    And stdout contains "pipeline complete"
    And stderr is empty
    And the stub was started
    And the JSON field task in ".marestail/runs/t/timeline.json" is t
    And ".marestail/runs/t/timeline.md" exists
    And ".marestail/handoffs/t/" does not exist
    And ".marestail/runs/t/" has one directory whose name starts with "handoffs-" and that directory contains "01-critic.md"
    And ".marestail/runs/t/pipeline.log" does not exist
    And the critic prompt contains "# Task\n# Add one"
    Given "tasks/018-prev.md" whose bytes are "+++\n"
    And "tasks/019-demo.md" whose bytes are:
      """
      +++
      depends = ["018-prev"]
      stack = true
      +++
      # Something else

      Carry on.
      """
    When I run `marestail run tasks/019-demo.md --from critic --to critic --auto --retries 1`
    Then the exit code is 0
    And stdout contains "pipeline complete"
    And stderr is empty
    And the stub was started
    And the JSON field task in ".marestail/runs/019-demo/timeline.json" is 019-demo
    And ".marestail/handoffs/019-demo/" does not exist
    And ".marestail/runs/019-demo/" has one directory whose name starts with "handoffs-" and that directory contains "01-critic.md"
    And the critic prompt's text under "# Task" is:
      """
      This task depends on `018-prev`, which runs before it, so its work is already in the tree. Treat what it delivered as existing behaviour: build on it and keep it working.

      # Something else

      Carry on.
      """
    And the critic prompt does not contain "+++"
    And the critic prompt does not contain "depends ="
    And the critic prompt does not contain "stack"
    Given "tasks/019-absent.md" whose bytes are:
      """
      +++
      depends = ["099-missing"]
      stack = false
      +++
      # Absent
      """
    And "tasks/099-missing.md" does not exist
    When I run `marestail run tasks/019-absent.md --from critic --to critic --auto --retries 1`
    Then the exit code is 0
    And stdout contains "pipeline complete"
    And stdout does not contain "does not exist"
    And stderr is empty
    And the stub was started

  Scenario Outline: worker, judge and perf-author prompts share the task text and hide the block
    Given a task file with the bytes <file>
    When `worker_prompt`, `judge_prompt` and `perf_author_prompt` are built for that file
    Then the three prompts contain the same text under "# Task", keeping a heading inside that text and stopping before `# Specification`, `# Handoffs`, `# Finishing`, `# Authoring` or `# Verdict`, and that text is <text>
    And none of the three prompts contains "+++", "depends =" or "stack", except the row whose file is not a block
    And the three functions keep their current signatures
    And a file whose bytes are a space, "Do the thing.", a space and one LF still produces the task text "Do the thing."

    Examples:
      | file | text |
      | # Add one\n | # Add one |
      | +++ \ndepends = []\n+++\n# Later\n | +++ \ndepends = []\n+++\n# Later |
      | +++\ndepends = []\n+++\n# Title\n\nBody.\n | # Title\n\nBody. |
      | +++\ndepends = ["018-runs-stay-nice"]\nstack = true\n+++\n# Title\n\nBody.\n | This task depends on `018-runs-stay-nice`, which runs before it, so its work is already in the tree. Treat what it delivered as existing behaviour: build on it and keep it working.\n\n# Title\n\nBody. |
      | +++\ndepends = ["018-runs-stay-nice"]\nstack = false\n+++\n# Title\n\nBody.\n | This task depends on `018-runs-stay-nice`, which runs before it, so its work is already in the tree. Treat what it delivered as existing behaviour: build on it and keep it working.\n\n# Title\n\nBody. |
      | +++\ndepends = ["017-a", "018-b"]\nstack = false\n+++\n# Title\n\nBody.\n | This task depends on `017-a` and `018-b`, which run before it, so their work is already in the tree. Treat what they delivered as existing behaviour: build on it and keep it working.\n\n# Title\n\nBody. |
      | +++\ndepends = ["017-a", "018-b", "019-c"]\nstack = false\n+++\n# Title\n\nBody.\n | This task depends on `017-a`, `018-b` and `019-c`, which run before it, so their work is already in the tree. Treat what they delivered as existing behaviour: build on it and keep it working.\n\n# Title\n\nBody. |
      | +++\ndepends = ["017-a", "018-b", "019-c", "020-d"]\nstack = false\n+++\n# Title\n\nBody.\n | This task depends on `017-a`, `018-b`, `019-c` and `020-d`, which run before it, so their work is already in the tree. Treat what they delivered as existing behaviour: build on it and keep it working.\n\n# Title\n\nBody. |

  Scenario: the template, the README and the import contract grow, and this repo's tasks do not
    Then "templates/tasks-README.md" is unchanged up to its current last line and then ends with a blank line plus:
      """
      ## Dependencies

      A task that needs another task first says so in a block at the very top of the file, before the title:

          +++
          depends = ["018-runs-stay-nice"]
          stack = true
          +++

      `depends` lists task ids: file names in this folder without `.md`. Write `depends = []` for a task that needs nothing. `stack = true` runs the task on its one dependency's branch, after it; `stack = false` starts it on its own branch once every dependency is merged. Leave `stack` out when `depends` is empty. `marestail tasks check` reports every problem in this folder, one line each. `marestail run` works with or without the block.
      """
    And the "## Use" fence in "README.md" keeps its current lines and gains, immediately after the `dandelion/route-best` line, a line whose text is "marestail tasks check" plus ten spaces plus "# front matter and dependencies of every task in tasks/; or pass folders and .md files"
    And "## Writing tasks" gains this paragraph. This paragraph has no backticked token that contains a slash. The section's existing `tasks/README.md` backtick stays:
      """
      A task file may open with a `+++` TOML block holding `depends` and `stack`. `depends` lists task ids, the file names in that folder without the `.md` suffix, and `depends = []` means the task needs nothing. `stack = true` runs the task on its one dependency's branch, after it; `stack = false` starts the task on its own branch once every dependency is merged. Leave `stack` out when `depends` is empty. `marestail tasks check` checks a folder of task files. `marestail run` never requires the block. Roles see one sentence naming the dependencies, not the block.
      """
    When I run `marestail install` into an existing empty directory, with GROK_HOME pointed at a scratch directory
    Then "<target>/tasks/README.md" is byte-identical to "templates/tasks-README.md"
    When that installed file is replaced with "keep\n" and install is run again
    Then the file is still "keep\n"
    And "tasks/README.md" in this checkout has no "## Dependencies" heading
    And "tasks/018-runs-stay-nice.md" still begins "# 018 — runs stay nice"
    And "tasks/019-tasks-say-what-they-depend-on.md" still begins "# 019 — task files say what they depend on"
    And no file in this checkout's "tasks/" has a first line of `+++`
    And one line of ".importlinter" contains "marestail.report : marestail.config : marestail.changes : marestail._location" and "marestail.task_file"
    And the "foundations-import-nothing-above" source_modules list contains "marestail.task_file"
    And "marestail/task_file.py" imports only the modules tomllib, pathlib and dataclasses
