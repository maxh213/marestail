import time

from marestail import depth
from marestail.context import Context
from marestail.report import Result


def run_gate(ctx: Context) -> Result:
    started = time.time()
    modules = depth.analyse(ctx.config)
    if ctx.scoped:
        modules = [m for m in modules if ctx.in_scope(m.path)]
    findings = [p for m in modules for p in m.pass_throughs + m.private_imports]
    shallow = sum(1 for m in modules if m.shallow)
    summary = f"{len(modules)} modules, {shallow} shallow, {len(findings)} rule breaks" if modules else "no modules"
    return Result("depth", not findings, summary, findings, time.time() - started)
