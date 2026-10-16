+++
depends = ["017-architect-reaches-for-patterns-a-design-judge-checks"]
stack = false
+++
# 026 — Python gets a practices rulebook, and every rulebook asks for named conditions

After this task, `marestail install` copies `guidance/py.md` into any Python target, and the practices judge flags an `if` whose compound condition has no name, in Python, TypeScript and plain JavaScript. For example, `if (event.data === 'donated' || event.data?.status === 'donated')` becomes `if (userHasDonated(event))`.

## Source

main commit `b2e5138` built all of this. This task is a port: change no wording and no rule ids.

- `git show b2e5138:templates/guidance/py.md` becomes `templates/guidance/py.md`. PY-1…PY-52 come from the Python section of Max's design-patterns sheet (`.marestail/phases/sources/design-patterns-cheat-sheet.md`), with PY-36 "name the condition" added.
- `templates/guidance/ts.md` gets main's TS-49 "name the condition" as the last rule of `## Design & OOP`, and its header line now also covers plain `*.js`/`*.jsx` changes, leaving out the rules that are about types. Take both from `git show b2e5138:templates/guidance/ts.md`.
- Port main's install detection into green's `_install.py` beside `uses_csharp`: `uses_python` is true when `pyproject.toml`, `requirements.txt` or `setup.py` sits at the target root or at the target's `[python] root` from its `marestail.toml`. Detection checks files only. The `[python]` section alone does not count, because the template `marestail.toml` always has one.

If 017 has run, its `patterns/` rulebooks must not restate a PY or TS-49 rule. Where a pattern is already covered, they cite that rule's id. 017 said Python gets no pattern rulebook; that stays true.

## Scenarios

- A target with `server/requirements.txt` and `[python] root = "server"` gets `guidance/py.md`, byte-identical to the template.
- A target with a root `pyproject.toml` gets `guidance/py.md`.
- A target whose only Python sign is the template `marestail.toml` gets no `guidance/py.md`.
- An existing `guidance/py.md` is never overwritten.
- `py.md` has at least 40 rules, numbered PY-1 onwards with no gaps, is at most 250 lines, and has the headings `## GoF patterns, the Python way`, `## Python-native idioms`, `## Concurrency` and `## Web & architecture`.
- `ts.md` contains TS-49 and names `*.js` in its header.

main's `tools/test-practices.py` at `b2e5138` has these cases (`python_rulebook_content`, and the install cases named `install-*-python-guidance*`). Port them into green's test layout.
