Feature: Junie backend

  After this task, `marestail run tasks/x.md --agent junie` runs every role on the
  JetBrains Junie CLI, and a `--model dandelion/route` run whose dandelion line is
  `gemini-3.8-flash high junie` runs that session on junie stamped
  `[gemini-3.8-flash high]`, instead of stopping with "dandelion route picked 'junie',
  which marestail has no backend for".

  Background:
    Given the current working directory is the marestail-green repo root
    And the Python virtualenv at ".venv" is active and has the dev dependencies installed

  Scenario: junie command is built with model, effort and prompt on stdin
    Given a run with agent "junie", model "gemini-3.8-flash" and effort "high"
    When the runner builds the agent command
    Then it is [
      "junie", "--skip-update-check", "--input-format=json", "--output-format=json",
      "-p", "<repo root>", "--model=gemini-3.8-flash", "--effort=high"
    ]
    And the prompt is sent on stdin as one JSON object {"task": <prompt>}
    And a prompt containing quotes, backslashes and newlines round-trips through json.loads

  Scenario Outline: junie command omits optional flags but keeps the stamp
    Given a run with agent "junie", model "<model>" and effort "<effort>"
    When the runner builds the agent command
    Then it contains no "--model" argument if "<model>" is ""
    And it contains no "--effort" argument if "<effort>" is not one of low, medium, high
    And the commit stamp is "<stamp>"

    Examples:
      | model             | effort | stamp                    |
      | gemini-3.8-flash  | xhigh  | gemini-3.8-flash xhigh   |
      |                   | high   | junie high               |

  Scenario: MARESTAIL_JUNIE overrides the junie binary
    Given the environment variable "MARESTAIL_JUNIE" is "/opt/junie"
    And a run with agent "junie"
    When the runner builds the agent command
    Then the first element is "/opt/junie"

  Scenario: junie JSON output is summarised and not treated as rate limited
    Given the verified junie JSON output
    When the junie outcome reader summarises it
    Then the summary line is "calls=48 tokens=423279 cost=$0.08 \"### Summary - pong  ### Changes - No files were created or modified as requested.  ### Verification \""
    And the junie outcome reader reports not rate limited

  Scenario Outline: junie out-of-credit is rate limited
    Given a non-zero junie exit with <where> containing "<text>"
    Then the junie outcome reader reports rate limited

    Examples:
      | where  | text                                                                      |
      | stderr | Your balance is exhausted.                                                |
      | stdout | {"errors":[{"level":"ERROR","message":"InsufficientAccountBalance"}]}      |
      | stderr | insufficient balance                                                      |
      | stderr | rate limit exceeded                                                       |

  Scenario Outline: junie failures that are not rate limits
    Given a <code> junie exit with <where> containing "<text>"
    Then the junie outcome reader reports not rate limited

    Examples:
      | code | where  | text                                                   |
      | 1    | stderr | Junie failed with the message: Invalid model: no-such-model-xyz |
      | 0    | result | the quota gate passed                                  |

  Scenario Outline: non-JSON junie output falls back to its tail
    Given a junie exit with code <code> and stdout "<stdout>"
    When the junie outcome reader summarises it
    Then the summary is the last 200 characters of stdout with newlines collapsed to spaces
    And the junie outcome reader reports <rate_limited>

    Examples:
      | code | stdout              | rate_limited     |
      | 1    | rate limit exceeded | rate limited     |
      | 0    | plain ok output     | not rate limited |

  Scenario: dandelion route can send a session to junie
    Given "dandelion route" prints "gemini-3.8-flash high junie"
    When marestail parses that line
    Then the backend is "junie", the model is "gemini-3.8-flash", the effort is "high" and there is no account env
    And the session would be stamped "[gemini-3.8-flash high]"

  Scenario Outline: junie is resolved from every usual source
    Given the backend is selected from "<source>"
    When the backend is resolved
    Then the resolved backend is "junie"

    Examples:
      | source                     |
      | --agent junie              |
      | MARESTAIL_AGENT=junie      |
      | [agent] backend = "junie"  |

  Scenario: a verdict inside junie result is parsed
    Given no verdict file exists but the junie result contains "VERDICT: BOUNCE to coder"
    When the runner parses the verdict
    Then it returns ("BOUNCE", "coder")

  Scenario: junie is listed in the backend registry
    When I inspect "marestail/tui/collect.py"
    Then "BACKENDS" contains "junie"
    When I inspect "templates/marestail.toml"
    Then the commented backend list includes "junie"

  Scenario: README documents junie
    When I read "README.md"
    Then the environment variables table lists "MARESTAIL_JUNIE"
    And the "marestail run" example lists "junie" among backends
    And the "--effort" paragraph mentions "junie" takes low, medium or high
    And the "dandelion/route" paragraph mentions "junie" runs its own CLI
    And there is a "Junie pipeline runs" paragraph with the junie command and the stdin JSON

  Scenario Outline: existing backend commands and outcome readers are unchanged
    Given a run with agent "<agent>", model "m" and effort "e"
    When the runner builds the agent command
    Then it equals the known "<agent>" command
    And the outcome readers for "<agent>" are unchanged

    Examples:
      | agent  |
      | claude |
      | agy    |
      | cursor |
      | grok   |
      | kilo   |
      | kimi   |

  Scenario: the backend diagnostic scripts keep passing and cover junie
    Given "MARESTAIL_DANDELION_SRC" points at a route-table fixture built from marestail's own backend names
    When I run each of the following directly with "python3":
      | script                       |
      | tools/test-agent-backends.py |
      | tools/test-route.py          |
    Then each script exits 0 and its last line of output contains "ok"
    And "tools/test-route.py" also prints a line matching "^dandelion source: \\d+ route lines parse$"
