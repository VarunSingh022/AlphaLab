"""Every benchmark ceiling is judged by the stabilized method (ledger TST-011).

A benchmark's ceiling -- "the 4x workload may cost at most 6x the time" -- was
judged until v3.12 on one run of each workload with the cyclic collector on. That
method failed the v3.11 release gate in ``benchmark_institutional`` at 6.05x, on
code whose repeated runs read 3.0x to 5.2x (TST-010), and five more benchmarks
used it. Each now judges its ceiling through ``benchmarks/_stable_timing.py`` --
CPU time, the collector paused, the workloads interleaved, the fastest of three --
and keeps the collector-on run as the figure it reports.

These tests read the benchmarks, because the benchmarks run weekly rather than
on every push: a new ceiling judged the old way would otherwise be found the day
it fails.
"""

from __future__ import annotations

from pathlib import Path

BENCHMARKS = Path(__file__).resolve().parents[2] / "benchmarks"

#: Benchmarks whose only pass/fail line is an absolute budget on one run -- a
#: guard against a workload that cannot finish, set far above what it takes,
#: which noise cannot fail and the stabilized method would not change.
ABSOLUTE_BUDGET_ONLY = frozenset({"benchmark_risk_engine.py"})


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_every_benchmark_that_can_fail_judges_its_ceiling_by_the_stabilized_method() -> None:
    unjudged = [
        path.name
        for path in sorted(BENCHMARKS.glob("benchmark_*.py"))
        if "raise SystemExit" in _source(path)
        and path.name not in ABSOLUTE_BUDGET_ONLY
        and "from _stable_timing import" not in _source(path)
    ]

    assert unjudged == [], f"ceilings judged on a single run: {unjudged}"


def test_the_absolute_budgets_are_still_absolute_budgets_only() -> None:
    for name in ABSOLUTE_BUDGET_ONLY:
        source = _source(BENCHMARKS / name)

        assert source.count("raise SystemExit") == 1, name
        assert "BUDGET_SECONDS" in source, name


def test_no_benchmark_keeps_its_own_copy_of_the_method() -> None:
    """The collector is paused in one place, so the method cannot drift per file."""

    copies = [
        path.name
        for path in sorted(BENCHMARKS.glob("*.py"))
        if path.name != "_stable_timing.py" and "gc.disable()" in _source(path)
    ]

    assert copies == [], f"benchmarks pausing the collector themselves: {copies}"
