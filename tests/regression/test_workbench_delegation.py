"""The Workbench can name the tab it opened, and a session stays linear.

``benchmarks/benchmark_workbench.py`` crashed on its first iteration --
``WorkbenchValidationError: Tab 'bt-BT-0' is not open.`` -- identically at every
tag back to v2.14. Three separate defects met there, and only the first is the
benchmark's own:

1. **The tab identity was unobtainable.** ``WorkbenchEngine.run_backtest`` opens
   a tab named after the ``result_id`` Strategy Studio *mints*, not the
   ``backtest_id`` the caller supplied. The benchmark closed
   ``bt-<backtest_id>``. It had no better option: ``Tab.is_active`` existed and
   every transition maintained it, but :mod:`alphalab.workbench.views` exposed
   no way to read it, so the only way to learn the identifier was to
   re-implement the facade's own event scan.

2. **Finding the result was O(events) per delegation.** The facade filtered
   Studio's whole event log by ``type(e).__name__ == "BacktestCompleted"`` and
   took the last match, so ``N`` delegations cost ``O(N^2)`` -- and the name
   comparison would have accepted an event of that name from any package.

3. **Both packages accumulated state the pre-v2.1 way.** ``(*state.events, evt)``,
   ``dict(state.backtest_results)`` and ``(*proj.backtests, config)`` each copied
   their whole container per call, three quadratic terms in one loop. Throughput
   halved on every doubling of the workload: the benchmark could not have
   completed its declared 100,000 iterations even with the tab identity fixed.

``alphalab.common`` has had the answer to (3) since v2.1 and v2.2 --
``AppendOnlyLog`` and ``PersistentMap``, which the execution path uses and these
two packages had never taken.

A fourth defect surfaced while fixing (1): ``close_project`` could leave pinned
tabs with *no* active tab, breaking the invariant ``close_tab`` maintains. It
was invisible precisely because nothing could observe the focus.
"""

import time

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
from alphalab.studio import (
    BacktestConfiguration,
    PipelineDefinition,
    Project,
    StrategyStudioEngine,
    StrategyStudioState,
)
from alphalab.studio.runner import StudioRunner
from alphalab.workbench import (
    WorkbenchEngine,
    WorkbenchManager,
    WorkbenchState,
    active_tab,
    active_tabs,
)

METRICS = {"total_return": 0.10}

# Ratio of the two workload sizes used by the timing test.
SCALE = 8
SMALL = 1_000
LARGE = SMALL * SCALE
# Linear growth predicts ~8x. Quadratic growth predicts ~64x. The measured
# quadratic before the fix was ~3.2x per doubling, i.e. ~33x over this range.
MAX_GROWTH = 24.0


def _booted() -> tuple[WorkbenchState, StrategyStudioState]:
    studio = StrategyStudioEngine.initialize("STUDIO", "/bench")
    studio = StrategyStudioEngine.create_project(studio, Project("PROJ", "P", 1000.0), 1000.0)
    return WorkbenchEngine.initialize("WB", 1000.0), studio


def _config(index: int) -> BacktestConfiguration:
    return BacktestConfiguration(f"BT-{index}", "STRAT-1", (), 0.0, 100.0, 100_000.0)


def _cycle(
    workbench: WorkbenchState, studio: StrategyStudioState, index: int
) -> tuple[WorkbenchState, StrategyStudioState]:
    """One full UI cycle: delegate, render the result tab, close it."""

    workbench, studio = WorkbenchEngine.run_backtest(
        workbench, studio, "PROJ", _config(index), METRICS, float(1001 + index)
    )
    rendered = active_tab(workbench)
    assert rendered is not None
    return WorkbenchManager.close_tab(workbench, rendered.tab_id, float(1001 + index)), studio


# ---------------------------------------------------------------------------
# 1 -- the tab identity
# ---------------------------------------------------------------------------


def test_the_rendered_tab_is_reachable_from_the_returned_state() -> None:
    workbench, studio = _booted()

    workbench, studio = WorkbenchEngine.run_backtest(
        workbench, studio, "PROJ", _config(0), METRICS, 1001.0
    )

    rendered = active_tab(workbench)
    assert rendered is not None
    assert rendered.tab_id in {tab.tab_id for tab in active_tabs(workbench)}
    # Closing it is the operation the benchmark could not perform.
    WorkbenchManager.close_tab(workbench, rendered.tab_id, 1002.0)


def test_the_tab_is_named_after_the_minted_result_and_not_the_backtest_id() -> None:
    """The exact mismatch the benchmark crashed on, stated as a fact."""

    workbench, studio = _booted()
    config = _config(0)

    workbench, studio = WorkbenchEngine.run_backtest(
        workbench, studio, "PROJ", config, METRICS, 1001.0
    )

    rendered = active_tab(workbench)
    assert rendered is not None
    assert rendered.tab_id != f"bt-{config.backtest_id}"
    minted = next(iter(studio.backtest_results))
    assert rendered.tab_id == f"bt-{minted}"
    assert rendered.content_ref == minted


def test_a_pipeline_delegation_is_reachable_the_same_way() -> None:
    workbench, studio = _booted()
    pipeline = PipelineDefinition("PIPE-1", "PROJ", "P", ())

    workbench, studio = WorkbenchEngine.run_pipeline(
        workbench, studio, "PROJ", pipeline, METRICS, 1.0, 1001.0
    )

    rendered = active_tab(workbench)
    assert rendered is not None
    minted = next(iter(studio.pipeline_results))
    assert rendered.tab_id == f"pipe-{minted}"


def test_a_hundred_delegations_each_render_a_distinct_closable_tab() -> None:
    workbench, studio = _booted()
    seen: set[str] = set()

    for index in range(100):
        workbench, studio = WorkbenchEngine.run_backtest(
            workbench, studio, "PROJ", _config(index), METRICS, float(1001 + index)
        )
        rendered = active_tab(workbench)
        assert rendered is not None
        assert rendered.tab_id not in seen, "a delegation reused an earlier tab"
        seen.add(rendered.tab_id)
        workbench = WorkbenchManager.close_tab(workbench, rendered.tab_id, float(1001 + index))

    assert active_tabs(workbench) == ()
    assert active_tab(workbench) is None
    assert studio.metrics.backtests_run == 100


# ---------------------------------------------------------------------------
# 2 -- the result is found by type, in a call-bounded read
# ---------------------------------------------------------------------------


def test_the_facade_reads_only_the_events_its_own_call_appended() -> None:
    """Not "the last BacktestCompleted in the whole log"."""

    from alphalab.workbench.engine import _events_appended_by

    workbench, studio = _booted()
    for index in range(5):
        workbench, studio = _cycle(workbench, studio, index)

    before = studio
    after = StudioRunner.run_backtest(before, "PROJ", _config(99), METRICS, 2000.0)

    appended = _events_appended_by(before, after)
    assert len(appended) == 1, "one delegation appends one event"
    assert appended[0] is after.events[-1]


def test_the_facade_holds_no_class_name_comparison() -> None:
    """A name matched an event of that name from any package. Types do not.

    Checked over the executable code only -- the module docstring quotes the
    comparison it replaced, which is the point of having it there.
    """

    import ast
    import inspect

    from alphalab.workbench import engine

    tree = ast.parse(inspect.getsource(engine))
    compares = [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare) and "__name__" in ast.unparse(node)
    ]
    assert compares == [], (
        "the facade must select Studio's result event with isinstance, never by "
        f"class name: {compares}"
    )


# ---------------------------------------------------------------------------
# 3 -- the accumulation is the canonical one
# ---------------------------------------------------------------------------


def test_both_packages_use_the_canonical_containers() -> None:
    workbench, studio = _booted()

    assert isinstance(workbench.events, AppendOnlyLog)
    assert isinstance(workbench.saved_layouts, PersistentMap)
    assert isinstance(studio.events, AppendOnlyLog)
    assert isinstance(studio.projects, PersistentMap)
    assert isinstance(studio.backtest_results, PersistentMap)
    assert isinstance(studio.projects["PROJ"].backtests, AppendOnlyLog)


def test_a_workbench_session_does_not_copy_its_history() -> None:
    """The structural property the linear cost rests on."""

    workbench, studio = _booted()
    workbench_buffers = set()
    studio_buffers = set()

    for index in range(200):
        workbench, studio = _cycle(workbench, studio, index)
        workbench_buffers.add(id(workbench.events._buffer))
        studio_buffers.add(id(studio.events._buffer))

    assert len(workbench_buffers) == 1, "the workbench event log branched"
    assert len(studio_buffers) == 1, "the studio event log branched"
    assert len(studio.events) == 201  # the project creation plus 200 delegations
    assert len(studio.projects["PROJ"].backtests) == 200


def test_the_history_is_retained_in_full_and_in_order() -> None:
    """Linear cost must not have been bought by dropping history."""

    workbench, studio = _booted()
    for index in range(50):
        workbench, studio = _cycle(workbench, studio, index)

    recorded = [config.backtest_id for config in studio.projects["PROJ"].backtests]
    assert recorded == [f"BT-{index}" for index in range(50)]
    assert len(studio.backtest_results) == 50
    assert len(workbench.events) == 1 + 50 * 2  # started, then open+close per cycle


def test_a_workbench_session_grows_linearly_with_the_workload() -> None:
    """Coarse backstop. It catches a return to quadratic, not constant factors."""

    def session(count: int) -> float:
        workbench, studio = _booted()
        configs = [_config(index) for index in range(count)]
        start = time.perf_counter()
        for index in range(count):
            workbench, studio = WorkbenchEngine.run_backtest(
                workbench, studio, "PROJ", configs[index], METRICS, float(1001 + index)
            )
            rendered = active_tab(workbench)
            assert rendered is not None
            workbench = WorkbenchManager.close_tab(workbench, rendered.tab_id, float(1001 + index))
        return time.perf_counter() - start

    small = min(session(SMALL) for _ in range(3))
    large = min(session(LARGE) for _ in range(3))

    growth = large / small
    assert growth < MAX_GROWTH, (
        f"an {SCALE}x workload cost {growth:.1f}x; linear predicts ~{SCALE}x and "
        f"quadratic ~{SCALE**2}x"
    )


# ---------------------------------------------------------------------------
# 4 -- the tab invariant close_project used to break
# ---------------------------------------------------------------------------


def test_closing_a_project_leaves_the_surviving_tabs_focused() -> None:
    from dataclasses import replace

    state = WorkbenchEngine.initialize("WB", 1.0)
    state = WorkbenchManager.open_project(state, "PROJ", 1.0)
    state = WorkbenchManager.open_tab(state, "t1", "T1", "r1", 1.0)
    state = WorkbenchManager.open_tab(state, "t2", "T2", "r2", 2.0)
    # Pin the tab that is *not* focused, so closing the project removes the focus.
    state = replace(state, tabs=tuple(replace(t, is_pinned=(t.tab_id == "t1")) for t in state.tabs))
    focused_before = active_tab(state)
    assert focused_before is not None and focused_before.tab_id == "t2"

    closed = WorkbenchManager.close_project(state, 3.0)

    assert [tab.tab_id for tab in closed.tabs] == ["t1"]
    focused = active_tab(closed)
    assert focused is not None, "pinned tabs survived with no active tab"
    assert focused.tab_id == "t1"


def test_the_invariant_holds_across_every_tab_transition() -> None:
    """While any tab is open, exactly one is active."""

    from dataclasses import replace

    state = WorkbenchEngine.initialize("WB", 1.0)
    state = WorkbenchManager.open_project(state, "PROJ", 1.0)

    def check(s: WorkbenchState) -> None:
        actives = [tab for tab in s.tabs if tab.is_active]
        assert len(actives) == (1 if s.tabs else 0), f"{len(actives)} active of {len(s.tabs)}"
        assert (active_tab(s) is None) == (not s.tabs)

    for index in range(4):
        state = WorkbenchManager.open_tab(state, f"t{index}", "T", "r", float(index))
        check(state)

    state = replace(
        state, tabs=tuple(replace(t, is_pinned=(t.tab_id in {"t0", "t1"})) for t in state.tabs)
    )
    state = WorkbenchManager.close_tab(state, "t3", 10.0)
    check(state)
    state = WorkbenchManager.close_project(state, 11.0)
    check(state)
    for tab_id in ("t0", "t1"):
        state = WorkbenchManager.close_tab(state, tab_id, 12.0)
        check(state)

    assert state.tabs == ()
    assert active_tab(state) is None


def test_closing_a_project_with_no_pinned_tabs_leaves_nothing_focused() -> None:
    state = WorkbenchEngine.initialize("WB", 1.0)
    state = WorkbenchManager.open_project(state, "PROJ", 1.0)
    state = WorkbenchManager.open_tab(state, "t1", "T1", "r1", 1.0)

    closed = WorkbenchManager.close_project(state, 2.0)

    assert closed.tabs == ()
    assert active_tab(closed) is None
