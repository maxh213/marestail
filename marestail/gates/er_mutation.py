from marestail.context import Context
from marestail.report import Result


def run_gate(ctx: Context) -> Result:
    reason = "no mutation tester for Erlang; see README"
    if ctx.scoped:
        reason = f"{reason} (scope: {ctx.scope_name})"
    return Result.skipped("er.mutation", reason)
