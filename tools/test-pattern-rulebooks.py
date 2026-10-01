#!/usr/bin/env python3
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marestail.freeze import ALLOWED, matches

ROOT = Path(__file__).resolve().parent.parent
GUIDANCE = ROOT / "templates" / "guidance"
PATTERNS = GUIDANCE / "patterns"
LANGUAGES = ("cs", "ts", "rb", "ex", "er", "go")
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
    "go": ("%w", "errors.Is", "log/slog", "-race", "godoc"),
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
    ("errors.Join", "1.20"),
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
    ("errors.AsType", "1.26"),
)
PORTED = (
    ("origin/main", "ts"),
    ("origin/main", "cs"),
    ("dfdb550", "er"),
    ("dfdb550", "ex"),
    ("dfdb550", "rb"),
)
LATER_RUBY = re.compile(r"\bRB-(?:49|5\d|6\d|7[0-3])\b")
RB_CITES = (
    (7, ("RB-4", "RB-12"), ("PaymentGateway",)),
    (20, ("RB-11",), ("last resort",)),
    (21, ("RB-9",), ("WeeklyReport",)),
    (22, ("RB-16",), ("Billing::SubscribeCustomer",)),
    (27, ("RB-8",), ("STI",)),
)
REQUIRED = (
    ("cs", 16, "trigger", ("hand-written",)),
    ("cs", 16, "form", ("IEnumerable", "yield return")),
    ("ts", 4, "trigger", ("hand-written",)),
    ("ts", 4, "form", ("Symbol.iterator",)),
    ("ts", 9, "trigger", ("the same construction sequence at 3 or more call sites",)),
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
    ("go", 27, "trigger", ("Add(1)", "go func", "Done", "1.25", "go.mod")),
    ("go", 27, "form", ("sync.WaitGroup.Go", "1.25", "go.mod")),
    ("go", 30, "trigger", ("channel",)),
    ("go", 30, "form", ("close", "ctx.Done()")),
    ("go", 32, "trigger", ("time.Ticker",)),
    ("go", 32, "form", ("Stop",)),
    ("go", 34, "trigger", ("r.Method",)),
    ("go", 34, "form", ("ServeMux", "1.22", "go.mod")),
)
EXACT = (
    ("ts", 9, "trigger", "the same construction sequence at 3 or more call sites."),
    (
        "go",
        27,
        "trigger",
        "the `Add(1)` / `go func` / `defer Done()` sequence, only when `go.mod`'s `go` line is at least 1.25.",
    ),
)
ER_CITED = (1, 2, 4, 6, 9, 10, 13, 14, 15, 16, 17, 18, 19, 20, 22, 28, 29, 30, 33)
EX_CITED = (9, 10, 11, 12)
REACT = (24, 25, 26, 27, 28, 29, 30, 31)
RAILS = (21, 22, 23, 24, 25, 26, 27)
GO_NEEDLES = (
    (1, ("`error` last", "handle it or return it", "%w", "errors.Is", "errors.As")),
    (2, ("`panic`", "programmer errors", "`recover`", "goroutine", "handler", "boundaries")),
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
    "the code this task touched",
    "only when its trigger is present, and only in the rule's form",
    "Never add a dependency",
    "## Proposals",
    "## Patterns",
    "- <rule id> <file:line>: <what changed>",
    "one line saying why",
    "A factory or wrapper that hides nothing is the shallow module the role already rejects.",
)
ARCHITECT_PARAGRAPH = (
    "Read `guidance/patterns/*.md` and apply a pattern to the code this task touched only when its trigger is present,"
    " and only in the rule's form. Never add a dependency. A pattern that needs one goes in the handoff under"
    " `## Proposals`. Under `## Patterns`, list each pattern you applied as `- <rule id> <file:line>: <what changed>`,"
    " and each trigger you saw but deliberately did not act on with one line saying why."
    " A factory or wrapper that hides nothing is the shallow module the role already rejects."
)
DESIGN = (
    "guidance/patterns/",
    "## Patterns",
    "## Pre-existing",
    "VERDICT: BOUNCE",
    "VERDICT: PASS",
    "TS-P9 src/order.ts:4",
    "CS-P16 src/Walk.cs:10",
    "GO-P27 src/pool.go:18",
    "- GO-P27 src/old.go:3: Add(1) / go func / defer Done()",
    "a silent `## Patterns` still passes",
    "omit that heading when there is nothing to list",
    "plain function",
    "TS-11",
    "TS-15",
    "not applied because go.mod says go 1.22",
    "release condition is part of its trigger",
    "Iterator rule's form is `IEnumerable<T>` and `yield return`",
)
TOML = (
    "[design]",
    "enabled = true",
    "the design judge runs between architect and practices unless false",
    "skips repos with no guidance/patterns/*.md",
)
TOML_ADDED = [
    "# [design]",
    "# enabled = true  # the design judge runs between architect and practices unless false; skips repos with no guidance/patterns/*.md",
]
README = (
    "<rule id> <file:line>",
    "bounces to the architect",
    "design: no pattern rulebooks; skipping",
    "a pattern applied with no trigger",
    "a form other than the rule's",
    "neither applied nor explained under `## Patterns`",
)
KEPT = (
    "the curated TypeScript/React/Next.js rulebook (rules numbered `TS-1`",
    "(`CS-1`) requires every test to follow Arrange, Act, Assert",
    "frozen (`guidance/**` in `freeze.SPEC`)",
)
PARAGRAPH = (
    "Pattern rulebooks live in `guidance/patterns/`",
    "The architect reads them",
    "`design` judge reads them and bounces the architect",
    "The `practices` judge does not read them",
    "`er.md` (any `*.erl`)",
    "`ex.md` (root `mix.exs`)",
    "`rb.md` (root `Gemfile`)",
    "`go.md` (root `go.mod`)",
    "patterns/ts.md",
    "`[design]` key `enabled` defaults to true",
    "a pattern applied with no trigger",
    "a form other than the rule's",
    "neither applied nor explained under `## Patterns`",
)
SIMPLE = "simple_one_for_one"
HYPER_ASSIGNMENTS = (
    ("ALL_ROLES", '["specifier", "critic", "coder", "cleaner", "architect", "design", "practices", "perf", "hardener", "qa"]'),
    ("HARD_PLAN", '["specify", "judge PASS", "code", "worker cleaner", "worker architect", "judge PASS", "worker qa"]'),
    (
        "TOML",
        "'[git]\\nbase = \"main\"\\n\\n[design]\\nenabled = false\\n\\n[practices]\\nenabled = false\\n\\n[perf]\\nenabled = false\\n'",
    ),
)
HYPER_README = (
    '["none", "none", "full", "—", "full", "—", "—", "—", "none", "full", "visual", "qa"]',
    "rows[8:11]",
    '["| perf", "| blast", "| hardener"]',
    'rows[9][:4], ["| blast", "judge", "—", "none"]',
)
VISUAL_UNKNOWN = "unknown role visual; choose from specifier, critic, coder, cleaner, architect, design, practices, perf, hardener, qa"


class Rule(NamedTuple):
    prefix: str
    number: int
    name: str
    fields: dict[str, str]
    text: str


class Book(NamedTuple):
    text: str
    rules: list[Rule]


Books = dict[str, Book]


def expect(name: str, got: object, wanted: object) -> None:
    if got != wanted:
        raise SystemExit(f"{name}: {got!r} != {wanted!r}")


def expect_true(name: str, value: object) -> None:
    if not value:
        raise SystemExit(f"{name}: {value!r}")


def token_re(token: str) -> re.Pattern[str]:
    return token_pattern(token, "/" in token, 0)


def banned_re(needle: str) -> re.Pattern[str]:
    return token_pattern(needle, " " in needle, re.I)


def token_pattern(token: str, literal: bool, flags: int) -> re.Pattern[str]:
    body = re.escape(token) if literal else bounded(token)
    return re.compile(body, flags)


def bounded(token: str) -> str:
    return rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])"


def fields(body: str) -> dict[str, str]:
    found = {}
    for name in FIELD_NAMES:
        match = re.search(rf"^\s*{re.escape(name)}:\s*(.*)$", body, re.M)
        if match:
            found[name] = match.group(1).strip()
    return found


def rule_blocks(text: str) -> list[Rule]:
    starts = list(HEADING.finditer(text))
    return [rule_at(text, starts, index) for index in range(len(starts))]


def rule_at(text: str, starts: list[re.Match[str]], index: int) -> Rule:
    found = starts[index]
    body = text[found.end() : rule_end(starts, index, len(text))]
    return Rule(found.group(1), int(found.group(2)), found.group(3), fields(body), found.group(0) + body)


def rule_end(starts: list[re.Match[str]], index: int, length: int) -> int:
    if index + 1 == len(starts):
        return length
    return starts[index + 1].start()


def load() -> Books:
    return {language: load_book(language) for language in LANGUAGES}


def load_book(language: str) -> Book:
    text = (PATTERNS / f"{language}.md").read_text()
    return Book(text, rule_blocks(text))


def rule_of(store: Books, language: str, number: int) -> Rule:
    return next(rule for rule in store[language].rules if rule.number == number)


def check_shape(language: str, found: list[Rule]) -> None:
    expect(f"{language}-ids", [rule.number for rule in found], list(range(1, len(found) + 1)))
    expect(f"{language}-names", [rule.name for rule in found], NAMES[language])
    for rule in found:
        check_rule_shape(language, rule)


def check_rule_shape(language: str, rule: Rule) -> None:
    expect(f"{language}-{rule.number}-prefix", rule.prefix, language.upper())
    expect(f"{language}-{rule.number}-fields", list(rule.fields), list(FIELD_NAMES))
    for field, value in rule.fields.items():
        expect_true(f"{language}-{rule.number}-{field}-nonempty", bool(value.strip()))


def check_files() -> None:
    names = sorted(f"{language}.md" for language in LANGUAGES)
    expect("pattern-files", sorted(path.name for path in PATTERNS.glob("*.md")), names)
    expect("no-python-rulebook", ((PATTERNS / "py.md").exists(), (GUIDANCE / "py.md").exists()), (False, False))


def check_canonical(language: str, text: str) -> None:
    expect(f"{language}-canonical", [line for line in text.splitlines() if line.strip()][-1], CANONICAL[language])


def check_banned(language: str, text: str) -> None:
    for needle in (*BANNED_ANY, *BANNED_FILE.get(language, ()), *DROPPED.get(language, ())):
        expect_true(f"{language}-banned-{needle}", not banned_re(needle).search(text))


def check_rule_libraries(language: str, rule: Rule) -> None:
    for token in LIBRARIES:
        expect_true(f"{language}-P{rule.number}-{token}", allows(token, rule.text))


def allows(token: str, text: str) -> bool:
    return token_re(token).search(text) is None or DEPENDENCY in text


def check_libraries(store: Books) -> None:
    for language, book in store.items():
        for rule in book.rules:
            check_rule_libraries(language, rule)


def check_releases(store: Books) -> None:
    for rule in store["go"].rules:
        require_releases(f"go-P{rule.number}", rule.text)
    require_practice_releases((GUIDANCE / "go.md").read_text())


def require_practice_releases(text: str) -> None:
    for number in re.findall(r"^- \*\*GO-(\d+) — ", text, re.M):
        require_releases(f"GO-{number}", go_rule(text, int(number)))


def require_releases(label: str, text: str) -> None:
    for token, version in RELEASES:
        if token_re(token).search(text):
            expect_true(f"{label}-{token}-release", names_release(text, version))


def names_release(text: str, version: str) -> bool:
    return version in text and "go.mod" in text


def check_required(store: Books) -> None:
    for language, number, name, needles in REQUIRED:
        value = rule_of(store, language, number).fields[name]
        for needle in needles:
            expect_true(f"{language}-P{number}-{name}-{needle}", needle in value)
    check_exact(store)


def check_exact(store: Books) -> None:
    for language, number, name, wanted in EXACT:
        got = rule_of(store, language, number).fields[name]
        expect(f"{language}-P{number}-{name}", got, wanted)


def check_citations(store: Books) -> None:
    for prefix, language, cited in (("ER", "er", ER_CITED), ("EX", "ex", EX_CITED)):
        text = store[language].text
        for number in cited:
            expect_true(f"{language}-cites-{prefix}-{number}", f"{prefix}-{number}" in text)
    expect_true("ex-P12-cites-EX-12", "EX-12" in rule_of(store, "ex", 12).text)


def check_ruby_cites(store: Books) -> None:
    for number, ids, banned in RB_CITES:
        text = rule_of(store, "rb", number).text
        expect_needles(f"rb-P{number}", text, ids)
        expect_absent(f"rb-P{number}", text, banned)


def expect_absent(label: str, text: str, needles: tuple[str, ...]) -> None:
    for needle in needles:
        expect_true(f"{label}-restates-{needle}", needle not in text)


def check_order(store: Books) -> None:
    for language, number, name in ORDER:
        expect(f"{language}-order-{number}", rule_of(store, language, number).name, name)


def check_flavour(store: Books) -> None:
    for language, numbers, phrase in (("ts", REACT, "React code"), ("rb", RAILS, "Rails app")):
        for number in numbers:
            text = rule_of(store, language, number).text
            expect_true(f"{language}-P{number}-{phrase}", phrase in text)


def check_shipped(store: Books) -> None:
    for language, number, name in (("ts", 9, "Factory"), ("cs", 16, "Iterator"), ("go", 27, "WaitGroup")):
        expect(f"shipped-{language}-{number}", rule_of(store, language, number).name, name)


def check_er_simple(store: Books) -> None:
    book = store["er"]
    hits = simple_rules(book.rules)
    expect_true("er-simple-present", bool(hits))
    expect_true("er-simple-only-rules", not simple_outside(book))
    expect_simple_refusals(hits)


def simple_rules(rules: list[Rule]) -> list[Rule]:
    return [rule for rule in rules if SIMPLE in rule.text]


def simple_outside(book: Book) -> bool:
    return book.text.count(SIMPLE) != sum(rule.text.count(SIMPLE) for rule in book.rules)


def expect_simple_refusals(rules: list[Rule]) -> None:
    for rule in rules:
        expect_true(f"er-P{rule.number}-simple", refuses_simple(rule.text))


def refuses_simple(text: str) -> bool:
    return "not allowed" in text and "ER-18" in text


def go_rule(text: str, number: int) -> str:
    found = re.search(rf"^- \*\*GO-{number} — .*?(?=^- \*\*GO-|\Z)", text, re.M | re.S)
    if found is None:
        raise SystemExit(f"go-rule-{number}-missing")
    return found.group(0)


def check_go_practices() -> None:
    text = (GUIDANCE / "go.md").read_text()
    expect("go-heading", first_content_line(text), "# Go best practices")
    expect("go-ids", re.findall(r"^- \*\*GO-(\d+) — ", text, re.M), [str(number) for number in range(1, 11)])
    expect_go_shape(text)
    expect_go_needles(text)


def first_content_line(text: str) -> str:
    return next(line for line in text.splitlines() if line.strip())


def expect_go_shape(text: str) -> None:
    expect_true("go-judge", contains_all(text, ("practices judge", "*.go")))
    expect_true("go-no-doc", contains_none(text, ("doc comment", "godoc")))
    expect_true("go-no-pattern-terms", contains_none(text, ("WaitGroup.Go", "iter.Seq", "Ticker", "tickers")))
    expect_true("go-4-no-ticker", re.search("ticker", go_rule(text, 4), re.I) is None)


def contains_all(text: str, needles: tuple[str, ...]) -> bool:
    return all(needle in text for needle in needles)


def contains_none(text: str, needles: tuple[str, ...]) -> bool:
    return all(needle not in text for needle in needles)


def expect_go_needles(text: str) -> None:
    for number, needles in GO_NEEDLES:
        expect_rule_needles(go_rule(text, number), number, needles)


def expect_rule_needles(rule: str, number: int, needles: tuple[str, ...]) -> None:
    for needle in needles:
        expect_true(f"go-{number}-{needle}", needle in rule)


def check_roles() -> None:
    expect_needles("architect", (ROOT / "roles" / "architect.md").read_text(), ARCHITECT)
    expect_needles("design", (ROOT / "roles" / "design.md").read_text(), DESIGN)
    expect_needles("toml", (ROOT / "templates" / "marestail.toml").read_text(), TOML)
    check_architect_diff()
    check_toml_diff()


def check_architect_diff() -> None:
    removed, added = git_diff_lines("roles/architect.md")
    expect("architect-no-deletions", removed, [])
    expect("architect-added-paragraph", [line for line in added if line.strip()], [ARCHITECT_PARAGRAPH])


def check_toml_diff() -> None:
    removed, added = git_diff_lines("templates/marestail.toml")
    expect("toml-no-deletions", removed, [])
    expect("toml-added-block", [line.strip() for line in added if line.strip()], TOML_ADDED)


def git_diff_lines(path: str) -> tuple[list[str], list[str]]:
    completed = subprocess.run(
        ["git", "diff", "a2f6786", "--", path],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    diff = completed.stdout
    removed = [line[1:] for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")]
    added = [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]
    return removed, added


def expect_needles(label: str, text: str, needles: tuple[str, ...]) -> None:
    for needle in needles:
        expect_true(f"{label}-{needle}", needle in text)


def table_rows(text: str) -> list[list[str]]:
    return [table_cells(line) for line in text.splitlines() if line.startswith("| ")]


def table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip("|").split("|")]


def check_readme() -> None:
    text = (ROOT / "README.md").read_text()
    rows = table_rows(text)
    index = step_index(rows, "design")
    expect("readme-order", neighbor_steps(rows, index), ["architect", "design", "practices"])
    expect("readme-cells", rows[index][1:4], ["judge", "none", "—"])
    expect_needles("readme", rows[index][4], README)
    expect_true("readme-old-sentence", "Other languages get no shipped rulebook" not in text)
    expect_needles("readme-kept", best_practices(text), KEPT)
    expect_needles("readme-paragraph", pattern_paragraph(text), PARAGRAPH)


def step_index(rows: list[list[str]], name: str) -> int:
    return [row[0] for row in rows].index(name)


def neighbor_steps(rows: list[list[str]], index: int) -> list[str]:
    return [row[0] for row in rows][index - 1 : index + 2]


def best_practices(text: str) -> str:
    start = text.index("## Best practices")
    return text[start : text.index("\n## ", start + 1)]


def pattern_paragraph(text: str) -> str:
    return text[text.index("Pattern rulebooks live in") :].split("\n\n", 1)[0]


def check_task_readmes() -> None:
    template = lines_of(ROOT / "templates" / "tasks-README.md")
    shipped = pipeline_line(template)
    expect_true("task-readmes-design", "architect, design, practices" in shipped)
    expect("task-readmes-frozen", lines_of(ROOT / "tasks" / "README.md"), without_design(template, shipped))
    expect_true("task-readme-not-allowed", not readme_allowed())


def pipeline_line(lines: list[str]) -> str:
    return next(line for line in lines if "specifier, critic, coder" in line)


def readme_allowed() -> bool:
    return any(matches("tasks/README.md", pattern) for group in ALLOWED.values() for pattern in group)


def lines_of(path: Path) -> list[str]:
    return path.read_text().splitlines()


def without_design(lines: list[str], shipped: str) -> list[str]:
    frozen = shipped.replace("architect, design, practices", "architect, practices")
    index = lines.index("## Dependencies")
    end = index - 1 if lines[index - 1] == "" else index
    return [frozen if line == shipped else line for line in lines[:end]]


def check_script_pins() -> None:
    hyper = script_text("test-run-hyper.py")
    expect_assignments(hyper, HYPER_ASSIGNMENTS)
    expect_needles("hyper-readme", hyper, HYPER_README)
    expect_true("visual-unknown-role", VISUAL_UNKNOWN in script_text("test-visual-judge.py"))


def script_text(name: str) -> str:
    return (ROOT / "tools" / name).read_text()


def expect_assignments(text: str, pairs: tuple[tuple[str, str], ...]) -> None:
    for name, wanted in pairs:
        expect(f"pin-{name}", assigned(text, name), wanted)


def assigned(text: str, name: str) -> str:
    line = next(line for line in text.splitlines() if line.startswith(f"{name} = "))
    return line.split("=", 1)[1].strip()


def check_ported() -> None:
    for rev, name in PORTED:
        expect_blob(rev, name)
    expect_true("rb-cited-ids", ruby_ids_known())
    expect_true("no-rb-49", not later_ruby())


def expect_blob(rev: str, name: str) -> None:
    if (GUIDANCE / f"{name}.md").read_text() != git_show(rev, name):
        raise SystemExit(f"{name}-blob: differs from {rev}:templates/guidance/{name}.md")


def git_show(rev: str, name: str) -> str:
    completed = subprocess.run(
        ["git", "show", f"{rev}:templates/guidance/{name}.md"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def ruby_ids_known() -> bool:
    present = set(re.findall(r"\bRB-\d+\b", (GUIDANCE / "rb.md").read_text()))
    cited = set(re.findall(r"\bRB-\d+\b", (PATTERNS / "rb.md").read_text()))
    return bool(cited) and cited <= present


def later_ruby() -> bool:
    return any(LATER_RUBY.search(path.read_text()) for path in GUIDANCE.rglob("*.md"))


def main() -> None:
    check_files()
    store = load()
    for language, book in store.items():
        check_shape(language, book.rules)
        check_canonical(language, book.text)
        check_banned(language, book.text)
    check_libraries(store)
    check_releases(store)
    check_required(store)
    check_citations(store)
    check_ruby_cites(store)
    check_order(store)
    check_flavour(store)
    check_shipped(store)
    check_er_simple(store)
    check_go_practices()
    check_roles()
    check_readme()
    check_task_readmes()
    check_ported()
    check_script_pins()
    print("patterns ok")


if __name__ == "__main__":
    main()
