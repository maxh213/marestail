+++
depends = []
+++
# 035 — each worker is told which frozen files it may change

After this task, the Finishing section of a worker's prompt says which frozen files that role may change, from the rules the runner enforces. The specifier is told it may change `features/**` and `qa/**`. So when a hardener writes `VERDICT: BOUNCE specifier` over a defect in the feature file or the QA procedure, the specifier fixes it instead of looping.

Today `prompts.finishing()` gives every worker the same step: "Do not change gate configuration, the feature files, or the QA procedure; the runner reverts such changes." For some roles that is false. `freeze.ALLOWED` lets the specifier change `features/**` and `qa/**`, and the architect its layer files. A repo's `[freeze.allow]` replaces a role's list; eu-parosol-demo-gathering sets `qa = ["qa/**"]`. Meanwhile the hardener is told to use `VERDICT: BOUNCE specifier` "when the defect is in the feature file or the QA procedure rather than the code".

On 2026-10-16 and 17, eu-parosol's task 015 looped nine times, from 34-hardener to 52-hardener, for about 2.5 hours on gpt-6-astra. Every hardener bounced to the specifier over the same two prose fixes, in `qa/dashboard-and-export.md` and `qa/admin-content-and-settings.md`, and a ready patch sat in a handoff. Every specifier took the step literally, wrote a handoff and changed nothing. QA attempts 30 to 33 refused the same fix for the same reason. The run stopped on "hardener repeated the same findings twice". Earlier Claude specifiers in the same task did edit `qa/` and `features/`, so the outcome depended on how literally the model read one sentence.

## What changes

- `finishing()` takes the role's allowed patterns from the same place `freeze.frozen_paths` does: `ALLOWED` merged with `[freeze.allow]`, where a repo's list for a role replaces the default list. One helper in `freeze` returns that list, and `frozen_paths` and the prompt both call it.
- **A role with no allowed patterns** (coder, cleaner and the rest, by default) keeps today's step word for word.
- **A role with allowed patterns** gets this in place of the first sentence: `You may change <patterns>; the runner keeps those changes. Do not change any other gate configuration, feature file or QA procedure; the runner reverts such changes.` `<patterns>` is each pattern in backticks, in list order, joined with `, `. The `## Config change` sentence and the csproj note follow as today.
  - Specifier, default config: `` You may change `features/**`, `qa/**`; the runner keeps those changes. ``
  - qa, with `[freeze.allow] qa = ["qa/**"]`: `` You may change `qa/**`; the runner keeps those changes. ``
  - Specifier, with `[freeze.allow] specifier = ["features/**"]`: `` You may change `features/**`; … ``, and `qa/**` is not named.

## What must not change

- What the runner reverts. `frozen_paths` returns exactly what it returns today for every role and config.
- The hardener's verdict instructions, and every judge prompt.
- The step's position in the Finishing list and the numbering of the other steps.

## Tests

- Worker prompts for the specifier, the architect and the coder with the default config: the specifier and architect name their `ALLOWED` patterns, and the coder's step is today's text.
- The qa and specifier cases above, with `[freeze.allow]` set.
- For each role and each of those configs, a path matching each pattern the prompt names is not in `frozen_paths` for that role.

## Done when

The tests pass, and README's paragraph on the runner's checks after every worker says that each worker's prompt names the frozen files it may change.
