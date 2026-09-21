"""A tolerance bounds something, or it is refused.

The type is four lines of arithmetic and one refusal, and the refusal is the
part worth testing: every comparison in v3.5 goes through it, so a tolerance
that silently bounded nothing would make health, comparison and reconciliation
all report agreement they never established.
"""

from decimal import Decimal

import pytest

from alphalab.lifecycle import LifecycleInputError, Tolerance, ToleranceOutcome


class TestConstruction:
    def test_a_tolerance_that_bounds_nothing_is_refused(self) -> None:
        with pytest.raises(LifecycleInputError, match="bounds nothing"):
            Tolerance()

    def test_a_negative_absolute_bound_is_refused(self) -> None:
        with pytest.raises(LifecycleInputError, match="negative allowance"):
            Tolerance(absolute=Decimal("-0.01"))

    def test_a_negative_relative_bound_is_refused(self) -> None:
        with pytest.raises(LifecycleInputError, match="negative allowance"):
            Tolerance(relative=Decimal("-0.001"))

    def test_zero_is_a_legitimate_bound(self) -> None:
        """Demanding, and a real policy: a position count admits no slack."""

        tolerance = Tolerance(absolute=Decimal("0"))
        assert tolerance.outcome(Decimal("5"), Decimal("5")) is ToleranceOutcome.EXACT
        assert tolerance.outcome(Decimal("5"), Decimal("5.01")) is ToleranceOutcome.MATERIAL


class TestOutcome:
    def test_equal_values_are_exact_rather_than_merely_within(self) -> None:
        tolerance = Tolerance(absolute=Decimal("10"))
        assert tolerance.outcome(Decimal("3"), Decimal("3")) is ToleranceOutcome.EXACT

    def test_exactly_at_the_absolute_bound_is_within_it(self) -> None:
        tolerance = Tolerance(absolute=Decimal("0.05"))
        assert (
            tolerance.outcome(Decimal("100.00"), Decimal("100.05"))
            is ToleranceOutcome.WITHIN_TOLERANCE
        )
        assert (
            tolerance.outcome(Decimal("100.00"), Decimal("99.95"))
            is ToleranceOutcome.WITHIN_TOLERANCE
        )

    def test_one_step_beyond_the_bound_is_material(self) -> None:
        tolerance = Tolerance(absolute=Decimal("0.05"))
        assert tolerance.outcome(Decimal("100.00"), Decimal("100.06")) is ToleranceOutcome.MATERIAL

    def test_a_relative_bound_scales_with_the_expected_value(self) -> None:
        tolerance = Tolerance(relative=Decimal("0.01"))
        assert tolerance.allowance(Decimal("100")) == Decimal("1.00")
        assert tolerance.allowance(Decimal("1000")) == Decimal("10.00")
        assert (
            tolerance.outcome(Decimal("1000"), Decimal("1009")) is ToleranceOutcome.WITHIN_TOLERANCE
        )
        assert tolerance.outcome(Decimal("100"), Decimal("109")) is ToleranceOutcome.MATERIAL

    def test_a_relative_bound_permits_nothing_at_zero(self) -> None:
        """Documented, and the case a reader most wants refused."""

        tolerance = Tolerance(relative=Decimal("0.5"))
        assert tolerance.allowance(Decimal("0")) == Decimal("0")
        assert tolerance.outcome(Decimal("0"), Decimal("0.4")) is ToleranceOutcome.MATERIAL

    def test_a_relative_bound_is_measured_against_the_magnitude(self) -> None:
        """A short position is as tolerant as the long one of the same size."""

        tolerance = Tolerance(relative=Decimal("0.1"))
        assert tolerance.allowance(Decimal("-100")) == Decimal("10.0")

    def test_stating_both_bounds_takes_the_more_forgiving_one(self) -> None:
        tolerance = Tolerance(absolute=Decimal("0.01"), relative=Decimal("0.001"))
        # At a small expected value the absolute bound is larger.
        assert tolerance.allowance(Decimal("1")) == Decimal("0.01")
        # At a large one the relative bound is.
        assert tolerance.allowance(Decimal("1000")) == Decimal("1.000")

    def test_the_outcome_is_symmetric_in_the_direction_of_the_difference(self) -> None:
        tolerance = Tolerance(absolute=Decimal("1"))
        assert tolerance.outcome(Decimal("10"), Decimal("12")) is ToleranceOutcome.MATERIAL
        assert tolerance.outcome(Decimal("10"), Decimal("8")) is ToleranceOutcome.MATERIAL


class TestDeterminism:
    def test_the_same_pair_classifies_identically_every_time(self) -> None:
        tolerance = Tolerance(absolute=Decimal("0.02"), relative=Decimal("0.0005"))
        pairs = [
            (Decimal("100.00"), Decimal("100.01")),
            (Decimal("0"), Decimal("0")),
            (Decimal("-50.5"), Decimal("-50.4")),
            (Decimal("1000000"), Decimal("1000400")),
        ]
        first = [tolerance.outcome(*pair) for pair in pairs]
        second = [tolerance.outcome(*pair) for pair in pairs]
        assert first == second

    def test_a_tolerance_is_a_value(self) -> None:
        assert Tolerance(absolute=Decimal("1")) == Tolerance(absolute=Decimal("1"))
        assert Tolerance(absolute=Decimal("1")) != Tolerance(relative=Decimal("1"))
