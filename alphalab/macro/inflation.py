"""Real interest rate calculations via the Fisher equation.

Both the exact and the commonly used linear approximation are provided, because
conflating them is a frequent, real source of small but avoidable error --
"real rate = nominal minus inflation" is the approximation most people know, not
the exact relationship.
"""

from decimal import Decimal

from alphalab.macro.exceptions import MacroInputError


def real_interest_rate_exact(nominal_rate: Decimal, inflation_rate: Decimal) -> Decimal:
    """Computes the real interest rate using the exact Fisher equation.

    (1 + nominal) = (1 + real)(1 + inflation)  =>  real = (1+nominal)/(1+inflation) - 1

    Raises:
        MacroInputError: If (1 + inflation_rate) is zero, making the ratio undefined.
    """
    denominator = Decimal("1") + inflation_rate
    if denominator == Decimal("0"):
        raise MacroInputError("1 + inflation_rate cannot be zero.")
    return (Decimal("1") + nominal_rate) / denominator - Decimal("1")


def real_interest_rate_approx(nominal_rate: Decimal, inflation_rate: Decimal) -> Decimal:
    """Computes the real interest rate using the common linear approximation.

    real ≈ nominal - inflation

    Accurate to within a small error for low rates, but increasingly diverges from
    `real_interest_rate_exact` as either rate grows -- prefer the exact form unless
    matching a specific approximate convention already in use elsewhere.
    """
    return nominal_rate - inflation_rate
