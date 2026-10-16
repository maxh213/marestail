# 015 — for a bug, marestail looks at the real thing before anyone writes a spec

After this task, a bug task cannot be specified from its description alone. The run starts by capturing the symptom on the project's real app, and the specifier and the critic have to square the task's stated cause with what was captured.

This is the root of the otwarteklatki/next-boilerplate#537 failure. The task file said "Radius on an iframe does not clip Donorbox's nested document" and asked for the widget to "fill the donate column". Both were wrong. Radius on an iframe does clip: that is why the left corner was round. The right corner was square because Donorbox's form stops at 425px and sat left-aligned in a 440px frame, so filling the column was never possible. No role questioned either claim. Across 37 steps nobody fetched the page or the embed. The critic is told to check the spec against the task and the code, not against the page, and every later step built on the same belief, including the fixtures that were meant to test it.

Tasks 013 and 014 would now bounce that run at the end. This task stops it at the start, which is cheaper.

## What changes

**The task file may carry two lines,** and `tasks/README.md` says a bug task should:

```
where: /donate
symptom: the teal bar's top-left corner is round and its top-right corner is square
```

`where` is a route on the app that `[visual]` or `[qa] start` brings up, or a full URL. When a task has both lines and `[visual] enabled` is true:

- **Before the specifier runs,** the runner captures `where` at the start commit (013's capture, base tree only, whole viewport plus the largest element matching an optional `selector:` line) into `.marestail/runs/<task>/visual/reported/`.
- **The specifier's prompt gains a `# Reported` section:** the symptom, the picture paths, the geometry record, and the page's own markup around the selector (the outer HTML of the element and its ancestors up to `body`, with attributes, trimmed to 200 lines). For a third-party frame it also gets the frame's URL and the result of loading that URL alone at the frame's width: the box of the largest visible element inside it.
- **The specifier must write an `## Observed` section in its handoff:** what the capture shows, in its own words, and one line for each cause the task states, marked `holds` or `does not hold` with the measurement that says so. Where a stated cause does not hold, the specifier writes the spec from what it observed and says so at the top of the handoff.
- **The critic's prompt includes the same section and the specifier's `## Observed`.** `roles/critic.md` gains one sentence: bounce a spec whose cause is not supported by the capture, or whose scenarios model the page differently from the captured markup.
- A spec that models a layout must take it from the captured markup. `roles/specifier.md` gains one sentence saying so.
- If the capture fails (app will not start, route 404s), the run stops before the specifier with the reason. A bug cannot be reproduced by agreement.

With the specifier on a backend that cannot read images, it still gets the geometry and the markup, which is where the #537 facts were: a 440px frame, a 425px form inside it, and a `block-full` parent.

## What must not change

- Tasks with no `where` and `symptom` lines start exactly as they do today.
- Targets with no `[visual]` section.
- The specifier and critic prompts for every other task.
- Nothing captured is committed.

## Tests

`tools/test-reproduce-first.py`, stub agents, the static app from 013 with a framed page:

- a task with `where` and `symptom` captures before the specifier; the pictures exist before step 01 starts
- the specifier's prompt has the `# Reported` section with the symptom, picture paths, geometry, ancestor markup and the frame's inner box
- a specifier handoff with no `## Observed` section is rejected and retried, naming the missing section
- the critic's prompt carries `# Reported` and the specifier's `## Observed`
- a `where` that 404s stops the run before the specifier with the reason, and exits non-zero
- a task with no `where` line produces the prompts of today

## Done when

The new test passes, the other `tools/test-*.py` still pass, `tasks/README.md` and the install template for it describe `where` and `symptom`, and README's pipeline section says that bug tasks are reproduced first.
