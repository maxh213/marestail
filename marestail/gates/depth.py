import time

from marestail import depth
from marestail.context import Context
from marestail.report import Result, elapsed


def run_gate(ctx: Context) -> Result:
    started = time.time()
    modules = [module for module in depth.analyse(ctx.config) if ctx.in_scope(module.path)]
    findings = depth.rule_breaks(modules)
    return Result("depth", not findings, summary(modules, findings), findings, elapsed(started))


def summary(modules: list[depth.Module], findings: list[str]) -> str:
    if not modules:
        return "no modules"
    shallow = sum(1 for module in modules if module.shallow)
    return f"{len(modules)} modules, {shallow} shallow, {len(findings)} rule breaks"
