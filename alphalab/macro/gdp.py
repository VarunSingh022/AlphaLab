"""GDP growth and nominal-to-real conversion."""

from decimal import Decimal

from alphalab.macro.exceptions import MacroInputError


def gdp_growth_rate(current_gdp: Decimal, prior_gdp: Decimal) -> Decimal:
    """Computes the period-over-period growth rate as a decimal fraction.

    Raises:
        MacroInputError: If prior_gdp is not positive, making the ratio undefined.
    """
    if prior_gdp <= Decimal("0"):
        raise MacroInputError(f"prior_gdp must be positive, got {prior_gdp}.")
    return (current_gdp - prior_gdp) / prior_gdp


def real_gdp(nominal_gdp: Decimal, gdp_deflator_index: Decimal) -> Decimal:
    """Converts nominal GDP to real GDP using a GDP deflator index.

    `gdp_deflator_index` is expressed on the usual base-100 scale, e.g.
    Decimal("110.0") for a deflator indicating 10% cumulative price growth since
    the base period: real_gdp = nominal_gdp / (deflator_index / 100).

    Raises:
        MacroInputError: If gdp_deflator_index is not positive.
    """
    if gdp_deflator_index <= Decimal("0"):
        raise MacroInputError(f"gdp_deflator_index must be positive, got {gdp_deflator_index}.")
    return nominal_gdp / (gdp_deflator_index / Decimal("100"))
