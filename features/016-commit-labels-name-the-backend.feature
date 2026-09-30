Feature: Commit labels name the backend: [cursor/auto high], not [auto high]

  After this task, a run whose model string does not already identify its origin
  stamps commits [<backend>/<model> <effort>]: a cursor run on the auto model
  stamps [cursor/auto high], and a routed cursor session on K3 stamps
  [cursor/kimi-k3-max] instead of the bare [kimi-k3-max]. A label that already
  names its origin stays byte-identical. The rule lives in model_name in
  marestail/runner.py and ports main's commit 80ed792 into the rewritten runner
  so merging green into main cannot regress it.

  The rule: when the run's model is set, it is used unchanged when it contains
  "/" (a provider prefix, as kimi-code's and x-ai's ids carry) or when it
  contains the resolved backend string (claude-opus-5 names claude, grok-4.6
  names grok); otherwise the label's model part is <backend>/<model>. Every
  other path of model_name and effort_name is untouched.

  Background:
    Given a temporary git repo with marestail installed and stub agents
    And the task file is "tasks/t.md" (stem "t")
    And features/qa for t are pre-seeded so the coder can pass its gate

  Scenario: cursor run on the auto model stamps [cursor/auto high]
    When I run `marestail run tasks/t.md --from coder --to coder --auto --agent cursor --model auto --effort high`
    Then the exit code is 0
    And with `$S` the HEAD recorded before the run, every subject in `git log --format=%s $S..HEAD` starts with `[cursor/auto high] `
    And no commit subject starts with `[auto high] `
    And no commit subject starts with `[cursor/cursor/`

  Scenario: a routed cursor session on K3 stamps [cursor/kimi-k3-max]
    Given a dandelion stub that prints `kimi-k3-max cursor` to every route call
    When I run `marestail run tasks/t.md --from coder --to coder --auto --model dandelion/route`
    Then the exit code is 0
    And with `$S` the HEAD recorded before the run, every subject in `git log --format=%s $S..HEAD` starts with `[cursor/kimi-k3-max] `
    And the saved prompt `.marestail/runs/t/01-coder.prompt.md` contains `[cursor/kimi-k3-max] `
    And that saved prompt does not contain `[dandelion/route] `

  Scenario Outline: the stamp for every backend, model and effort combination
    When a run starts on agent "<backend>" with model "<model>" and effort "<effort>"
    Then the label stamped on every commit the run produces is exactly "<label>"

    An empty <effort> cell means no effort is set for the run.

    Examples: prefix added, the model names no provider and not the backend
      | backend | model                 | effort | label                          |
      | cursor  | auto                  | high   | cursor/auto high               |
      | cursor  | kimi-k3-max           |        | cursor/kimi-k3-max             |
      | agy     | gemini-3.8-flash-high | high   | agy/gemini-3.8-flash-high high |
      | junie   | gemini-3.8-flash      | high   | junie/gemini-3.8-flash high    |

    Examples: unchanged, the model contains the backend string
      | backend | model         | effort | label              |
      | claude  | claude-opus-5 | high   | claude-opus-5 high |
      | grok    | grok-4.6      | xhigh  | grok-4.6 xhigh     |

    Examples: unchanged, the model carries a provider prefix
      | backend | model                             | effort | label                             |
      | kimi    | kimi-code/kimi-for-coding-highspeed |        | kimi-code/kimi-for-coding-highspeed |
      | hermes  | x-ai/grok-4.6                     | xhigh  | x-ai/grok-4.6 xhigh               |

  Scenario: no-model labels stay as they are
    Then a run with `--model dandelion/route` still labels `dandelion/route` before dandelion answers
    And a model-less run still labels with the bare backend name (`claude`, `agy`, `cursor`, `grok`, `kimi`, `junie`, `hermes`)
    And a model-less kilo run still labels `kilo/stepfun/step-3.7-flash:free high`
    And a kilo run with `--model kilo/other` still labels `kilo/other`

  Scenario: the re-route relabel still works
    Then routed() still swaps `[<before>] ` for `[<after>] ` in the session prompt
    And state.labels still accumulates every routed label
    And stamped() still leaves a commit alone when its subject already starts with any earlier label
    And stamped() still stamps a subject whose bracket names no earlier label (`[WIP] x` becomes `[<label>] [WIP] x`)
    And `python3 tools/test-route.py` keeps its current contract: exit 0 with last line `route ok`, and its pinned labels `grok-4.6 xhigh` and `claude-opus-5 high` stay valid because both name their backend (its dandelion-source check is environment-dependent and already skips when MARESTAIL_DANDELION_SRC names no file)

  Scenario: commit layout is otherwise unchanged
    Then commit subjects, bodies and the `By <role>.` byline keep their current shape
    And verdict commits and handoff-folded commits keep their current subjects
    And the only difference in any commit subject is the `<backend>/` prefix on labels whose model named no origin

  Scenario: tools/test-agent-backends.py pins the new labels
    Then `python3 tools/test-agent-backends.py` exits 0 with last line `agent backends ok`
    And its updated label expectations are exactly: label-model-only `claude/mymodel`, label-with-effort `claude/mymodel high`, label-grok `grok/mymodel xhigh`, label-kimi `kimi/mymodel`, label-kimi-effort `kimi/mymodel high`, label-junie `junie/mymodel`, label-junie-effort `junie/mymodel high`, label-hermes `hermes/mymodel`, label-hermes-effort `hermes/mymodel xhigh`
    And a new pin label-cursor-auto expects `cursor/auto high`
    And label-no-model `claude`, label-kimi-no-model `kimi`, label-junie-no-model `junie`, label-hermes-no-model `hermes`, label-kilo-default `kilo/stepfun/step-3.7-flash:free high` and label-kilo-plain `kilo/other` are unchanged

  Scenario: pytest covers every label branch
    Then the pytest suite carries one test per row of the eight-row table above
    And tests cover each branch: prefix added, slash present, backend contained, model unset with a route, model unset without a route, kilo default
    And existing label expectations that asserted a bare model are updated to the prefixed form (`opus high` to `claude/opus high`, kilo `other` to `kilo/other`, hermes `mymodel` to `hermes/mymodel`, hermes `mymodel xhigh` to `hermes/mymodel xhigh`)
    And `python -m pytest tests -q` passes

  Scenario: README describes the stamp rule
    Then the README paragraph that describes the commit stamp says a run whose model names no origin is stamped `<backend>/<model>`, with `[cursor/auto high]` as the example
    And it still says a model-less run stamps the bare backend name and that no effort means the stamp is the model alone

  Scenario: other diagnostics keep their contracts
    Then every other tools/test-*.py script keeps its current pass/fail contract, including tools/test-perf.py exit 1 with last line `verdict-commit-files: '' != 'perf/bench_x.py'`
