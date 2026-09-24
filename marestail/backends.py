import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from marestail.config import Config
from marestail.shell import clean


class AgentRun(Protocol):
    config: Config
    model: str | None
    effort: str | None
    account_env: dict[str, str]


AGY = "agy"
CURSOR = "cursor"
CLAUDE = "claude"
GROK = "grok"
KILO = "kilo"
KIMI = "kimi"
JUNIE = "junie"
HERMES = "hermes"
BACKENDS = frozenset({CLAUDE, AGY, CURSOR, GROK, KILO, KIMI, JUNIE, HERMES})

LIMIT_PATTERN = re.compile(
    r"rate.?limit|usage limit|session limit|resets \d|overloaded|capacity|too many requests|\b529\b|quota", re.IGNORECASE
)
GROK_ENV = {
    "GROK_MEMORY": "0",
    "GROK_ASK_USER_QUESTION": "0",
    "GROK_WORKFLOWS": "0",
    "GROK_CLAUDE_HOOKS_ENABLED": "0",
}
GROK_LIMIT_PATTERN = re.compile(
    r"rate.?limit|usage limit|overloaded|capacity|too many requests|\b529\b|\b503\b",
    re.IGNORECASE,
)
GROK_APPROVE_LOCK = re.compile(
    r"always-approve|always approve|bypassPermissions|yolo.{0,40}(disabled|locked|forbidden)|disable_bypass",
    re.I,
)
SPACE = " "
KILO_DEFAULT_MODEL = "kilo/stepfun/step-3.7-flash:free"
KILO_DEFAULT_VARIANT = "high"
JUNIE_SUMMARY_WIDTH = 100
JUNIE_LIMIT_PATTERN = re.compile(r"Your balance is exhausted|InsufficientAccountBalance|insufficient\s+balance", re.IGNORECASE)
HERMES_SUMMARY_WIDTH = 100
HERMES_LIMIT_PATTERN = re.compile(
    r"insufficient_credits|Subscription credits are exhausted|no_usable_credits|subscription_expired|subscription_required|member_spend_cap_exceeded",
    re.IGNORECASE,
)
AGENT_TIMEOUT = 4 * 3600
SUMMARY_WIDTH = 120
MODEL_FLAG = "--model"
EFFORT_FLAG = "--effort"
OUTPUT_FORMAT = "--output-format"
JSON_FORMAT = "json"
ERROR = "error"
TOTAL_COST = "total_cost_usd"
NUM_TURNS = "num_turns"
TOTAL_TOKENS = "total_tokens"
USAGE = "usage"
EMPTY = ""
PART = "part"
TEXT = "text"
TYPE_KEY = "type"
EMPTY_STDOUT = ""
TOKENS = "tokens"
CONTENT = "content"
COST_USD = "costUSD"
RESULT = "result"
MESSAGE = "message"
ROLE_KEY = "role"
STOP_REASON = "stopReason"
RESPONSE = "response"
GROK_LIMIT_KEYS = (MESSAGE, TEXT, TYPE_KEY, STOP_REASON)
INPUT_OUTPUT_TOKENS = ("inputTokens", "outputTokens")
KIMI_TOKENS = ("input_tokens", "output_tokens")
Event = dict[str, Any]
Spawned = subprocess.CompletedProcess[str] | tuple[int, str]


def optional_flag(flag: str, value: str | None) -> list[str]:
    return [flag, value] if value else []


def agy_command(state: AgentRun) -> list[str]:
    binary = os.environ.get("MARESTAIL_AGY", "agy")
    command = [binary, "--dangerously-skip-permissions", OUTPUT_FORMAT, JSON_FORMAT, "--print-timeout", "4h"]
    return command + optional_flag(MODEL_FLAG, state.model) + optional_flag(EFFORT_FLAG, state.effort)


def cursor_command(state: AgentRun) -> list[str]:
    binary = os.environ.get("MARESTAIL_CURSOR", "cursor-agent")
    command = [binary, "-p", OUTPUT_FORMAT, JSON_FORMAT, "--force", "--trust", "--sandbox", "disabled"]
    return command + optional_flag(MODEL_FLAG, state.model)


def claude_command(state: AgentRun) -> list[str]:
    command = [
        os.environ.get("MARESTAIL_CLAUDE", CLAUDE),
        "-p",
        "--permission-mode",
        "bypassPermissions",
        "--dangerously-skip-permissions",
        OUTPUT_FORMAT,
        JSON_FORMAT,
    ]
    return command + optional_flag(MODEL_FLAG, state.model) + optional_flag(EFFORT_FLAG, state.effort)


def spawn(command: list[str], state: AgentRun, env: Mapping[str, str], stdin: str, shown: str) -> Spawned:
    try:
        return subprocess.run(
            command,
            cwd=state.config.root,
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=AGENT_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as error:
        return 127, f"{command[0]}: not found ({error})"
    except subprocess.TimeoutExpired:
        return 124, f"{shown}: timed out after {AGENT_TIMEOUT}s"


def session_result(spawned: Spawned, text_of: Callable[[subprocess.CompletedProcess[str]], str]) -> tuple[int, str]:
    if isinstance(spawned, tuple):
        return spawned
    return spawned.returncode, clean(text_of(spawned))


def stdout_or_stderr(completed: subprocess.CompletedProcess[str]) -> str:
    blank = not (completed.stdout or EMPTY_STDOUT).strip()
    return completed.stderr if completed.returncode != 0 and blank else completed.stdout


def stdout_and_stderr(completed: subprocess.CompletedProcess[str]) -> str:
    return (completed.stdout + "\n" + completed.stderr).strip() if completed.returncode != 0 else completed.stdout


def grok_run(state: AgentRun, prompt_file: Path) -> tuple[int, str]:
    command = grok_command(state, prompt_file)
    return session_result(spawn(command, state, {**os.environ, **GROK_ENV}, "", SPACE.join(command)), stdout_or_stderr)


def grok_command(state: AgentRun, prompt_file: Path) -> list[str]:
    command = [
        os.environ.get("MARESTAIL_GROK", "grok"),
        "--prompt-file",
        str(prompt_file.resolve()),
        OUTPUT_FORMAT,
        JSON_FORMAT,
        "--always-approve",
        "--no-plan",
        "--trust",
    ]
    return command + optional_flag(MODEL_FLAG, state.model) + optional_flag("--reasoning-effort", grok_effort(state))


def grok_effort(state: AgentRun) -> str | None:
    return state.effort or os.environ.get("MARESTAIL_GROK_EFFORT")


def kilo_command(state: AgentRun) -> list[str]:
    model = state.model or KILO_DEFAULT_MODEL
    command = [
        os.environ.get("MARESTAIL_KILO", "kilo"),
        "run",
        "--auto",
        "--format",
        JSON_FORMAT,
        "--log-level",
        "ERROR",
        MODEL_FLAG,
        model,
    ]
    return command + optional_flag("--variant", kilo_variant(state))


def kilo_variant(state: AgentRun) -> str | None:
    variant = state.effort if state.effort is not None else os.environ.get("MARESTAIL_KILO_VARIANT")
    if variant == "":
        return None
    return default_variant(state) if variant is None else variant


def default_variant(state: AgentRun) -> str | None:
    return KILO_DEFAULT_VARIANT if (state.model or KILO_DEFAULT_MODEL) == KILO_DEFAULT_MODEL else None


def kilo_run(state: AgentRun, prompt: str) -> tuple[int, str]:
    command = kilo_command(state)
    return session_result(spawn(command, state, os.environ, prompt, SPACE.join(command)), stdout_or_stderr)


def json_object(text: str) -> Event | None:
    start = text.find("{")
    if start < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return object_or_none(data)


def object_or_none(data: object) -> Event | None:
    return data if isinstance(data, dict) else None


def json_events(output: str) -> list[Event]:
    events = [json_object(line.strip()) for line in output.splitlines()]
    return [event for event in events if event is not None]


kilo_events = json_events
kimi_events = json_events


def event_dict(event: Event, key: str) -> Event:
    value = event.get(key)
    return value if isinstance(value, dict) else {}


def event_error(event: Event, fallback: object) -> str:
    err = event.get(ERROR)
    return str(err.get(MESSAGE) if isinstance(err, dict) else err or fallback)


def output_tail(output: str) -> str:
    return output[-200:].replace("\n", " ")


def last_text(texts: list[str]) -> str:
    return texts[-1] if texts else ""


def limited_output(code: int, output: str, pattern: re.Pattern[str] = LIMIT_PATTERN) -> bool:
    return code != 0 and bool(pattern.search(output))


def cost_bit(cost: Any) -> str:
    try:
        return f"api-equivalent=${float(cost):.2f}"
    except (TypeError, ValueError):
        return f"cost={cost}"


def summary_line(counts: dict[str, Any], cost: Any, text: str) -> str:
    bits = [f"{name}={value}" for name, value in counts.items() if value is not None]
    if cost is not None:
        bits.append(cost_bit(cost))
    bits.append(repr(text))
    return SPACE.join(bits)


def kilo_rate_limited(code: int, output: str) -> bool:
    events = kilo_events(output)
    if not events:
        return limited_output(code, output)
    blob = SPACE.join(kilo_errors(events))
    return bool(blob) and bool(LIMIT_PATTERN.search(blob))


def kilo_text(event: Event) -> str:
    if event.get(TYPE_KEY) != TEXT:
        return ""
    return str(mapping_text(event_dict(event, PART), TEXT) or mapping_text(event, TEXT) or EMPTY).strip()


def mapping_text(data: Event, key: str) -> str:
    if key not in data:
        return EMPTY
    return str(data[key])


def kilo_texts(events: list[Event]) -> list[str]:
    return [text for text in map(kilo_text, events) if text]


def kilo_errors(events: list[Event]) -> list[str]:
    return [event_error(event, event) for event in events if event.get("type") == ERROR]


def kilo_error(events: list[Event]) -> str:
    return last_text([event_error(event, "")[:SUMMARY_WIDTH] for event in events if event.get("type") == ERROR])


def first_value(part: Event, keys: tuple[str, str], fallback: Any) -> Any:
    return next((part[key] for key in keys if part.get(key)), fallback)


def kilo_usage(events: list[Event]) -> tuple[Any, Any]:
    tokens = cost = None
    for part in (event_dict(event, "part") for event in events if event.get("type") == "step_finish"):
        tokens = first_value(part, (TOKENS, TOTAL_TOKENS), tokens)
        cost = first_value(part, ("cost", COST_USD), cost)
    return tokens, cost


def kilo_summary(output: str) -> str:
    events = kilo_events(output)
    if not events:
        return output_tail(output)
    tokens, cost = kilo_usage(events)
    text = (kilo_error(events) or last_text(kilo_texts(events)))[:SUMMARY_WIDTH]
    return summary_line({TOKENS: tokens}, cost, text)


def kimi_prompt(prompt_file: Path) -> str:
    return f"Your instructions are in {prompt_file.resolve()}. Read that whole file first, then follow it exactly."


def kimi_command(state: AgentRun, prompt_file: Path) -> list[str]:
    command = [
        os.environ.get("MARESTAIL_KIMI", "kimi"),
        "-p",
        kimi_prompt(prompt_file),
        OUTPUT_FORMAT,
        "stream-json",
    ]
    return command + optional_flag("-m", state.model)


def kimi_run(state: AgentRun, prompt_file: Path) -> tuple[int, str]:
    command = kimi_command(state, prompt_file)
    return session_result(spawn(command, state, os.environ, "", command[0]), stdout_and_stderr)


def is_assistant(event: Event) -> bool:
    return event.get("role") == "assistant" or event.get("type") == "assistant"


def string_texts(content: str) -> list[str]:
    return [content.strip()] if content.strip() else []


def part_texts(content: list[Any]) -> list[str]:
    return [str(part["text"]).strip() for part in content if isinstance(part, dict) and part.get("text")]


def content_texts(content: Any) -> list[str]:
    if isinstance(content, str):
        return string_texts(content)
    if isinstance(content, list):
        return part_texts(content)
    return []


def assistant_texts(event: Event) -> list[str]:
    contents = (event.get(CONTENT), event_dict(event, MESSAGE).get(CONTENT))
    return [text for content in contents for text in content_texts(content)]


def kimi_texts(events: list[Event]) -> list[str]:
    return [text for event in events if is_assistant(event) for text in assistant_texts(event)]


def error_typed(event: Event) -> bool:
    return ERROR in mapping_text(event, TYPE_KEY) or ERROR in mapping_text(event, ROLE_KEY)


def error_event_text(event: Event) -> str | None:
    if error_typed(event):
        return str(event.get(MESSAGE) or event.get(CONTENT) or event)
    return None


def kimi_error(event: Event) -> str | None:
    err = event.get(ERROR)
    if isinstance(err, dict):
        return str(err.get(MESSAGE) or err)
    if err:
        return str(err)
    return error_event_text(event)


def kimi_errors(events: list[Event]) -> list[str]:
    return [error for error in map(kimi_error, events) if error is not None]


def kimi_rate_limited(code: int, output: str) -> bool:
    errors = kimi_errors(kimi_events(output))
    if errors:
        return bool(LIMIT_PATTERN.search(SPACE.join(errors)))
    return limited_output(code, output)


def token_count(usage: Event, key: str) -> int:
    return int(usage.get(key) or 0)


def summed_tokens(usage: Event, keys: tuple[str, str]) -> int | None:
    if not any(key in usage for key in keys):
        return None
    return sum(token_count(usage, key) for key in keys)


def kimi_tokens(usage: Event, tokens: Any) -> Any:
    found = usage.get(TOTAL_TOKENS) or tokens
    return summed_tokens(usage, KIMI_TOKENS) if found is None else found


def kimi_usage(events: list[Event]) -> tuple[Any, Any, Any]:
    turns = tokens = cost = None
    for event in events:
        if NUM_TURNS in event:
            turns = event[NUM_TURNS]
        if TOTAL_COST in event:
            cost = event[TOTAL_COST]
        tokens = kimi_tokens(event_dict(event, USAGE), tokens)
    return turns, tokens, cost


def kimi_summary(output: str) -> str:
    events = kimi_events(output)
    if not events:
        return output_tail(output)
    turns, tokens, cost = kimi_usage(events)
    errors = kimi_errors(events)
    text = (errors[-1] if errors else last_text(kimi_texts(events)))[:SUMMARY_WIDTH]
    return summary_line({"turns": turns, TOKENS: tokens}, cost, text)


def junie_command(state: AgentRun) -> list[str]:
    binary = os.environ.get("MARESTAIL_JUNIE", JUNIE)
    command = [binary, "--skip-update-check", "--input-format=json", "--output-format=json", "-p", str(state.config.root)]
    if state.model:
        command.append(f"--model={state.model}")
    if state.effort in ("low", "medium", "high"):
        command.append(f"--effort={state.effort}")
    return command


def junie_run(state: AgentRun, prompt: str) -> tuple[int, str]:
    command = junie_command(state)
    return session_result(spawn(command, state, os.environ, json.dumps({"task": prompt}), command[0]), stdout_and_stderr)


def junie_usage(data: Any) -> tuple[int, int, float]:
    if not isinstance(data, dict):
        return 0, 0, 0.0
    calls = tokens = 0
    cost = 0.0
    for item in data.get("llmUsage", []):
        item_calls, item_tokens, item_cost = junie_item_usage(item)
        calls += item_calls
        tokens += item_tokens
        cost += item_cost
    return calls, tokens, cost


def junie_item_usage(item: Any) -> tuple[int, int, float]:
    if not isinstance(item, dict):
        return 0, 0, 0.0
    calls = int(item.get("calls") or 0)
    tokens = junie_item_tokens(item)
    cost = float(item.get("cost") or 0.0)
    return calls, tokens, cost


def junie_item_tokens(item: Event) -> int:
    return sum(int(item.get(key) or 0) for key in ("inputTokens", "cacheInputTokens", "outputTokens"))


def junie_summary(output: str) -> str:
    data = json_object(output)
    if data is None:
        return output_tail(output)
    calls, tokens, cost = junie_usage(data)
    text = str(data.get(RESULT) or "").replace("\n", " ")[:JUNIE_SUMMARY_WIDTH]
    return f"calls={calls} tokens={tokens} cost=${cost:.2f} {text!r}"


def junie_rate_limited(code: int, output: str) -> bool:
    if code == 0:
        return False
    if junie_limit_match(output):
        return True
    return junie_errors_limited(json_object(output))


def junie_limit_match(text: str) -> bool:
    return bool(JUNIE_LIMIT_PATTERN.search(text) or LIMIT_PATTERN.search(text))


def junie_errors_limited(data: Event | None) -> bool:
    if data is None:
        return False
    return any(junie_error_limited(error) for error in data.get("errors", []))


def junie_error_limited(error: Any) -> bool:
    return isinstance(error, dict) and junie_limit_match(str(error.get("message")))


hermes_events = json_events


def hermes_command(state: AgentRun, prompt_file: Path) -> list[str]:
    command = [
        os.environ.get("MARESTAIL_HERMES", HERMES),
        "chat",
        "--query-file",
        str(prompt_file.resolve()),
        "--oneshot",
        "-Q",
        "--format",
        "stream-json",
        "--yolo",
        "--accept-hooks",
        "--max-turns",
        "1000",
    ]
    return command + optional_flag("-m", state.model) + optional_flag("--reasoning", state.effort)


def hermes_run(state: AgentRun, prompt_file: Path) -> tuple[int, str]:
    command = hermes_command(state, prompt_file)
    return session_result(spawn(command, state, os.environ, "", command[0]), stdout_and_stderr)


def hermes_result(events: list[Event]) -> Event:
    for event in events:
        if event.get(TYPE_KEY) == RESULT:
            return event
    return {}


def hermes_limit_match(text: str) -> bool:
    return bool(HERMES_LIMIT_PATTERN.search(text) or LIMIT_PATTERN.search(text))


def hermes_rate_limited(code: int, output: str) -> bool:
    return code != 0 and hermes_limit_match(output)


def hermes_summary(output: str) -> str:
    events = hermes_events(output)
    result = hermes_result(events)
    if not result:
        return output_tail(output)
    tokens = event_dict(result, TOKENS).get("total")
    text = str(result.get("text") or "").replace("\n", " ")[:HERMES_SUMMARY_WIDTH]
    return f'tokens={tokens} "{text}"'


def grok_parse_json(output: str) -> Event | None:
    text = output.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return json_object(text)
    return data if isinstance(data, dict) else None


def grok_failed(data: Event, code: int) -> bool:
    return data.get("type") == ERROR or code != 0


def grok_limit_text(data: Event, output: str) -> bool:
    blob = SPACE.join(mapping_text(data, key) for key in GROK_LIMIT_KEYS)
    return bool(GROK_LIMIT_PATTERN.search(blob) or GROK_LIMIT_PATTERN.search(output))


def grok_rate_limited(code: int, output: str) -> bool:
    data = grok_parse_json(output)
    if data is None:
        return limited_output(code, output, GROK_LIMIT_PATTERN)
    return grok_failed(data, code) and grok_limit_text(data, output)


def grok_cost(data: Event) -> Any:
    cost = data.get(TOTAL_COST)
    if cost is not None:
        return cost
    parts = model_costs(data)
    return sum(parts) if parts else None


def model_costs(data: Event) -> list[Any]:
    rows = event_dict(data, "modelUsage").values()
    return [row[COST_USD] for row in rows if isinstance(row, dict) and row.get(COST_USD) is not None]


def token_info(tokens: Any) -> str:
    return f"tokens={tokens} " if tokens else ""


def grok_text(data: Event) -> str:
    return str(data.get("text") or data.get(MESSAGE) or "")[:SUMMARY_WIDTH]


def grok_summary(output: str) -> str:
    data = grok_parse_json(output)
    if data is None:
        return output_tail(output)
    text = grok_text(data)
    turns = data.get(NUM_TURNS, "?")
    cost = grok_cost(data)
    if cost is not None:
        return f"turns={turns} api-equivalent=${float(cost):.2f} {text!r}"
    tokens = event_dict(data, USAGE).get(TOTAL_TOKENS)
    return f"turns={turns} {token_info(tokens)}{text!r}".strip()


def grok_always_approve_locked(code: int, output: str) -> bool:
    if code == 0:
        return False
    data = grok_parse_json(output)
    blob = output if data is None else str(data.get(MESSAGE) or output)
    return bool(GROK_APPROVE_LOCK.search(blob))


def rate_limited(code: int, output: str) -> bool:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return limited_output(code, output)
    if "is_error" in data:
        return bool(data.get("is_error")) and bool(LIMIT_PATTERN.search(mapping_text(data, RESULT)))
    return response_limited(data, code)


def response_limited(data: Any, code: int) -> bool:
    error_text = mapping_text(data, RESPONSE) or mapping_text(data, ERROR)
    failed = data.get("status") == "ERROR" or code != 0
    return failed and bool(LIMIT_PATTERN.search(error_text))


def result_tokens(usage: Event) -> Any:
    found = usage.get(TOTAL_TOKENS)
    return summed_tokens(usage, INPUT_OUTPUT_TOKENS) if found is None else found


def is_result(data: Any, usage: Event) -> bool:
    return data.get("type") == RESULT or any(key in usage for key in INPUT_OUTPUT_TOKENS)


def result_summary(data: Any, usage: Event) -> str:
    text = repr(str(data.get(RESULT) or "")[:SUMMARY_WIDTH])
    tokens = result_tokens(usage)
    shown = "" if tokens is None else f"tokens={tokens} "
    return f"{shown}{text}".strip()


def turns_summary(data: Any, usage: Event) -> str:
    turns = data.get(NUM_TURNS, "?")
    text = repr(str(data.get(RESULT) or data.get("response") or "")[:SUMMARY_WIDTH])
    return f"turns={turns} {token_info(usage.get('total_tokens'))}{text}".strip()


def summary(output: str) -> str:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return output_tail(output)
    if TOTAL_COST in data:
        cost = data[TOTAL_COST]
        turns = data.get(NUM_TURNS)
        text = mapping_text(data, RESULT)[:SUMMARY_WIDTH]
        return f"turns={turns} api-equivalent=${cost:.2f} {text!r}"
    usage = event_dict(data, USAGE)
    if is_result(data, usage):
        return result_summary(data, usage)
    return turns_summary(data, usage)
