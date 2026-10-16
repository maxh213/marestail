# Writing tasks

One task is one pipeline run: specifier, critic, coder, cleaner, architect, practices, perf, hardener, QA. Write it so that run ends with something a user could do that they could not do before.

- **Slice vertically.** Name the user and the outcome: "a donor gives 50 zł by card through the new widget". Reach every layer that outcome needs and no wider. "Build the amounts component" is a layer, not a task.
- **Keep it thin.** One payment method, one variant, one route. The next slice adds the next one. If the task needs a list of sub-features, it is several tasks.
- **Say what must not change.** Existing routes, contracts, and behaviour the slice touches. The specifier pins these in scenarios.
- **Give the values.** Amounts, currencies, error messages, locales. Concrete values become concrete scenarios.
- **Put the rules in the gate, not the task.** Coverage, complexity, comments, and Sonar are already enforced. Mention a rule only when this task needs a different one.
- **Refactors are the exception.** A task that changes no behaviour says so in the first line and freezes everything; the critic will not demand a user-visible outcome.
- **Reproduce a bug first.** A bug task should carry two lines, and may add a third naming the element:

  ```
  where: /donate.html
  symptom: the teal bar's top-left corner is round and its top-right corner is square
  selector: #widget
  ```

  `where` is a route on the app that `[visual]` or `[qa] start` brings up, or a full URL; `symptom` is what a user sees. With both lines and `[visual]` enabled, the run captures `where` at the start commit before the specifier runs, and stops if it cannot. The specifier and the critic then check the task's stated cause against the capture, so describe what you see and treat the cause you suspect as a guess.
- **Order slices in a file** when several belong together, and run them one after another; the architect reshapes modules between them.

Name files `NNN-short-name.md`. The specifier writes `features/short-name.feature` and `qa/short-name.md` to match.
