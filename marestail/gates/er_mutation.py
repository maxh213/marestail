from marestail.context import Context
from marestail.report import Result


def run_gate(ctx: Context) -> Result:
    return Result.skipped("er.mutation", "no mutation tester for Erlang; see README")
