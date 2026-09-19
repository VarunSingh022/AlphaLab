"""Raw prices and adjusted prices are different data, and never confusable.

Apple's close on 2014-06-06 was $645.57 raw and $92.22 after its 7-for-1 split.
A backtest that sizes from one and computes returns from the other is wrong by
a factor of seven and raises nothing. These tests hold the boundary that keeps
the two apart, and the traceability that says which one is in hand.
"""

from __future__ import annotations

import pytest

from alphalab.data import (
    DataValidationError,
    Delisting,
    Merger,
    PriceBasis,
    SymbolChange,
    apply_adjustments,
)
from alphalab.data.feed import Bar, Dividend, Split

DAY = 86_400.0


def _bar(timestamp: float, close: float, volume: float = 100.0) -> Bar:
    return Bar(
        symbol="AAPL",
        timestamp=timestamp,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
    )


SERIES = (_bar(1.0 * DAY, 700.0), _bar(2.0 * DAY, 644.0), _bar(4.0 * DAY, 93.0))
SPLIT = Split(symbol="AAPL", timestamp=3.0 * DAY, ratio=7.0)


# --------------------------------------------------------------------------- #
# Splits
# --------------------------------------------------------------------------- #


def test_a_split_adjusts_prices_before_the_ex_date_and_leaves_later_ones_alone() -> None:
    outcome = apply_adjustments(SERIES, [SPLIT], [], PriceBasis.SPLIT_ADJUSTED)

    assert outcome.bars[0].close == pytest.approx(100.0)
    assert outcome.bars[1].close == pytest.approx(92.0)
    assert outcome.bars[2].close == 93.0, "on or after the ex-date, untouched"


def test_a_split_adjusts_volume_in_the_opposite_direction() -> None:
    """Adjusting only the price silently breaks every turnover and notional
    figure downstream: the same money changes hands either way."""

    outcome = apply_adjustments(SERIES, [SPLIT], [], PriceBasis.SPLIT_ADJUSTED)

    assert outcome.bars[0].volume == pytest.approx(700.0)
    assert outcome.bars[0].close * outcome.bars[0].volume == pytest.approx(
        SERIES[0].close * SERIES[0].volume
    ), "price x volume is preserved, which is the invariant a split has"
    assert outcome.bars[2].volume == 100.0


def test_the_adjustment_is_traceable_to_the_action_that_caused_it() -> None:
    outcome = apply_adjustments(SERIES, [SPLIT], [], PriceBasis.SPLIT_ADJUSTED)

    assert len(outcome.adjustments) == 1
    record = outcome.adjustments[0]
    assert record.action == "split"
    assert record.symbol == "AAPL"
    assert record.effective_timestamp == 3.0 * DAY
    assert record.factor == pytest.approx(1.0 / 7.0)
    assert record.bars_affected == 2, "measured, not assumed"
    assert "7-for-1" in record.detail


def test_a_non_positive_split_ratio_is_refused() -> None:
    with pytest.raises(DataValidationError) as error:
        apply_adjustments(SERIES, [Split("AAPL", 3.0 * DAY, 0.0)], [], PriceBasis.SPLIT_ADJUSTED)

    assert "ratio must be positive" in str(error.value)


def test_an_action_for_another_instrument_does_not_touch_this_one() -> None:
    outcome = apply_adjustments(
        SERIES, [Split("MSFT", 3.0 * DAY, 2.0)], [], PriceBasis.SPLIT_ADJUSTED
    )

    assert [bar.close for bar in outcome.bars] == [700.0, 644.0, 93.0]
    assert outcome.adjustments[0].bars_affected == 0, "recorded, and affected nothing"


# --------------------------------------------------------------------------- #
# Dividends
# --------------------------------------------------------------------------- #


def test_a_dividend_adjusts_prices_only_for_the_total_return_basis() -> None:
    series = (_bar(1.0 * DAY, 100.0), _bar(2.0 * DAY, 99.0))
    dividend = Dividend(symbol="AAPL", timestamp=2.0 * DAY, amount=1.0, currency="USD")

    split_only = apply_adjustments(series, [], [dividend], PriceBasis.SPLIT_ADJUSTED)
    total = apply_adjustments(series, [], [dividend], PriceBasis.TOTAL_RETURN)

    assert split_only.bars[0].close == 100.0, "a dividend is not a split"
    assert split_only.adjustments == ()
    assert total.bars[0].close == pytest.approx(99.0), "(100 - 1) / 100 applied to the prior bar"
    assert total.bars[1].close == 99.0


def test_a_dividend_leaves_volume_alone() -> None:
    series = (_bar(1.0 * DAY, 100.0, volume=500.0), _bar(2.0 * DAY, 99.0))
    dividend = Dividend("AAPL", 2.0 * DAY, 1.0, "USD")

    outcome = apply_adjustments(series, [], [dividend], PriceBasis.TOTAL_RETURN)

    assert outcome.bars[0].volume == 500.0, "no shares were created or destroyed"


def test_a_dividend_with_no_prior_bar_is_refused_rather_than_guessed() -> None:
    """The factor is computed from the close before the ex-date. Without one
    there is no factor, and inventing a reference price would silently rescale
    the whole series."""

    with pytest.raises(DataValidationError) as error:
        apply_adjustments(
            (_bar(5.0 * DAY, 100.0),),
            [],
            [Dividend("AAPL", 1.0 * DAY, 1.0, "USD")],
            PriceBasis.TOTAL_RETURN,
        )

    assert "no bar precedes the ex-date" in str(error.value)


def test_a_dividend_at_or_above_the_prior_close_is_refused() -> None:
    with pytest.raises(DataValidationError) as error:
        apply_adjustments(
            (_bar(1.0 * DAY, 10.0), _bar(2.0 * DAY, 9.0)),
            [],
            [Dividend("AAPL", 2.0 * DAY, 10.0, "USD")],
            PriceBasis.TOTAL_RETURN,
        )

    assert "zero or below" in str(error.value)


# --------------------------------------------------------------------------- #
# The basis itself
# --------------------------------------------------------------------------- #


def test_the_raw_basis_applies_nothing_and_says_so() -> None:
    outcome = apply_adjustments(SERIES, [], [], PriceBasis.RAW)

    assert outcome.bars == SERIES
    assert outcome.basis is PriceBasis.RAW
    assert outcome.adjustments == ()


def test_asking_for_raw_while_supplying_actions_is_refused() -> None:
    """It asks for an adjustment and for the claim that none was made."""

    with pytest.raises(DataValidationError) as error:
        apply_adjustments(SERIES, [SPLIT], [], PriceBasis.RAW)

    assert "nothing to apply" in str(error.value)


def test_the_outcome_states_the_basis_the_bars_are_now_on() -> None:
    assert apply_adjustments(SERIES, [SPLIT], [], PriceBasis.SPLIT_ADJUSTED).basis is (
        PriceBasis.SPLIT_ADJUSTED
    )


def test_splits_apply_in_chronological_order_so_factors_compound_correctly() -> None:
    series = (_bar(1.0 * DAY, 400.0), _bar(5.0 * DAY, 50.0))
    splits = [Split("AAPL", 4.0 * DAY, 2.0), Split("AAPL", 2.0 * DAY, 4.0)]

    outcome = apply_adjustments(series, splits, [], PriceBasis.SPLIT_ADJUSTED)

    assert outcome.bars[0].close == pytest.approx(50.0), "400 / 4 / 2"
    assert [record.effective_timestamp for record in outcome.adjustments] == [
        2.0 * DAY,
        4.0 * DAY,
    ]


# --------------------------------------------------------------------------- #
# Lifecycle events are declared, not applied
# --------------------------------------------------------------------------- #


def test_lifecycle_events_carry_their_facts_without_rewriting_history() -> None:
    """Rewriting a series to use today's ticker throughout asserts knowledge of
    a rename that had not happened yet. AlphaLab records the event instead."""

    rename = SymbolChange(old_symbol="FB", new_symbol="META", effective_timestamp=2.0 * DAY)
    delisting = Delisting(symbol="TWTR", effective_timestamp=3.0 * DAY, reason="taken private")
    merger = Merger(
        target_symbol="ATVI",
        acquirer_symbol="MSFT",
        share_ratio=0.0,
        cash_per_share=95.0,
        effective_timestamp=4.0 * DAY,
    )

    assert rename.old_symbol == "FB" and rename.new_symbol == "META"
    assert delisting.reason == "taken private"
    assert merger.cash_per_share == 95.0, "cash consideration is not expressible as a ratio"
    assert merger.share_ratio == 0.0, "an all-cash deal, stated as such"
