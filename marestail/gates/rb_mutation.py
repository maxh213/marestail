from marestail.context import Context
from marestail.report import Result


def run_gate(ctx: Context) -> Result:
    return Result.skipped("rb.mutation", "no standard mutation tester wired for Ruby; mutant exists but is not in the gate")
