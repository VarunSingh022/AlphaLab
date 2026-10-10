"""Multiple-testing corrections (ledger OFE-005).

The reference values are R's ``p.adjust``, the implementation most published
work cites, on inputs small enough to check by hand.
"""

import itertools
import math

import pytest

from alphalab.research import ResearchValidationError
from alphalab.research.multiple_testing import (
    Correction,
    ErrorRate,
    correct_p_values,
)

FIVE = {"a": 0.01, "b": 0.02, "c": 0.03, "d": 0.04, "e": 0.05}


@pytest.mark.parametrize(
    ("correction", "expected"),
    [
        # p.adjust(c(.01, .02, .03, .04, .05), method = ...)
        (Correction.BONFERRONI, (0.05, 0.10, 0.15, 0.20, 0.25)),
        (Correction.HOLM, (0.05, 0.08, 0.09, 0.09, 0.09)),
        (Correction.BENJAMINI_HOCHBERG, (0.05, 0.05, 0.05, 0.05, 0.05)),
        (Correction.BENJAMINI_YEKUTIELI, (0.1141667,) * 5),
    ],
)
def test_adjusted_p_values_match_r(correction: Correction, expected: tuple[float, ...]) -> None:
    result = correct_p_values(FIVE, correction, 0.05)
    assert tuple(round(result.adjusted[name], 7) for name in "abcde") == pytest.approx(expected)


def test_a_second_r_reference_with_unsorted_input() -> None:
    # p.adjust(c(0.04, 0.001, 0.2, 0.03, 0.01, 0.5), method = ...)
    family = {"u": 0.04, "v": 0.001, "w": 0.2, "x": 0.03, "y": 0.01, "z": 0.5}
    holm = correct_p_values(family, Correction.HOLM, 0.05)
    assert [round(holm.adjusted[k], 6) for k in "uvwxyz"] == [0.12, 0.006, 0.4, 0.12, 0.05, 0.5]
    bh = correct_p_values(family, Correction.BENJAMINI_HOCHBERG, 0.05)
    assert [round(bh.adjusted[k], 6) for k in "uvwxyz"] == [0.06, 0.006, 0.24, 0.06, 0.03, 0.5]
    assert holm.rejected == ("v", "y")
    assert bh.rejected == ("v", "y")
    loose = correct_p_values(family, Correction.BENJAMINI_HOCHBERG, 0.07)
    assert loose.rejected == ("v", "y", "u", "x")


def test_what_each_correction_controls_and_assumes() -> None:
    assert Correction.BONFERRONI.controls is ErrorRate.FAMILY_WISE
    assert Correction.HOLM.controls is ErrorRate.FAMILY_WISE
    assert Correction.BENJAMINI_HOCHBERG.controls is ErrorRate.FALSE_DISCOVERY
    assert Correction.BENJAMINI_YEKUTIELI.controls is ErrorRate.FALSE_DISCOVERY
    assert "PRDS" in Correction.BENJAMINI_HOCHBERG.assumption
    for correction in (Correction.BONFERRONI, Correction.HOLM, Correction.BENJAMINI_YEKUTIELI):
        assert correction.assumption.startswith("none")


def test_holm_never_rejects_less_than_bonferroni_and_bh_never_less_than_holm() -> None:
    families = [
        {f"h{i}": p for i, p in enumerate(ps)}
        for ps in itertools.product((0.001, 0.009, 0.011, 0.02, 0.2), repeat=3)
    ]
    for family in families:
        bonferroni = set(correct_p_values(family, Correction.BONFERRONI, 0.05).rejected)
        holm = set(correct_p_values(family, Correction.HOLM, 0.05).rejected)
        bh = set(correct_p_values(family, Correction.BENJAMINI_HOCHBERG, 0.05).rejected)
        by = set(correct_p_values(family, Correction.BENJAMINI_YEKUTIELI, 0.05).rejected)
        assert bonferroni <= holm <= bh
        assert by <= bh


@pytest.mark.parametrize("correction", list(Correction))
def test_adjusted_values_are_monotone_bounded_and_order_free(correction: Correction) -> None:
    family = {"q": 0.03, "r": 0.001, "s": 0.03, "t": 0.9, "u": 0.03, "v": 0.2}
    result = correct_p_values(family, correction, 0.1)
    ordered = sorted(family, key=lambda name: (family[name], name))
    adjusted = [result.adjusted[name] for name in ordered]
    assert adjusted == sorted(adjusted)
    assert all(family[name] <= result.adjusted[name] <= 1.0 for name in family)
    # a tie is one adjusted value, whatever order the family arrived in
    assert result.adjusted["q"] == result.adjusted["s"] == result.adjusted["u"]
    for permutation in itertools.permutations(family):
        again = correct_p_values({k: family[k] for k in permutation}, correction, 0.1)
        assert again == result


def test_the_result_counts_the_whole_family() -> None:
    result = correct_p_values(FIVE, Correction.HOLM, 0.05)
    assert result.trials == 5
    assert result.controls is ErrorRate.FAMILY_WISE
    assert result.describe() == "HOLM (FAMILY_WISE) at alpha=0.05: 1 of 5 rejected"


@pytest.mark.parametrize(
    ("family", "alpha", "message"),
    [
        ({}, 0.05, "at least one"),
        ({"a": 0.5}, 0.0, "alpha"),
        ({"a": 0.5}, 1.0, "alpha"),
        ({"a": -0.1}, 0.05, r"\[0, 1\]"),
        ({"a": 1.5}, 0.05, r"\[0, 1\]"),
        ({"a": math.nan}, 0.05, r"\[0, 1\]"),
        ({"a": True}, 0.05, "not a number"),
        ({" ": 0.5}, 0.05, "name"),
    ],
)
def test_a_malformed_family_is_refused(
    family: dict[str, float], alpha: float, message: str
) -> None:
    with pytest.raises(ResearchValidationError, match=message):
        correct_p_values(family, Correction.HOLM, alpha)
