"""What the spine costs against an earlier commit, for a run declaring nothing new (v4.3 M8).

WS-11 and WS-26 hold M8's execution additions to one percent of the spine for
a run that declares none of them, on PRF-006's spine benchmarks: a backtest and
its replay, the execution pipeline, the OMS and the portfolio engine. Each tree
runs its own copy of each benchmark's workload, and the copies must be the same
file.

Two trees cannot share one interpreter. So each round runs every workload of
both trees, each in a fresh interpreter (``python -I``, the tree first on its
path), one tree straight after the other, the order alternating from round to
round. Each interpreter runs its workload once to warm up, then reports the
fastest of three samples on the process CPU clock with the collector paused
(``benchmarks/_stable_timing.py``). The estimate is the median of the rounds'
paired ratios, PC-07's estimator: a slow phase covering a round scales both of
its figures alike. The portfolio engine, which M8 did not change, is the
control, and its figure is the measurement's resolution.

Both trees are extracted by ``git archive`` into a scratch directory, so the
index and every worktree are untouched. Run it with nothing else running: it
measures the machine as much as the code. Kept for provenance and re-runnable,
not run by the suite (it takes minutes)::

    python docs/audit/scripts/spine_overhead.py 1f749f3 [--head HEAD] [--rounds 15]
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import statistics
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]

#: The workloads: PRF-006's four benchmarks, the backtest's replay timed apart.
WORKLOADS = ("backtest", "replay", "pipeline", "oms", "portfolio")

#: What a workload is drawn from, which must be the same file in both trees.
SOURCES = (
    "benchmarks/_stable_timing.py",
    "benchmarks/benchmark_backtesting.py",
    "benchmarks/benchmark_execution_pipeline.py",
    "benchmarks/benchmark_oms.py",
    "benchmarks/benchmark_portfolio_engine.py",
)

#: Sizes giving each sample about half a second on the reference machine.
RECORDS = 4_000
ORDERS = 20_000
FILLS = 20_000

#: M8's budget for a run declaring none of its options (WS-11, WS-26).
BUDGET = 0.01


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _portfolio() -> object:
    """benchmark_portfolio_engine's loop, at ``FILLS`` fills: a buy, and a sell every other."""

    from decimal import Decimal

    from alphalab.portfolio import Account, PortfolioEngine, PortfolioState

    state = PortfolioState(account=Account("BENCH", "USD", "Perf Test", 0.0))
    state = PortfolioEngine.apply_deposit(state, Decimal("10000000.00"), "USD", 0.0)
    qty, price, commission = Decimal("1"), Decimal("100.00"), Decimal("0.50")
    for index in range(FILLS):
        state = PortfolioEngine.apply_fill(
            state, "AAPL", qty, price, commission, float(index), "USD"
        )
        if index % 2 == 0:
            state = PortfolioEngine.apply_fill(
                state, "AAPL", -qty, price, commission, float(index), "USD"
            )
    return state


def _workload(tree: Path, name: str) -> Callable[[], object]:
    """``name``'s workload, built from ``tree``'s own benchmarks."""

    benchmarks = tree / "benchmarks"
    if name in ("backtest", "replay"):
        module = _load("benchmark_backtesting", benchmarks / "benchmark_backtesting.py")
        backtest, replay = module._workloads(RECORDS)
        return backtest if name == "backtest" else replay  # type: ignore[no-any-return]
    if name == "pipeline":
        module = _load(
            "benchmark_execution_pipeline", benchmarks / "benchmark_execution_pipeline.py"
        )
        return lambda: module._run(RECORDS)
    if name == "oms":
        module = _load("benchmark_oms", benchmarks / "benchmark_oms.py")
        return lambda: module._full_workload(ORDERS, 1.0)
    assert name == "portfolio", name
    return _portfolio


def _child(tree: Path, name: str) -> None:
    """In a fresh interpreter: warm ``name`` up, then print its fastest CPU time as JSON."""

    sys.path[:0] = [str(tree), str(tree / "benchmarks")]
    import alphalab

    imported = Path(alphalab.__file__).resolve()
    assert imported.is_relative_to(tree.resolve()), f"alphalab came from {imported}"
    timing = _load("_stable_timing", tree / "benchmarks" / "_stable_timing.py")
    work = _workload(tree, name)
    work()
    (best,) = timing.fastest([work])
    print(json.dumps({"cpu": best, "alphalab": str(imported)}))


def _extract(ref: str, into: Path) -> Path:
    """``ref``'s package and benchmarks, by ``git archive``: no worktree, no index touched."""

    archive = subprocess.run(
        ["git", "-C", str(ROOT), "archive", "--format=tar", ref, "alphalab", "benchmarks"],
        check=True,
        capture_output=True,
    ).stdout
    into.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(into, filter="data")
    return into


def _measure(tree: Path, name: str) -> float:
    completed = subprocess.run(
        [sys.executable, "-I", str(Path(__file__).resolve()), "--child", str(tree), name],
        check=True,
        capture_output=True,
        text=True,
        cwd=tree,
    )
    return float(json.loads(completed.stdout.strip().splitlines()[-1])["cpu"])


def _commit(ref: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--short", f"{ref}^{{commit}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _quartiles(values: Sequence[float]) -> tuple[float, float]:
    if len(values) < 2:
        return values[0], values[0]
    low, _, high = statistics.quantiles(values, n=4, method="inclusive")
    return low, high


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else None)
    parser.add_argument("ref", nargs="?", help="the earlier commit, e.g. M7's close")
    parser.add_argument("--head", default="HEAD", help="the commit measured against it")
    parser.add_argument("--rounds", type=int, default=15)
    parser.add_argument("--json", type=Path, help="write every round's figures here")
    parser.add_argument("--child", nargs=2, metavar=("TREE", "WORKLOAD"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.child is not None:
        _child(Path(args.child[0]), args.child[1])
        return 0
    if args.ref is None:
        parser.error("name the earlier commit")
    before_commit, after_commit = _commit(args.ref), _commit(args.head)
    with tempfile.TemporaryDirectory(prefix="spine-overhead-") as scratch:
        before = _extract(before_commit, Path(scratch) / "before")
        after = _extract(after_commit, Path(scratch) / "after")
        for source in SOURCES:
            if (before / source).read_bytes() != (after / source).read_bytes():
                parser.error(f"{source} differs between the trees: the workloads would too")
        cpu: dict[str, dict[str, list[float]]] = {
            name: {"before": [], "after": []} for name in WORKLOADS
        }
        for round_ in range(args.rounds):
            order = (("before", before), ("after", after))
            for name in WORKLOADS:
                for label, tree in order if round_ % 2 == 0 else tuple(reversed(order)):
                    cpu[name][label].append(_measure(tree, name))
            print(f"  round {round_ + 1} of {args.rounds} done", flush=True)
    print(
        f"spine: {before_commit} -> {after_commit}, {args.rounds} rounds, the median of "
        "paired ratios (after over before), CPU time, the collector paused"
    )
    report: dict[str, object] = {"before": before_commit, "after": after_commit}
    worst = 0.0
    for name in WORKLOADS:
        ratios = [a / b for a, b in zip(cpu[name]["after"], cpu[name]["before"], strict=True)]
        median = statistics.median(ratios) - 1
        low, high = _quartiles(ratios)
        control = " (the control: M8 did not change it)" if name == "portfolio" else ""
        print(
            f"  {name:<9} {median:+.2%}  quartiles {low - 1:+.2%} to {high - 1:+.2%}; "
            f"median {statistics.median(cpu[name]['before']):.3f} s before, "
            f"{statistics.median(cpu[name]['after']):.3f} s after{control}"
        )
        report[name] = {"median_ratio": median + 1, "cpu": cpu[name]}
        if name != "portfolio":
            worst = max(worst, median)
    verdict = "within" if worst <= BUDGET else "OVER"
    print(f"  the dearest workload: {worst:+.2%}, {verdict} the {BUDGET:.0%} budget")
    if args.json is not None:
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if worst <= BUDGET else 1


if __name__ == "__main__":
    raise SystemExit(main())
