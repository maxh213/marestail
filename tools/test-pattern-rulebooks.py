#!/usr/bin/env python3
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
GUIDANCE = ROOT / "templates" / "guidance"
PATTERNS = GUIDANCE / "patterns"
LANGUAGES = ("cs", "ts", "rb", "ex", "er", "go")
PATTERN_FILES = ["cs.md", "er.md", "ex.md", "go.md", "rb.md", "ts.md"]
FIELD_NAMES = ("pattern", "trigger", "form", "not when")
HEADING = re.compile(r"^- \*\*([A-Z]{2})-P(\d+) — (.+?)\.\*\*$", re.M)
DEPENDENCY = "already a dependency"
NAMES = {
    "cs": [
        "Factory Method",
        "Abstract Factory",
        "Builder",
        "Prototype",
        "Singleton",
        "Adapter",
        "Bridge",
        "Composite",
        "Decorator",
        "Facade",
        "Flyweight",
        "Proxy",
        "Chain of Responsibility",
        "Command",
        "Interpreter",
        "Iterator",
        "Memento",
        "Observer",
        "State",
        "Strategy",
        "Template Method",
        "Visitor",
        "Dependency injection",
        "Options",
        "Repository",
        "Result",
        "Middleware",
        "Producer/consumer",
    ],
    "ts": [
        "Strategy",
        "Command",
        "Observer",
        "Iterator",
        "Decorator",
        "Adapter",
        "Facade",
        "Proxy",
        "Factory",
        "Builder",
        "Singleton",
        "Composite",
        "State",
        "Chain of Responsibility",
        "Visitor",
        "Memento",
        "Template Method",
        "Module",
        "Function composition",
        "Async coordination",
        "Result",
        "Dependency injection",
        "Immutability",
        "Custom hooks",
        "Compound components",
        "Provider",
        "Controlled and uncontrolled",
        "Reducer",
        "Render props",
        "Composition over configuration",
        "Client data caching",
        "Discriminated union",
        "Branded types",
        "Const assertion",
        "satisfies",
        "Template literal types",
        "Overloads",
        "Mapped types",
    ],
    "rb": [
        "Template Method",
        "Strategy",
        "Observer",
        "Composite",
        "Iterator",
        "Command",
        "Adapter",
        "Proxy",
        "Decorator",
        "Singleton",
        "Factory",
        "Builder",
        "Interpreter",
        "Block",
        "Mixin",
        "Delegation",
        "Null Object",
        "Internal DSL",
        "Guard clause",
        "Refinement",
        "Value object",
        "Service object",
        "Form object",
        "Query object",
        "Presenter",
        "Policy",
        "Concern",
    ],
    "ex": [
        "Process or module",
        "Async stream",
        "Unlinked task",
        "Linked task",
        "GenServer",
        "Agent",
        "DynamicSupervisor",
        "handle_continue",
        "Rescheduled tick",
        "gen_statem",
        "Pool",
        "Notify many",
        "Monitor",
        "Supervisor strategy",
        "Behaviour",
        "Protocol",
        "Functional core",
        "Token",
        "Stream",
        "Macro",
        "Runtime config",
        "Context",
        "Ecto.Multi",
        "Query object",
        "Plug",
        "Telemetry",
        "Memento",
    ],
    "er": ["gen_statem", "gen_event", "Application", "Error kernel", "Monitor", "Error class", "Iolist", "Send after"],
    "go": [
        "Factory",
        "Functional options",
        "Prototype",
        "Singleton",
        "Adapter",
        "Bridge",
        "Composite",
        "Decorator",
        "Facade",
        "Flyweight",
        "Proxy",
        "Chain of Responsibility",
        "Command",
        "Interpreter",
        "Iterator",
        "Mediator",
        "Memento",
        "Observer",
        "State",
        "Strategy",
        "Template Method",
        "Visitor",
        "Embedding",
        "Constructor injection",
        "Enum",
        "Bounded parallel work",
        "WaitGroup",
        "Mutex",
        "Confinement",
        "Producer/consumer",
        "Timeouts",
        "Ticker",
        "Synctest",
        "net/http handlers",
        "Handler struct",
        "HTTP status mapping",
        "Repository",
    ],
}
CANONICAL = {
    "cs": "Canonical: GoF (1994); Refactoring.Guru; Fowler's PoEAA.",
    "ts": "Canonical: Osmani, Learning JavaScript Design Patterns; patterns.dev; Vanderkam, Effective TypeScript.",
    "rb": "Canonical: Olsen, Design Patterns in Ruby; Metz, POODR; Helmkamp, 7 Patterns to Refactor Fat ActiveRecord Models.",
    "ex": "Canonical: Jurić, Elixir in Action; OTP Design Principles; Gospodinov, Concurrent Data Processing in Elixir.",
    "er": "Canonical: Armstrong's thesis; OTP Design Principles; Hébert, Learn You Some Erlang; Hébert, Erlang in Anger.",
    "go": "Canonical: Effective Go; Go Proverbs; Cheney, Practical Go; Harsanyi, 100 Go Mistakes.",
}
BANNED_ANY = ("singleflight", "graceful shutdown", "golang.org/x/sync", "state_functions", "doc comment")
BANNED_FILE = {
    "go": ("%w", "errors.Is", "log/slog", "-race"),
    "er": (
        "a case on a shape this module owns",
        "#mod_state{}",
        "heavy startup",
        "tightly coupled",
        "pg:get_members",
        "OpenTelemetry context",
        "ensure_all_started",
        "is_binary(Id)",
        "no swallowing a DB crash",
        "error threading is a finding",
        "no sys/debug",
        "pg2 is gone",
        "gen_fsm",
    ),
    "ex": ("holds a list you could pass", "LiveView already gives a process", "one named GenServer", "subscriber pid lists"),
}
DROPPED = {
    "cs": ("Mediator", "CQRS", "Specification", "Aggregates"),
    "ts": (
        "EventEmitter",
        "PubSub",
        "barrel",
        "presentational",
        "Server Component",
        "Mediator",
        "Bridge",
        "Flyweight",
        "Interpreter",
        "Prototype",
    ),
    "rb": ("Metaprogramming", "method_missing", "define_method", "Duck typing", "Convention over configuration"),
    "ex": ("shard", "ETS", "GenStage", "Broadway", "circuit", "cluster", "cast"),
    "er": ("Release", "distribution", "hot code", "Mnesia", "DETS", "ETS", "share nothing", "typespec", "Dialyzer"),
    "go": ("singleflight", "rate limit", "graceful", "GOMAXPROCS", "cmd/", "clean architecture"),
}
LIBRARIES = (
    "MediatR",
    "Polly",
    "XState",
    "Oban",
    "Broadway",
    "Scrutor",
    "Draper",
    "Stateless",
    "neverthrow",
    "errgroup",
    "cobra",
    "viper",
    "envconfig",
    "sqlc",
    "pgx",
    "participle",
    "gproc",
    "syn",
    "PropEr",
    "recon",
    "wire",
    "fx",
    "golang.org/x/time/rate",
)
RELEASES = (
    ("WithCancelCause", "1.20"),
    ("ServeMux", "1.22"),
    ("sync.OnceValue", "1.21"),
    ("sync.OnceFunc", "1.21"),
    ("slices.Clone", "1.21"),
    ("maps.Clone", "1.21"),
    ("context.AfterFunc", "1.21"),
    ("iter.Seq", "1.23"),
    ("unique", "1.23"),
    ("sync.WaitGroup.Go", "1.25"),
    ("testing/synctest", "1.25"),
)
REQUIRED = (
    ("cs", 16, "trigger", ("hand-written",)),
    ("cs", 16, "form", ("IEnumerable",)),
    ("ts", 4, "trigger", ("hand-written",)),
    ("ts", 4, "form", ("Symbol.iterator",)),
    ("ts", 9, "form", ("plain function", "TS-11", "TS-15")),
    ("ts", 11, "trigger", ("hand-written",)),
    ("ts", 11, "form", ("ES module",)),
    ("ts", 20, "form", ("Promise.all", "allSettled", "race", "AbortController")),
    ("ts", 21, "form", ("{ ok: true, value }", "neverthrow")),
    ("ex", 10, "trigger", ("status", "handle_")),
    ("ex", 10, "form", ("gen_statem",)),
    ("ex", 12, "trigger", ("pid",)),
    ("ex", 12, "form", ("Registry.dispatch", "Phoenix.PubSub")),
    ("ex", 27, "trigger", ("hand-written",)),
    ("ex", 27, "form", ("immutable",)),
    ("er", 1, "trigger", ("status", "handle_")),
    ("er", 1, "form", ("handle_event_function",)),
    ("er", 7, "trigger", ("<<Acc/binary, X/binary>>",)),
    ("er", 7, "form", ("iolist",)),
    ("er", 8, "trigger", ("timer:send_interval",)),
    ("er", 8, "form", ("erlang:send_after",)),
    ("go", 4, "form", ("sync.Once",)),
    ("go", 10, "form", ("sync.Pool",)),
    ("go", 15, "form", ("iter.Seq", "1.23", "go.mod")),
    ("go", 27, "trigger", ("Add(1)", "go func", "Done")),
    ("go", 27, "form", ("sync.WaitGroup.Go", "1.25", "go.mod")),
    ("go", 30, "trigger", ("channel",)),
    ("go", 30, "form", ("close", "ctx.Done()")),
    ("go", 32, "trigger", ("time.Ticker",)),
    ("go", 32, "form", ("Stop",)),
    ("go", 34, "trigger", ("r.Method",)),
    ("go", 34, "form", ("ServeMux", "1.22", "go.mod")),
)
ER_CITED = (1, 2, 4, 6, 9, 10, 13, 14, 15, 16, 17, 18, 19, 20, 22, 28, 29, 30, 33)
EX_CITED = (9, 10, 11, 12)
REACT = (24, 25, 26, 27, 28, 29, 30, 31)
RAILS = (21, 22, 23, 24, 25, 26, 27)
GO_NEEDLES = (
    (1, ("`error` last", "handle it or return it", "%w", "errors.Is", "errors.As")),
    (2, ("`panic`", "programmer errors", "`recover`", "boundaries")),
    (3, ("context.Context", "first parameter", "never stored in a struct", "request metadata only")),
    (4, ("goroutine", "known stop", "context", "wait for it")),
    (5, ("accept interfaces", "return structs", "consumer", "small")),
    (6, ("zero value",)),
    (7, ("table", "t.Run", "-race")),
    (8, ("log/slog", "explicitly", "1.21", "go.mod")),
    (9, ("init()",)),
    (10, ("generics", "containers", "algorithms")),
)
ORDER = (
    ("ts", 19, "Function composition"),
    ("ts", 20, "Async coordination"),
    ("ts", 21, "Result"),
    ("ts", 34, "Const assertion"),
    ("ts", 35, "satisfies"),
    ("ex", 11, "Pool"),
    ("ex", 12, "Notify many"),
    ("ex", 13, "Monitor"),
    ("go", 29, "Confinement"),
    ("go", 30, "Producer/consumer"),
    ("go", 31, "Timeouts"),
    ("go", 33, "Synctest"),
    ("go", 34, "net/http handlers"),
    ("go", 35, "Handler struct"),
)


ARCHITECT = (
    "Prefer a few deep modules over many shallow ones",
    "guidance/patterns/*.md",
    "## Proposals",
    "## Patterns",
    "A factory or wrapper that hides nothing is the shallow module the role already rejects.",
)
DESIGN = (
    "guidance/patterns/",
    "## Patterns",
    "## Pre-existing",
    "VERDICT: BOUNCE",
    "VERDICT: PASS",
    "src/order.ts:4",
    "src/Walk.cs:10",
    "src/pool.go:18",
    "src/old.go:3",
    "plain function",
    "TS-11",
    "TS-15",
    "not applied because go.mod says go 1.22",
    "release condition is part of its trigger",
    "Iterator rule's form is `IEnumerable<T>`",
)
TOML = (
    "[design]",
    "enabled = true",
    "the design judge runs between architect and practices unless false",
    "skips repos with no guidance/patterns/*.md",
)
README = ("<rule id> <file:line>", "bounces to the architect", "design: no pattern rulebooks; skipping")
PARAGRAPH = (
    "Pattern rulebooks live in `guidance/patterns/`",
    "The architect reads them",
    "`design` judge reads them and bounces the architect",
    "The `practices` judge does not read them",
    "ships `er.md`",
    "`ex.md`",
    "`rb.md`",
    "`go.md`",
    "`[design]` key `enabled` defaults to true",
)


def expect(name: str, got: object, wanted: object) -> None:
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def expect_true(name: str, value: object) -> None:
    if not value:
        raise SystemExit(f"{name}: {value!r}")


def token_re(token: str) -> re.Pattern[str]:
    if "/" in token:
        return re.compile(re.escape(token))
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])")


def banned_re(needle: str) -> re.Pattern[str]:
    if " " in needle:
        return re.compile(re.escape(needle), re.I)
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(needle)}(?![A-Za-z0-9_])", re.I)


def fields(body: str) -> dict[str, str]:
    return {field: match.group(1).strip() for field in FIELD_NAMES if (match := re.search(rf"^\s*{field}: (.+)$", body, re.M))}


def block_at(text: str, starts: list[re.Match[str]], index: int) -> dict[str, Any]:
    found = starts[index]
    end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
    body = text[found.end() : end]
    return {"id": int(found.group(2)), "name": found.group(3), "body": body, "fields": fields(body), "text": found.group(0) + body}


def rule_blocks(text: str) -> list[dict[str, Any]]:
    starts = list(HEADING.finditer(text))
    return [block_at(text, starts, index) for index in range(len(starts))]


def load() -> dict[str, tuple[str, list[dict[str, Any]]]]:
    return {
        language: ((PATTERNS / f"{language}.md").read_text(), rule_blocks((PATTERNS / f"{language}.md").read_text()))
        for language in LANGUAGES
    }


def rule_of(store: dict[str, tuple[str, list[dict[str, Any]]]], language: str, number: int) -> dict[str, Any]:
    return next(rule for rule in store[language][1] if rule["id"] == number)


def check_shape(language: str, found: list[dict[str, Any]]) -> None:
    expect(f"{language}-ids", [rule["id"] for rule in found], list(range(1, len(found) + 1)))
    expect(f"{language}-names", [rule["name"] for rule in found], NAMES[language])
    for rule in found:
        expect(f"{language}-{rule['id']}-fields", list(rule["fields"]), list(FIELD_NAMES))


def check_files() -> None:
    expect("pattern-files", sorted(path.name for path in PATTERNS.glob("*.md")), PATTERN_FILES)
    expect("no-python-rulebook", ((PATTERNS / "py.md").exists(), (GUIDANCE / "py.md").exists()), (False, False))


def check_canonical(language: str, text: str) -> None:
    expect(f"{language}-canonical", [line for line in text.splitlines() if line.strip()][-1], CANONICAL[language])


def check_banned(language: str, text: str) -> None:
    for needle in (*BANNED_ANY, *BANNED_FILE.get(language, ()), *DROPPED.get(language, ())):
        expect_true(f"{language}-banned-{needle}", not banned_re(needle).search(text))


def check_rule_libraries(language: str, rule: dict[str, Any]) -> None:
    for token in LIBRARIES:
        found = token_re(token).search(str(rule["text"]))
        expect_true(f"{language}-P{rule['id']}-{token}", not found or DEPENDENCY in str(rule["text"]))


def check_libraries(store: dict[str, tuple[str, list[dict[str, Any]]]]) -> None:
    for language in store:
        for rule in store[language][1]:
            check_rule_libraries(language, rule)


def check_releases(store: dict[str, tuple[str, list[dict[str, Any]]]]) -> None:
    for rule in store["go"][1]:
        for token, version in RELEASES:
            if token_re(token).search(str(rule["text"])):
                expect_true(f"go-P{rule['id']}-{token}-release", version in str(rule["text"]) and "go.mod" in str(rule["text"]))


def check_required(store: dict[str, tuple[str, list[dict[str, Any]]]]) -> None:
    for language, number, name, needles in REQUIRED:
        value = str(rule_of(store, language, number)["fields"][name])
        for needle in needles:
            expect_true(f"{language}-P{number}-{name}-{needle}", needle in value)


def check_citations(store: dict[str, tuple[str, list[dict[str, Any]]]]) -> None:
    for prefix, language, cited in (("ER", "er", ER_CITED), ("EX", "ex", EX_CITED)):
        text = store[language][0]
        for number in cited:
            expect_true(f"{language}-cites-{prefix}-{number}", f"{prefix}-{number}" in text)


def check_order(store: dict[str, tuple[str, list[dict[str, Any]]]]) -> None:
    for language, number, name in ORDER:
        expect(f"{language}-order-{number}", rule_of(store, language, number)["name"], name)


def check_flavour(store: dict[str, tuple[str, list[dict[str, Any]]]]) -> None:
    for language, numbers, phrase in (("ts", REACT, "React code"), ("rb", RAILS, "Rails app")):
        for number in numbers:
            expect_true(f"{language}-P{number}-{phrase}", phrase in str(rule_of(store, language, number)["text"]))


def check_shipped(store: dict[str, tuple[str, list[dict[str, Any]]]]) -> None:
    for language, number, name in (("ts", 9, "Factory"), ("cs", 16, "Iterator"), ("go", 27, "WaitGroup")):
        expect(f"shipped-{language}-{number}", rule_of(store, language, number)["name"], name)


def check_er_simple(store: dict[str, tuple[str, list[dict[str, Any]]]]) -> None:
    for rule in store["er"][1]:
        if "simple_one_for_one" in str(rule["text"]):
            expect_true(f"er-P{rule['id']}-simple", "not allowed" in str(rule["text"]) and "ER-18" in str(rule["text"]))


def go_rule(text: str, number: int) -> str:
    found = re.search(rf"^- \*\*GO-{number} — .*?(?=^- \*\*GO-|\Z)", text, re.M | re.S)
    if found is None:
        raise SystemExit(f"go-rule-{number}-missing")
    return found.group(0)


def check_go_practices() -> None:
    text = (GUIDANCE / "go.md").read_text()
    lines = [line for line in text.splitlines() if line.strip()]
    expect("go-heading", lines[0], "# Go best practices")
    expect("go-ids", re.findall(r"^- \*\*GO-(\d+) — ", text, re.M), [str(number) for number in range(1, 11)])
    expect_true("go-judge", "practices judge" in text and "*.go" in text)
    expect_true("go-no-doc", "doc comment" not in text and "godoc" not in text)
    expect_true("go-no-pattern-terms", not any(token in text for token in ("WaitGroup.Go", "iter.Seq", "Ticker", "tickers")))
    expect_true("go-4-no-ticker", not re.search("ticker", go_rule(text, 4), re.I))
    for number, needles in GO_NEEDLES:
        for needle in needles:
            expect_true(f"go-{number}-{needle}", needle in go_rule(text, number))


def check_roles() -> None:
    architect = (ROOT / "roles" / "architect.md").read_text()
    for needle in ARCHITECT:
        expect_true(f"architect-{needle}", needle in architect)
    design = (ROOT / "roles" / "design.md").read_text()
    for needle in DESIGN:
        expect_true(f"design-{needle}", needle in design)
    toml = (ROOT / "templates" / "marestail.toml").read_text()
    for needle in TOML:
        expect_true(f"toml-{needle}", needle in toml)


def readme_rows() -> list[list[str]]:
    lines = [line for line in (ROOT / "README.md").read_text().splitlines() if line.startswith("| ")]
    return [[cell.strip() for cell in line.strip("|").split("|")] for line in lines]


def check_readme() -> None:
    rows = readme_rows()
    steps = [row[0] for row in rows]
    index = steps.index("design")
    expect("readme-order", steps[index - 1 : index + 2], ["architect", "design", "practices"])
    expect("readme-cells", rows[index][1:4], ["judge", "none", "—"])
    does = rows[index][4]
    for needle in README:
        expect_true(f"readme-{needle}", needle in does)
    text = (ROOT / "README.md").read_text()
    expect_true("readme-old-sentence", "Other languages get no shipped rulebook" not in text)
    paragraph = text[text.index("Pattern rulebooks live in") :].split("\n\n", 1)[0]
    for needle in PARAGRAPH:
        expect_true(f"readme-paragraph-{needle}", needle in paragraph)


def check_task_readmes() -> None:
    readme = (ROOT / "tasks" / "README.md").read_text()
    expect("task-readmes", readme, (ROOT / "templates" / "tasks-README.md").read_text())
    line = next(line for line in readme.splitlines() if "specifier, critic, coder" in line)
    expect_true("task-readmes-design", "architect, design, practices" in line)


def check_ported() -> None:
    ruby = (GUIDANCE / "rb.md").read_text()
    expect_true("rb-ends-at-RB-48", "RB-48" in ruby and "RB-49" not in ruby)


def main() -> None:
    check_files()
    store = load()
    for language, (text, found) in store.items():
        check_shape(language, found)
        check_canonical(language, text)
        check_banned(language, text)
    check_libraries(store)
    check_releases(store)
    check_required(store)
    check_citations(store)
    check_order(store)
    check_flavour(store)
    check_shipped(store)
    check_er_simple(store)
    check_go_practices()
    check_roles()
    check_readme()
    check_task_readmes()
    check_ported()
    print("patterns ok")


if __name__ == "__main__":
    main()
