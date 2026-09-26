"""Point-in-time fundamentals, in the shapes the factor computations read.

Two bridges from :mod:`alphalab.alt_data.fundamentals` into this package, and
neither re-implements anything on the far side of it:

* :func:`fundamental_snapshot_as_of` builds the
  :class:`~alphalab.factor_library.inputs.FundamentalSnapshot` the v2 style
  factors -- :func:`~alphalab.factor_library.value.compute_value`,
  :func:`~alphalab.factor_library.quality.compute_quality`,
  :func:`~alphalab.factor_library.carry.compute_carry` -- have always consumed.
  ``inputs.py`` said a future data package would produce these snapshots; this
  is that producer, and it produces them *as they were knowable* at the
  snapshot's instant rather than from whatever figures a caller had to hand.
* :func:`fundamental_frame` samples one input -- trailing earnings, latest book
  equity -- across a universe on a research clock, as a
  :class:`~alphalab.factor_library.knowledge.KnowledgeFrame`, so cross-sectional
  fundamental factors are computed by the v3.2 feature engine like any other.

The style factors' ``timestamp`` argument remains a label, as it always was:
``compute_momentum`` stamps a result at the instant it is told. Point-in-time
correctness therefore belongs to whatever *produces* the input, and a snapshot
built here carries the research instant it was read at as its ``timestamp``, so
the label and the knowledge agree.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from alphalab.alt_data.exceptions import AltDataInputError
from alphalab.alt_data.fundamentals import (
    FundamentalInput,
    FundamentalObservation,
    aggregate_timeline,
    currency_of_per_share_unit,
    fundamental_inputs_as_of,
)
from alphalab.alt_data.observation_set import ObservationView, VintagePolicy
from alphalab.factor_library.exceptions import FactorInputError
from alphalab.factor_library.inputs import FundamentalSnapshot
from alphalab.factor_library.knowledge import (
    KnowledgeFrame,
    KnowledgeStep,
    ResearchClock,
    _frame,
    _require_universe,
)

__all__ = [
    "SnapshotSpecification",
    "fundamental_frame",
    "fundamental_snapshot_as_of",
]


@dataclass(frozen=True, slots=True)
class SnapshotSpecification:
    """Which line items feed each field of a :class:`FundamentalSnapshot`.

    A source's line-item names are its own vocabulary, so there is no default
    mapping. Book value per share is book equity divided by shares outstanding,
    both as knowable at the instant.

    Attributes:
        earnings_per_share: Trailing earnings per share, unit
            ``"<currency>/share"`` -- typically twelve months of a quarterly
            per-share line item.
        book_equity: Common equity, in the price's currency.
        shares_outstanding: The share count, unit ``"shares"``.
        dividends_per_share: Trailing dividends per share, unit
            ``"<currency>/share"``.
    """

    earnings_per_share: FundamentalInput
    book_equity: FundamentalInput
    shares_outstanding: FundamentalInput
    dividends_per_share: FundamentalInput

    @property
    def inputs(self) -> tuple[FundamentalInput, ...]:
        """The four inputs, in field order."""

        return (
            self.earnings_per_share,
            self.book_equity,
            self.shares_outstanding,
            self.dividends_per_share,
        )


def fundamental_snapshot_as_of(
    view: ObservationView[FundamentalObservation],
    subject: str,
    as_of: float,
    price: Decimal,
    price_unit: str,
    price_observed_at: float,
    policy: VintagePolicy,
    spec: SnapshotSpecification,
) -> FundamentalSnapshot:
    """The snapshot the style factors read, built from what was knowable at ``as_of``.

    Raises:
        FactorInputError: If the price was observed after ``as_of`` -- a snapshot
            priced with a figure nobody had seen yet -- or is not positive; if
            the four input names are not distinct; if any input was not
            knowable at ``as_of``; if the share count is not positive; or if
            the units disagree with the price's currency.
    """

    if price_observed_at > as_of:
        raise FactorInputError(
            f"The price for {subject!r} was observed at {price_observed_at!r}, after the "
            f"snapshot instant {as_of!r}."
        )
    if price <= 0:
        raise FactorInputError(f"A price of {price} for {subject!r} is not a price.")
    names = [spec_input.name for spec_input in spec.inputs]
    if len(set(names)) != len(names):
        raise FactorInputError(f"The snapshot's four inputs must be named apart, got {names}.")
    try:
        currency = currency_of_per_share_unit(price_unit)
        inputs = fundamental_inputs_as_of(view, subject, as_of, policy, spec.inputs)
    except AltDataInputError as error:
        raise FactorInputError(str(error)) from error
    if inputs.missing:
        raise FactorInputError(
            f"{subject!r} has no complete snapshot at {as_of!r}: "
            + "; ".join(f"{name}: {why}" for name, why in sorted(inputs.missing.items()))
        )
    expected = {
        spec.earnings_per_share.name: price_unit,
        spec.book_equity.name: currency,
        spec.shares_outstanding.name: "shares",
        spec.dividends_per_share.name: price_unit,
    }
    for name, unit in expected.items():
        if inputs.units[name] != unit:
            raise FactorInputError(
                f"{subject!r} input {name!r} is in {inputs.units[name]!r}; a snapshot priced "
                f"in {price_unit!r} needs {unit!r}."
            )
    shares = inputs.values[spec.shares_outstanding.name]
    if shares <= 0:
        raise FactorInputError(f"{subject!r} reports {shares} shares outstanding.")
    return FundamentalSnapshot(
        asset_id=subject,
        timestamp=as_of,
        price=price,
        earnings_per_share=inputs.values[spec.earnings_per_share.name],
        book_value_per_share=inputs.values[spec.book_equity.name] / shares,
        dividend_per_share=inputs.values[spec.dividends_per_share.name],
    )


def fundamental_frame(
    view: ObservationView[FundamentalObservation],
    spec: FundamentalInput,
    clock: ResearchClock,
    subjects: Sequence[str],
    *,
    max_age_seconds: float | None,
    policy: VintagePolicy,
) -> KnowledgeFrame:
    """One fundamental input for each subject at each research instant, as knowable then.

    Each subject's input is read as an
    :func:`~alphalab.alt_data.fundamentals.aggregate_timeline` -- the same
    aggregation :func:`~alphalab.alt_data.fundamentals.fundamental_inputs_as_of`
    applies -- and sampled by bisection. Staleness is measured from the end of
    the latest period behind a value.

    Raises:
        FactorInputError: If the universe is malformed, the input asks for
            twelve months of a balance-sheet item, or nothing was knowable
            anywhere.
    """

    _require_universe(subjects)
    timelines: dict[str, list[KnowledgeStep]] = {}
    for subject in subjects:
        try:
            steps = aggregate_timeline(view, subject, spec, policy)
        except AltDataInputError as error:
            raise FactorInputError(str(error)) from error
        timelines[subject] = [
            KnowledgeStep(
                step.known_at,
                None if step.value is None else float(step.value),
                step.observed_at,
                step.record_ids,
            )
            for step in steps
        ]
    selection = f"{spec.statement.name.lower()}.{spec.line_item}:{spec.aggregation.name.lower()}"
    return _frame(
        "fundamental",
        view,
        selection,
        timelines,
        clock,
        subjects,
        max_age_seconds,
        policy,
    )
