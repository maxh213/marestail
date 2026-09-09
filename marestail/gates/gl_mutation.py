from marestail.context import Context
from marestail.report import Result


def run_gate(ctx: Context) -> Result:
    return Result.skipped(
        "gl.mutation",
        "muex mutates Elixir/Erlang AST, not Gleam source; no Gleam mutation tester yet",
    )
