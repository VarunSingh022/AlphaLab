"""What the backtest said, what paper did, and what the live account actually did.

The question this answers is the one every firm asks on the day a strategy goes
live and cannot answer from anything AlphaLab had before v3.5: *is it doing what
it was supposed to do?* A backtest produces trades, fills, P&L and exposure; so
does a paper run; so does a live account. All three were readable and none of
them was comparable, because nothing said which backtested fill corresponds to
which live one, or how far apart two numbers may be before the difference means
something.

Three sources, three pairs
--------------------------

.. code-block:: text

    EXPECTED ---- expected vs paper ----> PAPER
        |                                   |
        +-------- expected vs live -------> LIVE
                                             ^
                 paper vs live --------------+

:func:`compare_runs` does one pair and :func:`compare_expected_paper_live` does
all three. In every pair the **left** source is the reference -- the one the
outcome vocabulary calls *expected* -- so a paper-vs-live comparison reports a
live trade with no paper counterpart as ``MISSING_OBSERVED``, and paper is what
it was missing from.

Alignment is declared, never guessed
-------------------------------------

Two runs' trades line up by an :class:`AlignmentKey`, and there are exactly two
because there are exactly two honest answers:

``ORDER_ID``
    AlphaLab's own order identity. Correct when both runs were seeded from the
    same :class:`~alphalab.runtime.run.RunConfig` seed and therefore draw the
    same identifier stream -- the property ADR-0022 gives, and the only
    circumstance in which two separate runs genuinely produce the same order
    ids.
``ASSET_AND_TIME``
    ``asset_id`` and the execution timestamp. Correct when the two runs have
    different identifier streams, which is every unseeded run and every live
    account whose fills came from a venue.

There is no third mode that infers one, and no default. A wrong alignment
compares a Tuesday's fill against a Thursday's and reports the difference as
slippage, which is worse than reporting nothing.

Tolerances are stated, never assumed
-------------------------------------

A metric with no :class:`~alphalab.lifecycle.tolerance.Tolerance` in the supplied
mapping is reported ``NOT_COMPARABLE`` with the reason, and never as matching.
See :mod:`alphalab.lifecycle.tolerance` for why there is no default.

Dimensions
----------

* **Money is compared per currency and never summed across two.** Realized P&L
  and exposure are :class:`~alphalab.portfolio.amounts.CurrencyAmounts`, and the
  comparison walks the union of the currencies the two sides hold, reading each
  with :meth:`~alphalab.portfolio.amounts.CurrencyAmounts.of`. ADR-0020 removed
  the number that added a yen figure to a dollar one, and this does not
  reintroduce it.
* **An absent currency inside a supplied accumulation is a real zero**, because
  that is what ``CurrencyAmounts`` means: "a currency that has never been booked
  is absent rather than zero, and ``of()`` returns ``0.00`` for it". The
  *accumulation itself* being ``None`` is the missing case, and it is reported
  as missing.
* **Latency is seconds, as a ``Decimal``**, spelled in the field name. Every
  other quantity this module compares -- price, quantity, commission, slippage,
  P&L, exposure -- is an exact ``Decimal``, and a single float among them would
  be the one value whose tolerance arithmetic was inexact. Converting a float
  duration once, explicitly, at the boundary is visible;
  :mod:`alphalab.lifecycle.health` keeps its budgets as ``float`` seconds because
  it subtracts float timestamps and compares them directly, with no tolerance
  arithmetic at all.
* **Exposure is supplied, not computed here.** :attr:`RunObservations.exposure`
  is whatever the caller's book actually means by exposure --
  :class:`~alphalab.portfolio.exposure.ExposureEngine` for a book of shares,
  :attr:`~alphalab.portfolio.contracts.ContractExposure.gross` for a book of
  contracts, which is already a ``CurrencyAmounts``. A third exposure authority
  here would be the one that forgot the multiplier.

Not a connectivity layer
------------------------

Nothing here opens a connection, polls a venue or reads a feed.
:func:`observations_from_backtest` reads a finished run and
:func:`observations_from_broker` reads a normalized
:class:`~alphalab.broker.state.BrokerState` that an adapter already filled in.
Both are pure functions over values.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto
from typing import Protocol

from alphalab.backtesting.state import BacktestResult
from alphalab.broker.state import BrokerState
from alphalab.core.enums import Side
from alphalab.lifecycle.exceptions import LifecycleInputError
from alphalab.lifecycle.tolerance import Tolerance, ToleranceOutcome
from alphalab.portfolio.amounts import CurrencyAmounts

__all__ = [
    "AlignmentKey",
    "ComparisonEntry",
    "ComparisonMetric",
    "ComparisonOutcome",
    "ComparisonSource",
    "FillObservation",
    "PairComparison",
    "RunObservations",
    "ThreeWayComparison",
    "TradeObservation",
    "compare_expected_paper_live",
    "compare_runs",
    "observations_from_backtest",
    "observations_from_broker",
]


class ComparisonSource(Enum):
    """Where a set of observations came from.

    ``EXPECTED`` is the backtest -- what the research said would happen. It is
    named for what it *is* rather than for how it was produced, because a
    caller may legitimately treat a long paper record as the expectation a live
    account is judged against, and calling that source ``BACKTEST`` would make
    the honest thing read as a lie.
    """

    EXPECTED = auto()
    PAPER = auto()
    LIVE = auto()


class AlignmentKey(Enum):
    """How two runs' trades and fills are matched up. See the module docstring."""

    #: AlphaLab's own order identity. Two seeded runs over one dataset share it.
    ORDER_ID = auto()

    #: ``asset_id`` and the execution timestamp, for runs with different
    #: identifier streams.
    ASSET_AND_TIME = auto()


class ComparisonMetric(Enum):
    """What is compared.

    The first five are keyed by an :class:`AlignmentKey`-derived key, one entry
    per trade or fill. The last two are keyed by **currency**, one entry per
    currency either side has booked anything in.
    """

    TRADE_QUANTITY = auto()
    TRADE_PRICE = auto()
    FILL_QUANTITY = auto()
    FILL_PRICE = auto()
    SLIPPAGE = auto()
    EXECUTION_LATENCY = auto()
    REALIZED_PNL = auto()
    EXPOSURE = auto()


#: Metrics keyed by currency rather than by an alignment key.
MONETARY_METRICS = (ComparisonMetric.REALIZED_PNL, ComparisonMetric.EXPOSURE)


class ComparisonOutcome(Enum):
    """What one comparison established.

    Six values, and the last three are the reason this enum exists at all: a
    boolean would have had to report "no expected value", "no observed value"
    and "nobody said how close counts" as either a match or a mismatch, and each
    of those is a third thing.
    """

    #: Both values were supplied and are equal.
    EXACT_MATCH = auto()

    #: Both were supplied and differ by no more than the stated tolerance.
    WITHIN_TOLERANCE = auto()

    #: Both were supplied and differ by more than the stated tolerance.
    MATERIAL_DIFFERENCE = auto()

    #: The reference side has no value here.
    MISSING_EXPECTED = auto()

    #: The observed side has no value here.
    MISSING_OBSERVED = auto()

    #: Neither a match nor a mismatch: no tolerance was stated for the metric,
    #: or the two records under one key describe different things.
    NOT_COMPARABLE = auto()


# --------------------------------------------------------------------------- #
# Observations
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TradeObservation:
    """One executed trade as one source recorded it.

    Attributes:
        key: The alignment key this trade is matched by.
        asset_id: What was traded. Compared before the numbers are: two records
            under one key naming different instruments are not two versions of
            one trade, and their prices are not comparable.
        side: Which way. Compared for the same reason.
        quantity: Positive executed quantity.
        price: Average execution price.
        executed_at: Unix timestamp the trade was recorded.
    """

    key: str
    asset_id: str
    side: Side
    quantity: Decimal
    price: Decimal
    executed_at: float

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise LifecycleInputError("TradeObservation.key cannot be empty.")


@dataclass(frozen=True, slots=True)
class FillObservation:
    """One fill as one source recorded it.

    Attributes:
        key: The alignment key this fill is matched by.
        asset_id: What was filled.
        side: Which way.
        quantity: Positive filled quantity.
        price: Execution price.
        commission: What it cost.
        slippage: The difference between the reference price and the fill, or
            ``None`` when the source does not measure it. A venue fill does not:
            :func:`~alphalab.runtime.broker_routing.execution_report_from_broker`
            has recorded that as "absent, not zero" since v2.3, and this is the
            first type able to say so.
        latency_seconds: How long the execution took, in seconds, or ``None``
            when the source does not measure it. A simulated fill does not --
            an :class:`~alphalab.execution.report.ExecutionReport` carries the
            post-latency stamp and not the pre-latency one, so a latency derived
            from it would be a queue time reported under another name.
        filled_at: Unix timestamp of the fill.
    """

    key: str
    asset_id: str
    side: Side
    quantity: Decimal
    price: Decimal
    commission: Decimal
    filled_at: float
    slippage: Decimal | None = None
    latency_seconds: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise LifecycleInputError("FillObservation.key cannot be empty.")


@dataclass(frozen=True, slots=True)
class RunObservations:
    """Everything one source reported, ready to be compared against another.

    ``None`` means **not observed** in every field that permits it; an empty
    tuple means observed and empty. A comparison of a ``None`` against anything
    reports missing, and never reports a match.

    Attributes:
        source: Which of the three this is.
        trades: Executed trades, or ``None`` when trades were not observed.
        fills: Fills, or ``None`` when fills were not observed.
        realized_pnl: Realized P&L per settlement currency, or ``None``.
        exposure: Exposure per currency, or ``None``. Supplied by the caller
            from whichever exposure authority their book calls for; see the
            module docstring.
    """

    source: ComparisonSource
    trades: tuple[TradeObservation, ...] | None = None
    fills: tuple[FillObservation, ...] | None = None
    realized_pnl: CurrencyAmounts | None = None
    exposure: CurrencyAmounts | None = None


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ComparisonEntry:
    """One metric, under one key, as the two sides reported it.

    Attributes:
        metric: What was compared.
        key: The alignment key, or -- for a metric in :data:`MONETARY_METRICS`
            -- the currency code.
        outcome: What the comparison established.
        expected: The reference side's value, or ``None`` when it had none.
        observed: The other side's value, or ``None`` when it had none.
        detail: Why, for the outcomes that need one. Empty for a plain match.
    """

    metric: ComparisonMetric
    key: str
    outcome: ComparisonOutcome
    expected: Decimal | None
    observed: Decimal | None
    detail: str = ""

    @property
    def is_material(self) -> bool:
        """Whether this entry is a difference somebody has to explain."""

        return self.outcome is ComparisonOutcome.MATERIAL_DIFFERENCE

    @property
    def is_matched(self) -> bool:
        """Whether both sides were supplied and agree within what was stated."""

        return self.outcome in (
            ComparisonOutcome.EXACT_MATCH,
            ComparisonOutcome.WITHIN_TOLERANCE,
        )


@dataclass(frozen=True, slots=True)
class PairComparison:
    """Two sources compared, metric by metric.

    Attributes:
        left: The reference source -- the *expected* side of every entry.
        right: The observed source.
        entries: Every comparison, in :class:`ComparisonMetric` declaration
            order and then by key. Deterministic, so two comparisons of equal
            observations compare equal.
    """

    left: ComparisonSource
    right: ComparisonSource
    entries: tuple[ComparisonEntry, ...] = ()

    @property
    def material(self) -> tuple[ComparisonEntry, ...]:
        """Every entry whose two sides differ by more than was permitted."""

        return tuple(entry for entry in self.entries if entry.is_material)

    @property
    def missing(self) -> tuple[ComparisonEntry, ...]:
        """Every entry one side had no value for."""

        return tuple(
            entry
            for entry in self.entries
            if entry.outcome
            in (ComparisonOutcome.MISSING_EXPECTED, ComparisonOutcome.MISSING_OBSERVED)
        )

    @property
    def incomparable(self) -> tuple[ComparisonEntry, ...]:
        """Every entry that could not be judged at all."""

        return tuple(
            entry for entry in self.entries if entry.outcome is ComparisonOutcome.NOT_COMPARABLE
        )

    @property
    def agrees(self) -> bool:
        """Whether every entry matched.

        ``False`` when anything is material, missing or incomparable. A
        comparison that could not judge half its metrics has not established
        agreement, and this property does not say it has.
        """

        return all(entry.is_matched for entry in self.entries)

    def entries_for(self, metric: ComparisonMetric) -> tuple[ComparisonEntry, ...]:
        """Every entry for one metric, in order."""

        return tuple(entry for entry in self.entries if entry.metric is metric)


@dataclass(frozen=True, slots=True)
class ThreeWayComparison:
    """All three pairs at once, so a divergence can be located.

    Two of the three pairs answer a question the third cannot. A live account
    that differs from the backtest *and* from paper has an execution problem; one
    that differs from the backtest and matches paper has a modelling problem, and
    the fix is in a different place.
    """

    expected_vs_paper: PairComparison
    expected_vs_live: PairComparison
    paper_vs_live: PairComparison

    @property
    def pairs(self) -> tuple[PairComparison, ...]:
        """The three comparisons, in the order they are declared."""

        return (self.expected_vs_paper, self.expected_vs_live, self.paper_vs_live)

    @property
    def material(self) -> tuple[ComparisonEntry, ...]:
        """Every material difference across all three pairs, in pair order."""

        return tuple(entry for pair in self.pairs for entry in pair.material)

    @property
    def agrees(self) -> bool:
        """Whether all three pairs agree."""

        return all(pair.agrees for pair in self.pairs)


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #

_NO_TOLERANCE = (
    "no tolerance was stated for this metric, so how close the two values have to "
    "be is unknown; equality is not a default this comparison will assume."
)

_OUTCOME_OF = {
    ToleranceOutcome.EXACT: ComparisonOutcome.EXACT_MATCH,
    ToleranceOutcome.WITHIN_TOLERANCE: ComparisonOutcome.WITHIN_TOLERANCE,
    ToleranceOutcome.MATERIAL: ComparisonOutcome.MATERIAL_DIFFERENCE,
}


def _entry(
    metric: ComparisonMetric,
    key: str,
    expected: Decimal | None,
    observed: Decimal | None,
    tolerances: Mapping[ComparisonMetric, Tolerance],
    detail: str = "",
) -> ComparisonEntry:
    """One value pair classified, with missing data kept missing.

    The order of the checks is the contract: a missing value is reported as
    missing *before* a tolerance is looked for, so "the live account has no fill
    here" never comes back as "nobody stated a tolerance", and neither ever
    comes back as zero.
    """

    if expected is None and observed is None:
        return ComparisonEntry(
            metric,
            key,
            ComparisonOutcome.MISSING_EXPECTED,
            None,
            None,
            detail or "neither side supplied a value.",
        )
    if expected is None:
        return ComparisonEntry(
            metric,
            key,
            ComparisonOutcome.MISSING_EXPECTED,
            None,
            observed,
            detail or "the reference side supplied no value.",
        )
    if observed is None:
        return ComparisonEntry(
            metric,
            key,
            ComparisonOutcome.MISSING_OBSERVED,
            expected,
            None,
            detail or "the observed side supplied no value.",
        )

    tolerance = tolerances.get(metric)
    if tolerance is None:
        return ComparisonEntry(
            metric, key, ComparisonOutcome.NOT_COMPARABLE, expected, observed, _NO_TOLERANCE
        )

    outcome = _OUTCOME_OF[tolerance.outcome(expected, observed)]
    if outcome is ComparisonOutcome.MATERIAL_DIFFERENCE:
        detail = (
            f"differs by {abs(observed - expected)}, which exceeds the permitted "
            f"{tolerance.allowance(expected)}."
        )
    return ComparisonEntry(metric, key, outcome, expected, observed, detail)


def _incomparable(
    metric: ComparisonMetric,
    key: str,
    expected: Decimal,
    observed: Decimal,
    detail: str,
) -> ComparisonEntry:
    return ComparisonEntry(
        metric, key, ComparisonOutcome.NOT_COMPARABLE, expected, observed, detail
    )


def _identity_conflict(left_asset: str, left_side: Side, right_asset: str, right_side: Side) -> str:
    """Why two records under one key are not two versions of one event, or ``""``."""

    if left_asset != right_asset:
        return (
            f"the two records under this key name different instruments "
            f"({left_asset} and {right_asset}); they are not one event seen twice."
        )
    if left_side is not right_side:
        return (
            f"the two records under this key are on opposite sides "
            f"({left_side.name} and {right_side.name}); they are not one event seen twice."
        )
    return ""


def _compare_trades(
    left: tuple[TradeObservation, ...] | None,
    right: tuple[TradeObservation, ...] | None,
    tolerances: Mapping[ComparisonMetric, Tolerance],
) -> list[ComparisonEntry]:
    quantity_entries: list[ComparisonEntry] = []
    price_entries: list[ComparisonEntry] = []

    if left is None or right is None:
        detail = _side_absence(left, right, "trades")
        return [
            ComparisonEntry(metric, "", ComparisonOutcome.NOT_COMPARABLE, None, None, detail)
            for metric in (ComparisonMetric.TRADE_QUANTITY, ComparisonMetric.TRADE_PRICE)
        ]

    by_left = _keyed(left, "trade")
    by_right = _keyed(right, "trade")
    for key in sorted({*by_left, *by_right}):
        one = by_left.get(key)
        other = by_right.get(key)
        if one is not None and other is not None:
            conflict = _identity_conflict(one.asset_id, one.side, other.asset_id, other.side)
            if conflict:
                quantity_entries.append(
                    _incomparable(
                        ComparisonMetric.TRADE_QUANTITY, key, one.quantity, other.quantity, conflict
                    )
                )
                price_entries.append(
                    _incomparable(
                        ComparisonMetric.TRADE_PRICE, key, one.price, other.price, conflict
                    )
                )
                continue
        quantity_entries.append(
            _entry(
                ComparisonMetric.TRADE_QUANTITY,
                key,
                None if one is None else one.quantity,
                None if other is None else other.quantity,
                tolerances,
                _trade_absence(one, other),
            )
        )
        price_entries.append(
            _entry(
                ComparisonMetric.TRADE_PRICE,
                key,
                None if one is None else one.price,
                None if other is None else other.price,
                tolerances,
                _trade_absence(one, other),
            )
        )
    return quantity_entries + price_entries


def _compare_fills(
    left: tuple[FillObservation, ...] | None,
    right: tuple[FillObservation, ...] | None,
    tolerances: Mapping[ComparisonMetric, Tolerance],
) -> list[ComparisonEntry]:
    metrics = (
        ComparisonMetric.FILL_QUANTITY,
        ComparisonMetric.FILL_PRICE,
        ComparisonMetric.SLIPPAGE,
        ComparisonMetric.EXECUTION_LATENCY,
    )
    if left is None or right is None:
        detail = _side_absence(left, right, "fills")
        return [
            ComparisonEntry(metric, "", ComparisonOutcome.NOT_COMPARABLE, None, None, detail)
            for metric in metrics
        ]

    collected: dict[ComparisonMetric, list[ComparisonEntry]] = {metric: [] for metric in metrics}
    by_left = _keyed(left, "fill")
    by_right = _keyed(right, "fill")
    for key in sorted({*by_left, *by_right}):
        one = by_left.get(key)
        other = by_right.get(key)
        conflict = ""
        if one is not None and other is not None:
            conflict = _identity_conflict(one.asset_id, one.side, other.asset_id, other.side)

        readings: tuple[tuple[ComparisonMetric, Decimal | None, Decimal | None], ...] = (
            (
                ComparisonMetric.FILL_QUANTITY,
                None if one is None else one.quantity,
                None if other is None else other.quantity,
            ),
            (
                ComparisonMetric.FILL_PRICE,
                None if one is None else one.price,
                None if other is None else other.price,
            ),
            (
                ComparisonMetric.SLIPPAGE,
                None if one is None else one.slippage,
                None if other is None else other.slippage,
            ),
            (
                ComparisonMetric.EXECUTION_LATENCY,
                None if one is None else one.latency_seconds,
                None if other is None else other.latency_seconds,
            ),
        )
        for metric, expected, observed in readings:
            if conflict and expected is not None and observed is not None:
                collected[metric].append(_incomparable(metric, key, expected, observed, conflict))
                continue
            collected[metric].append(
                _entry(
                    metric,
                    key,
                    expected,
                    observed,
                    tolerances,
                    _fill_absence(one, other, metric),
                )
            )
    return [entry for metric in metrics for entry in collected[metric]]


def _compare_amounts(
    metric: ComparisonMetric,
    left: CurrencyAmounts | None,
    right: CurrencyAmounts | None,
    tolerances: Mapping[ComparisonMetric, Tolerance],
) -> list[ComparisonEntry]:
    """Per-currency comparison of two accumulations. Never sums across currencies."""

    if left is None or right is None:
        detail = _side_absence(left, right, metric.name.lower().replace("_", " "))
        return [ComparisonEntry(metric, "", ComparisonOutcome.NOT_COMPARABLE, None, None, detail)]

    entries: list[ComparisonEntry] = []
    for currency in sorted({*left.currencies, *right.currencies}):
        # ``of`` is 0.00 for a currency this accumulation never booked, which is
        # what CurrencyAmounts means by an absent key -- a real zero, not a
        # missing observation. The missing case is the whole accumulation being
        # None, handled above.
        entries.append(_entry(metric, currency, left.of(currency), right.of(currency), tolerances))
    return entries


class _Keyed(Protocol):
    """Anything an alignment key indexes. Both observation types satisfy it."""

    @property
    def key(self) -> str: ...


def _keyed[ObservationT: _Keyed](
    observations: Sequence[ObservationT], kind: str
) -> dict[str, ObservationT]:
    """Index observations by key, refusing a key used twice.

    Two records under one key mean the alignment does not identify a single
    event, and picking one of them would report a comparison against half the
    data as though it were against all of it.
    """

    indexed: dict[str, ObservationT] = {}
    for observation in observations:
        if observation.key in indexed:
            raise LifecycleInputError(
                f"Two {kind}s share the alignment key {observation.key!r}. The key does "
                "not identify a single event, so a comparison under it would silently "
                "drop one of them. Use AlignmentKey.ORDER_ID for seeded runs, or give "
                "the observations keys that are unique."
            )
        indexed[observation.key] = observation
    return indexed


def _side_absence(left: object, right: object, what: str) -> str:
    if left is None and right is None:
        return f"neither source observed {what}."
    if left is None:
        return f"the reference source did not observe {what}."
    return f"the observed source did not observe {what}."


def _trade_absence(one: TradeObservation | None, other: TradeObservation | None) -> str:
    if one is None and other is not None:
        return "the reference source recorded no trade under this key."
    if other is None and one is not None:
        return "the observed source recorded no trade under this key."
    return ""


def _fill_absence(
    one: FillObservation | None, other: FillObservation | None, metric: ComparisonMetric
) -> str:
    if one is None and other is not None:
        return "the reference source recorded no fill under this key."
    if other is None and one is not None:
        return "the observed source recorded no fill under this key."
    if metric is ComparisonMetric.SLIPPAGE:
        return "a fill whose source does not measure slippage reports none, not zero."
    if metric is ComparisonMetric.EXECUTION_LATENCY:
        return "a fill whose source does not measure latency reports none, not zero."
    return ""


def compare_runs(
    left: RunObservations,
    right: RunObservations,
    tolerances: Mapping[ComparisonMetric, Tolerance],
) -> PairComparison:
    """Compare two sources, metric by metric.

    ``left`` is the reference: every entry's ``expected`` is its value, and an
    event ``right`` has and ``left`` does not is ``MISSING_EXPECTED``.

    Deterministic and pure. Entries come out in
    :class:`ComparisonMetric` declaration order and then by key, so two calls
    over equal observations return equal results and a stored comparison can be
    diffed against a later one.

    Raises:
        LifecycleInputError: If the two sources are the same, or if either side
            uses one alignment key for two events.
    """

    if left.source is right.source:
        raise LifecycleInputError(
            f"Both sides are {left.source.name}; comparing a source against itself "
            "establishes nothing."
        )

    entries = [
        *_compare_trades(left.trades, right.trades, tolerances),
        *_compare_fills(left.fills, right.fills, tolerances),
        *_compare_amounts(
            ComparisonMetric.REALIZED_PNL, left.realized_pnl, right.realized_pnl, tolerances
        ),
        *_compare_amounts(ComparisonMetric.EXPOSURE, left.exposure, right.exposure, tolerances),
    ]
    order = {metric: index for index, metric in enumerate(ComparisonMetric)}
    entries.sort(key=lambda entry: (order[entry.metric], entry.key))
    return PairComparison(left=left.source, right=right.source, entries=tuple(entries))


def compare_expected_paper_live(
    expected: RunObservations,
    paper: RunObservations,
    live: RunObservations,
    tolerances: Mapping[ComparisonMetric, Tolerance],
) -> ThreeWayComparison:
    """All three pairs, so a divergence can be located rather than only noticed.

    Raises:
        LifecycleInputError: If any source is not the one its parameter names.
            A three-way comparison whose "live" argument holds paper
            observations would produce a report that is wrong about which side
            is which, which is worse than one that refuses.
    """

    for argument, wanted in (
        (expected, ComparisonSource.EXPECTED),
        (paper, ComparisonSource.PAPER),
        (live, ComparisonSource.LIVE),
    ):
        if argument.source is not wanted:
            raise LifecycleInputError(
                f"the {wanted.name.lower()} argument holds {argument.source.name} "
                "observations; a three-way comparison cannot report which side is which "
                "if the sides are mislabelled."
            )

    return ThreeWayComparison(
        expected_vs_paper=compare_runs(expected, paper, tolerances),
        expected_vs_live=compare_runs(expected, live, tolerances),
        paper_vs_live=compare_runs(paper, live, tolerances),
    )


# --------------------------------------------------------------------------- #
# Building observations from what AlphaLab already produces
# --------------------------------------------------------------------------- #


def _trade_key(alignment: AlignmentKey, order_id: str, asset_id: str, timestamp: float) -> str:
    if alignment is AlignmentKey.ORDER_ID:
        return order_id
    return f"{asset_id}@{timestamp!r}"


def observations_from_backtest(
    result: BacktestResult,
    source: ComparisonSource,
    alignment: AlignmentKey,
    exposure: CurrencyAmounts | None = None,
    submitted_at: Mapping[str, float] | None = None,
) -> RunObservations:
    """Read observations off a finished run through the execution path.

    Works for any run that produced a :class:`~alphalab.runtime.run.RunState` --
    a backtest, a replay, a paper session or a live session driven by
    :class:`~alphalab.runtime.live.LiveSession` -- because all four finish as the
    same state and ``BacktestResult`` is the read-only projection over it. Which
    of the three sources it *is* is the caller's to say, and is not guessed from
    the run.

    Trades and fills are read from the canonical records the run already holds:
    :attr:`~alphalab.backtesting.state.BacktestResult.trades`, and the
    :class:`~alphalab.execution.report.ExecutionReport` history for fills, which
    is the record that carries slippage and commission. A fill's ``side`` is
    joined from the OMS order it names, rather than re-derived from the sign of
    anything.

    Realized P&L is
    :attr:`~alphalab.portfolio.engine.PortfolioState.realized_pnl`, already a
    per-currency accumulation. Exposure is **not** derived: see the module
    docstring.

    Args:
        result: The finished run.
        source: Which of the three these observations are.
        alignment: How they will be matched against another source's.
        exposure: Exposure per currency, if the caller wants it compared.
        submitted_at: ``order_id -> submission instant``, if the caller can say
            when each order was sent. Latency is derived from it and the fill's
            own timestamp; without it, every fill reports no latency, because an
            ``ExecutionReport`` carries only the post-latency stamp and a
            latency derived from the order's creation time would be a queue time
            under another name.

    Raises:
        LifecycleInputError: If a fill names an order the run does not hold,
            which would mean the run's own records disagree.
    """

    sides = {str(order.order_id.value): order.side for order in result.orders}

    trades = tuple(
        TradeObservation(
            key=_trade_key(
                alignment,
                "" if trade.order_id is None else str(trade.order_id),
                str(trade.asset_id),
                trade.executed_at,
            ),
            asset_id=str(trade.asset_id),
            side=trade.side,
            quantity=trade.quantity,
            price=trade.average_price,
            executed_at=trade.executed_at,
        )
        for trade in result.trades
    )

    fills: list[FillObservation] = []
    for report in result.state.execution.history.to_tuple():
        side = sides.get(report.order_id)
        if side is None:
            raise LifecycleInputError(
                f"Execution report {report.execution_id} names order "
                f"{report.order_id}, which this run's OMS does not hold. The run's own "
                "records disagree, and guessing a side here would hide that."
            )
        sent = None if submitted_at is None else submitted_at.get(report.order_id)
        fills.append(
            FillObservation(
                key=_trade_key(alignment, report.order_id, report.asset_id, report.timestamp),
                asset_id=report.asset_id,
                side=side,
                quantity=report.fill_quantity,
                price=report.fill_price,
                commission=report.commission,
                filled_at=report.timestamp,
                slippage=report.slippage,
                latency_seconds=(None if sent is None else Decimal(str(report.timestamp - sent))),
            )
        )

    return RunObservations(
        source=source,
        trades=trades,
        fills=tuple(fills),
        realized_pnl=result.state.portfolio.realized_pnl,
        exposure=exposure,
    )


def observations_from_broker(
    state: BrokerState,
    source: ComparisonSource,
    alignment: AlignmentKey,
    realized_pnl: CurrencyAmounts | None = None,
    exposure: CurrencyAmounts | None = None,
    submitted_at: Mapping[str, float] | None = None,
) -> RunObservations:
    """Read observations off a normalized broker state.

    For the live side of a comparison when AlphaLab did not drive the run: the
    fills are the venue's, arriving through the canonical
    :class:`~alphalab.broker.execution.BrokerExecution` an adapter produces.
    Nothing here reaches a venue, holds a credential or knows which venue it is.

    **Slippage is ``None`` on every fill**, and that is the honest reading rather
    than a gap: a venue reports a price, not a difference from a reference it
    never saw. :func:`~alphalab.runtime.broker_routing.execution_report_from_broker`
    has said so since v2.3, and this is where "absent, not zero" stops being a
    comment and becomes a value.

    Trades are ``None``: a broker reports orders and fills, and a *trade* in
    AlphaLab's sense is :class:`~alphalab.core.trade.Trade`, produced by the
    execution path from a fill. Synthesising one per fill here would put a second
    trade authority in a comparison layer.

    Args:
        state: The normalized broker state.
        source: Which of the three these observations are. Usually ``LIVE``.
        alignment: How they will be matched. ``ORDER_ID`` uses the
            ``oms_order_id`` the broker order carries, which is AlphaLab's own
            identity and the only one that lines up with a run's.
        realized_pnl: Per-currency realized P&L, if the caller has it. A
            :class:`~alphalab.broker.account.BrokerAccount` reports cash and
            equity, not realized P&L, so deriving one here would be arithmetic
            on the wrong numbers.
        exposure: Exposure per currency, if the caller has it.
        submitted_at: ``oms_order_id -> submission instant``, for latency.

    Raises:
        LifecycleInputError: If a fill names a broker order the state does not
            hold. That is a reconciliation break, and
            :mod:`alphalab.lifecycle.reconciliation` is what reports it; a
            comparison must not silently drop the fill.
    """

    fills: list[FillObservation] = []
    for execution in state.executions.values():
        order = state.orders.get(execution.broker_order_id)
        if order is None:
            raise LifecycleInputError(
                f"Execution {execution.execution_id} names broker order "
                f"{execution.broker_order_id}, which this state does not hold. That is a "
                "reconciliation break -- reconcile_execution_state reports it -- and "
                "dropping the fill here would hide it."
            )
        sent = None if submitted_at is None else submitted_at.get(order.oms_order_id)
        fills.append(
            FillObservation(
                key=_trade_key(
                    alignment, order.oms_order_id, execution.symbol, execution.timestamp
                ),
                asset_id=execution.symbol,
                side=order.side,
                quantity=execution.fill_quantity,
                price=execution.fill_price,
                commission=execution.commission,
                filled_at=execution.timestamp,
                slippage=None,
                latency_seconds=(
                    None if sent is None else Decimal(str(execution.timestamp - sent))
                ),
            )
        )

    return RunObservations(
        source=source,
        trades=None,
        fills=tuple(fills),
        realized_pnl=realized_pnl,
        exposure=exposure,
    )
