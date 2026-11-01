"""Volatility surfaces arriving as data during a run (phase 5, ledger FUT-039).

A scenario model naming a ``surface_feed`` reads the surfaces the run receives
as :class:`ObservedSurface` deliveries -- each kept as it arrived, its source
and version beside it, on the risk state. A margin check reads the one known by
its instant and as of the latest instant, a revision preferring the later
version, never a late arrival older than one known, never one older than
``surface_max_age``; where none gives a volatility the option is margined by its
own terms. A strategy reads the latest known through ``context.risk_view``.

ACME quotes at 100 at T0, T0 + 60 and T0 + 120; ten of its puts, expiring
ninety days after T0, are written at T0 (the helpers are
tests/integration/test_margin_option_repricing.py's and
tests/integration/test_volatility_surfaces.py's).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import (
    BacktestEngine,
    PausedReplay,
    ReplayBacktest,
    RunSchedule,
)
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.instrument.margin import ScenarioMargin
from alphalab.instrument.registry import declare_margin_model
from alphalab.lifecycle import digest_run
from alphalab.options.enums import OptionType
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.fx import NO_RATES
from alphalab.risk import ObservedSurface
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.execution_pipeline import margin_position
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.runtime.snapshot import capture as capture_pipeline
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from tests.integration import test_margin_and_liquidation as margin
from tests.integration import test_margin_option_repricing as reprice
from tests.integration import test_volatility_surfaces as surfaces
from tests.integration.harness import (
    backtest_config,
    context_factory,
    quote,
    registry_of,
    running_strategy_state,
)

D = Decimal
T0, EXPIRES, SHARE, PUT = reprice.T0, reprice.EXPIRES, reprice.SHARE, reprice.PUT
INSTANTS = (T0, T0 + 60, T0 + 120)
FEED = "vendor"


def model(**kw: Any) -> ScenarioMargin:
    base = reprice.scenarios(PUT, vol=kw.pop("vol", "0"))
    return ScenarioMargin(
        base.scenarios,
        base.maintenance_ratio,
        repriced={PUT.asset_id: surfaces.FROM_SURFACE},
        surface_feed=kw.pop("feed", FEED),
        **kw,
    )


def delivered(vol: str, as_of: float, known_at: float, version: str, **kw: Any) -> ObservedSurface:
    points = kw.pop("points", (("100", EXPIRES, vol),))
    return ObservedSurface(
        surfaces.surface(*points, at=as_of), known_at, kw.pop("source", FEED), version
    )


class Watcher(margin.Trader):
    """Writes the puts at T0; notes the surface its context shows at each quote."""

    def __init__(self) -> None:
        super().__init__({(T0, PUT.asset_id): D("-10")})
        self.seen: dict[float, str | None] = {}

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.quote.asset_id == SHARE.asset_id:
            shown = context.risk_view.volatility_surface(SHARE.asset_id)
            self.seen[event.quote.timestamp] = None if shown is None else shown.version
        return super().on_quote(context, event)


def config(declared: ScenarioMargin) -> Any:
    registry = declare_margin_model(registry_of(SHARE, PUT), declared)
    base = backtest_config("S", starting_cash=D("1000000"), risk_limits=margin.LEVERED)
    return replace(
        base,
        pipeline=replace(
            base.pipeline,
            instruments=registry,
            budget=replace(
                base.pipeline.budget,
                global_capital=margin.WIDE,
                maximum_exposure=margin.WIDE,
                strategy_budgets={"S": margin.WIDE},
            ),
        ),
    )


def dataset() -> MarketDataset:
    marks = {SHARE.asset_id: "100", PUT.asset_id: "5"}
    quotes = [quote(a, at, D(m)) for a, m in marks.items() for at in INSTANTS]
    return MarketDataset.of("FEED", sorted(quotes, key=lambda q: (q.timestamp, q.asset_id)))


def run(*deliveries: ObservedSurface, declared: ScenarioMargin | None = None, **kw: Any) -> Any:
    return BacktestEngine.run(
        config(declared or model()),
        dataset(),
        running_strategy_state("S", kw.pop("trader", None) or Watcher()),
        context_factory,
        schedule=RunSchedule.of(surfaces=deliveries),
    )


def required(result: Any, at: float) -> Decimal:
    figures = margin_position(result.state, NO_RATES, at)
    assert figures is not None
    return figures.initial


def at_vol(vol: float) -> Decimal:
    now = reprice.bs(OptionType.PUT, 100.0, vol=vol)
    return max(reprice.bs(OptionType.PUT, s, vol=vol) - now for s in (85.0, 115.0, 100.0)) * 1000


OWN_TERMS = surfaces.OWN_TERMS


# --------------------------------------------------------------------------- #
# The one a check reads
# --------------------------------------------------------------------------- #


def test_a_surface_is_read_from_its_knowledge_instant_and_never_before() -> None:
    result = run(delivered("0.30", T0, T0 + 30, "v1"))
    assert required(result, T0 + 60) == at_vol(0.30)
    assert required(result, T0 + 10) == OWN_TERMS


def test_a_revision_is_read_from_when_it_is_known() -> None:
    result = run(delivered("0.30", T0, T0 + 20, "v1"), delivered("0.40", T0, T0 + 50, "v2"))
    assert required(result, T0 + 40) == at_vol(0.30)
    assert required(result, T0 + 60) == at_vol(0.40)


def test_a_late_arrival_never_displaces_a_newer_surface() -> None:
    result = run(
        delivered("0.40", T0 + 40, T0 + 45, "v2"),
        delivered("0.30", T0 + 10, T0 + 55, "v1-late"),
    )
    assert required(result, T0 + 60) == at_vol(0.40)


def test_a_stale_surface_is_not_read() -> None:
    result = run(delivered("0.30", T0, T0 + 5, "v1"), declared=model(surface_max_age=30.0))
    assert required(result, T0 + 20) == at_vol(0.30)
    assert required(result, T0 + 60) == OWN_TERMS


def test_a_surface_quoting_nothing_at_the_strike_gives_no_volatility() -> None:
    sparse = delivered("0.30", T0, T0 + 5, "v1", points=(("120", EXPIRES, "0.30"),))
    assert required(run(sparse), T0 + 60) == OWN_TERMS


def test_another_sources_surfaces_are_not_read() -> None:
    other = delivered("0.30", T0, T0 + 5, "v1", source="someone else")
    assert required(run(other), T0 + 60) == OWN_TERMS


def test_between_delivered_expiries_the_volatility_is_in_total_variance() -> None:
    near, far = T0 + 30 * surfaces.DAY, T0 + 150 * surfaces.DAY
    two = delivered("0", T0 - 10, T0, "v1", points=(("100", near, "0.20"), ("100", far, "0.40")))
    declared = surfaces.model(
        surfaces.surface(("100", near, "0.20"), ("100", far, "0.40"), at=T0 - 10)
    )
    # A delivered surface is read exactly as the same surface declared.
    expected = reprice.required(
        reprice.book({PUT.asset_id: "-10"}, declared, PUT, premium={PUT.asset_id: "5"})
    )
    assert required(run(two), T0 + 60) == expected


def test_a_scenarios_volatility_shift_moves_the_delivered_volatility() -> None:
    result = run(delivered("0.30", T0, T0 + 5, "v1"), declared=model(vol="0.10"))
    now = reprice.bs(OptionType.PUT, 100.0, vol=0.30)
    shifted = max(reprice.bs(OptionType.PUT, s, vol=0.40) - now for s in (85.0, 115.0, 100.0))
    assert required(result, T0 + 60) == shifted * 1000


# --------------------------------------------------------------------------- #
# The strategy's view, the record, the declarations
# --------------------------------------------------------------------------- #


def test_a_strategy_sees_only_the_surfaces_already_delivered() -> None:
    watcher = Watcher()
    run(delivered("0.30", T0, T0 + 30, "v1"), delivered("0.40", T0, T0 + 90, "v2"), trader=watcher)
    assert watcher.seen == {T0: None, T0 + 60: "v1", T0 + 120: "v2"}


def test_every_delivery_is_kept_as_it_arrived() -> None:
    result = run(delivered("0.30", T0, T0 + 20, "v1"), delivered("0.40", T0, T0 + 50, "v2"))
    kept = result.state.risk.surfaces[SHARE.asset_id]
    assert [(o.version, o.known_at, o.source) for o in kept] == [
        ("v1", T0 + 20, FEED),
        ("v2", T0 + 50, FEED),
    ]
    payload = deserialize(serialize(capture_pipeline(result.state)))
    assert "surfaces" in payload["risk"]
    plain = deserialize(serialize(capture_pipeline(run().state)))
    assert "surfaces" not in plain["risk"]


def test_one_sources_one_version_is_delivered_once() -> None:
    with pytest.raises(RuntimeValidationError, match="delivered already"):
        run(delivered("0.30", T0, T0 + 20, "v1"), delivered("0.40", T0, T0 + 50, "v1"))


@pytest.mark.parametrize(
    "build",
    [
        lambda: model(surfaces=(surfaces.FLAT,)),
        lambda: model(feed=""),
        lambda: replace(model(), surface_feed=None, surface_max_age=30.0),
        lambda: model(surface_max_age=0),
    ],
)
def test_a_feed_declaration_that_cannot_hold_is_refused(build: Any) -> None:
    with pytest.raises(InstrumentInputError):
        build()


def test_a_surface_cannot_be_known_before_it_is_true() -> None:
    from alphalab.risk.exceptions import RiskError

    with pytest.raises(RiskError):
        delivered("0.30", T0 + 10, T0, "v1")


def test_delivered_surfaces_resume_to_one_result_from_every_pause() -> None:
    schedule = RunSchedule.of(
        surfaces=[delivered("0.30", T0, T0 + 20, "v1"), delivered("0.40", T0, T0 + 90, "v2")]
    )
    cfg, data = config(model(surface_max_age=600.0)), dataset()
    whole = ReplayBacktest.run(
        cfg, data, running_strategy_state("S", Watcher()), context_factory, schedule=schedule
    )
    reference = digest_run(whole.backtest).result_id
    for boundary in range(1, len(data.records)):
        paused = ReplayBacktest.run(
            cfg, data, running_strategy_state("S", Watcher()), context_factory,
            schedule=schedule, pause_when=margin._at(boundary),
        )  # fmt: skip
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        objects = RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model, cfg.pipeline.simulator, {"S": Watcher()},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )  # fmt: skip
        restored = restore_replay(replay_from_primitives(payload), objects, data, schedule)
        resumed = ReplayBacktest.resume(restored, context_factory).backtest
        assert digest_run(resumed).result_id == reference, boundary


def test_a_strategy_is_shown_the_newer_surface_not_the_late_arrival() -> None:
    watcher = Watcher()
    run(
        delivered("0.40", T0 + 40, T0 + 45, "v2"),
        delivered("0.30", T0 + 10, T0 + 55, "v1-late"),
        trader=watcher,
    )
    assert watcher.seen[T0 + 60] == "v2"
