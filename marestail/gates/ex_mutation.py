from marestail.context import Context
from marestail.report import Result


def run_gate(ctx: Context) -> Result:
    return Result.skipped("ex.mutation", "no standard mutation tester for Elixir; see README")
