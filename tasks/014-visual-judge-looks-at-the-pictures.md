# 014 — a judge looks at the before and after pictures and says whether the fix is visible

After this task, a run that was meant to change what a user sees cannot finish until a judge that can read images has looked at the real page before and after, and agreed that the symptom in the task is gone.

Task 013 catches a layout that moved. It cannot catch a fix that did nothing. On otwarteklatki/next-boilerplate#537 the task was "the top-right corner is square". After the change it was still square, inside a third-party frame no measurement can see into. One look at the after picture shows it. No role looked.

## What changes

**A new judge, `visual`, between the hardener and QA.** It runs only when the task has a `visual` block (013) and `[visual] enabled` is true. It is optional in the same way the practices and perf judges are.

- Before it runs, the runner makes a fresh capture of base and HEAD (013), so the pictures match the code being judged.
- Its prompt has a `# Visual` section: the block's `symptom` line, the paths of `element.png` and `viewport.png` for each tree and viewport, and the geometry differences from 013 as a short table.
- `roles/visual.md` is short:

> Look at the before and after pictures for each viewport. Say whether the after picture shows what the task asks for, and whether anything else a user would notice changed. PASS only when the symptom is gone in every viewport and nothing else got worse. When the symptom is still there, say what you see, and bounce to the coder. When the pictures show that the task's stated cause cannot be right, say why, and bounce to the specifier. Describe what is in the pictures; do not reason from the code.

- It may bounce to the coder or the specifier. It is not pinned, because a wrong diagnosis has to go back to the specifier.
- A failing `visual` gate (013) forces a bounce to the coder whatever the judge writes, the way a failing gate does for other judges.
- It writes nothing. Its verdict is an empty commit like the other judges'.

**Which model looks.** A new key:

```toml
[visual]
judge_model = "claude-fable-5-1"   # default
```

- The visual judge runs on the Claude backend with `judge_model`, whatever backend and model the rest of the run uses.
- If that session ends on a rate limit or a usage limit, the runner does **not** wait. It runs the judge again at once on the run's own backend and model, and prints `visual: claude-fable-5-1 is out of usage; judging with <backend> <model>`.
- If the run's own backend cannot read images, the judge is given the geometry table only, its prompt says the pictures could not be shown, and a PASS is recorded as `PASS (geometry only, pictures not seen)`. Task 005's ending then reads `pipeline complete, NOT verified by eye`, exit 3.
- Verdict commits are stamped with the model that actually judged.
- With `dandelion/route` in use, the visual judge still asks for `judge_model` first and falls back the same way.

## What must not change

- Runs with no `visual` block or no `[visual]` section.
- The other judges' models, prompts, bounce rules and repeat limits.
- The 10-minute rate-limit wait for every other role.
- `marestail watch` shows the new step like any other judge.

## Tests

`tools/test-visual-judge.py`, stub agents:

- with a `visual` block, the judge runs after the hardener and before QA; without one, it does not run
- its prompt contains the symptom, eight picture paths for two trees and two viewports, and the geometry table
- a stub verdict of `BOUNCE coder` returns to the coder, `BOUNCE specifier` to the specifier, PASS moves on to QA
- a failing `visual` gate forces `BOUNCE coder` over a stub PASS
- the judge is invoked with `claude` and `claude-fable-5-1` when the run uses another backend
- a stub rate-limit from that session reruns the judge immediately on the run's backend and prints the fallback line; no wait is recorded
- a run backend marked as unable to read images gets the geometry-only prompt, and the run ends `NOT verified by eye`, exit 3
- the verdict commit carries the model that judged

## Done when

The new test passes, the other `tools/test-*.py` still pass, `roles/visual.md` exists, and README's pipeline table shows the judge, with a paragraph on `judge_model` and the fallback.
