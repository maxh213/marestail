Feature: Hermes backend

  After this task, `marestail run tasks/x.md --agent hermes` runs every role on Nous
  Research's Hermes Agent CLI, and a `--model dandelion/route` run whose dandelion line is
  `x-ai/grok-4.6 xhigh hermes` runs that session on hermes stamped
  `[x-ai/grok-4.6 xhigh]`, instead of stopping with "dandelion route picked 'hermes',
  which marestail has no backend for".

  Background:
    Given the current working directory is the marestail-green repo root
    And the Python virtualenv at ".venv" is active and has the dev dependencies installed

  Scenario: hermes command is built with model, effort and prompt file
    Given a run with agent "hermes", model "x-ai/grok-4.6" and effort "xhigh"
    When the runner builds the hermes command
    Then it is [
      "hermes", "chat", "--query-file", "<prompt file>", "--oneshot", "-Q",
      "--format", "stream-json", "--yolo", "--accept-hooks", "--max-turns", "1000",
      "-m", "x-ai/grok-4.6", "--reasoning", "xhigh"
    ]

  Scenario Outline: hermes command omits optional flags but keeps the stamp
    Given a run with agent "hermes", model "<model>" and effort "<effort>"
    When the runner builds the hermes command
    Then it contains no "-m" argument if "<model>" is ""
    And it contains no "--reasoning" argument if "<effort>" is ""
    And the commit stamp is "<stamp>"

    Examples:
      | model           | effort | stamp                  |
      | x-ai/grok-4.6   | xhigh  | x-ai/grok-4.6 xhigh    |
      |                 | xhigh  | hermes xhigh           |
      | x-ai/grok-4.6   |        | x-ai/grok-4.6          |
      |                 |        | hermes                 |

  Scenario: MARESTAIL_HERMES overrides the hermes binary
    Given the environment variable "MARESTAIL_HERMES" is "/opt/hermes"
    And a run with agent "hermes"
    When the runner builds the hermes command
    Then the first element is "/opt/hermes"

  Scenario: hermes verified output is summarised and not treated as rate limited
    Given the verified hermes JSON output
    When the hermes outcome reader summarises it
    Then the summary line is "tokens=14980 \"pong\""
    And the hermes outcome reader reports not rate limited

  Scenario: hermes summary truncates long result text to 100 characters
    Given a hermes result with text:
      """
      word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word
      """
    And tokens total 7
    When the hermes outcome reader summarises it
    Then the summary line is:
      """
      tokens=7 "word word word word word word word word word word word word word word word word word word word word "
      """

  Scenario: hermes stderr is included on non-zero exit
    Given a hermes process exits 2 with stdout "" and stderr "subscription_expired"
    When the hermes outcome reader checks rate limit
    Then it reports rate limited

  Scenario Outline: hermes out-of-credit is rate limited
    Given a non-zero hermes exit with <where> containing "<text>"
    Then the hermes outcome reader reports rate limited

    Examples:
      | where        | text                                                                  |
      | result.error | Subscription credits are exhausted. Top up/renew credits, then retry. |
      | stderr       | insufficient_credits                                                  |
      | stderr       | no_usable_credits                                                     |
      | stderr       | subscription_expired                                                  |
      | stderr       | subscription_required                                                 |
      | stderr       | member_spend_cap_exceeded                                             |
      | stderr       | rate limit exceeded                                                   |

  Scenario Outline: hermes failures that are not rate limits
    Given a <code> hermes exit with <where> containing "<text>"
    Then the hermes outcome reader reports not rate limited

    Examples:
      | code | where        | text                            |
      | 2    | stderr       | Unknown --reasoning 'ultrahigh' |
      | 0    | result       | the quota gate passed           |
      | 1    | stdout       | plain text failure              |

  Scenario Outline: non-JSON hermes output falls back to its tail
    Given a hermes exit with code <code> and stdout "<stdout>"
    When the hermes outcome reader summarises it
    Then the summary is the last 200 characters of stdout with newlines collapsed to spaces
    And the hermes outcome reader reports <rate_limited>

    Examples:
      | code | stdout              | rate_limited     |
      | 1    | rate limit exceeded | rate limited     |
      | 0    | plain ok output     | not rate limited |

  Scenario: dandelion route can send a session to hermes
    Given "dandelion route" prints "x-ai/grok-4.6 xhigh hermes"
    When marestail parses that line
    Then the backend is "hermes", the model is "x-ai/grok-4.6", the effort is "xhigh" and there is no account env
    And the session would be stamped "[x-ai/grok-4.6 xhigh]"

  Scenario Outline: hermes is resolved from every usual source
    Given the backend is selected from "<source>"
    When the backend is resolved
    Then the resolved backend is "hermes"

    Examples:
      | source                     |
      | --agent hermes             |
      | MARESTAIL_AGENT=hermes     |
      | [agent] backend = "hermes" |

  Scenario: a verdict inside hermes result is parsed
    Given no verdict file exists but the hermes result contains "VERDICT: BOUNCE to coder"
    When the runner parses the verdict
    Then it returns ("BOUNCE", "coder")

  Scenario: the watch TUI recognises a running hermes worker
    Given a process command line "python -m hermes_cli.main chat --query-file /tmp/p --oneshot -Q --format stream-json --yolo --accept-hooks --max-turns 1000 -m x-ai/grok-4.6 --reasoning xhigh"
    When the watch TUI classifies the process
    Then the backend is "hermes"

  Scenario: hermes is listed in the backend registry
    When I inspect "marestail.backends.BACKENDS"
    Then it contains "hermes"
    When I inspect "marestail.route.BACKENDS"
    Then "hermes" maps to "hermes" with no account env
    When I inspect "templates/marestail.toml"
    Then the commented backend list includes "hermes"
    And tests/test_green_repo.py asserts that template line lists hermes

  Scenario: README documents hermes
    When I read "README.md"
    Then the environment variables table lists "MARESTAIL_HERMES"
    And the "marestail run" example lists "hermes" among backends
    And the "--effort" paragraph mentions "hermes" takes "--reasoning"
    And the "dandelion/route" paragraph mentions "hermes" runs its own CLI
    And there is a "Hermes pipeline runs" paragraph with the hermes command

  Scenario Outline: existing backend commands and outcome readers are unchanged
    Given a run with agent "<agent>", model "m" and effort "e"
    When the runner builds the agent command
    Then it equals the "<agent>" command asserted in tools/test-agent-backends.py
    And the "<agent>" outcome reader assertions in tools/test-agent-backends.py still pass

    Examples:
      | agent  |
      | claude |
      | agy    |
      | cursor |
      | grok   |
      | kilo   |
      | kimi   |
      | junie  |

  Scenario: the backend diagnostic scripts keep passing and cover hermes
    Given "MARESTAIL_DANDELION_SRC" points at a route-table fixture built from marestail's own backend names
    When I run each of the following directly with "python3":
      | script                       |
      | tools/test-agent-backends.py |
      | tools/test-route.py          |
    Then each script exits 0 and its last line of output contains "ok"
    And "tools/test-route.py" also prints a line matching "^dandelion source: \\d+ route lines parse$"
