"""Foreign exchange rates, with the provenance that makes a converted figure honest.

This is the rate source four ADRs deferred to. Its shape is decided almost
entirely by one sentence -- ADR-0020's rejected alternative, which is the
sharpest constraint in the whole capability:

> **Introduce a rate source with a fixed or configurable rate.** Rejected. A
> configured rate is an invented one, and a figure derived from it is exactly as
> wrong as the figure being removed, with the added cost of looking
> authoritative.

So every rate here is **supplied** and carries where it came from and when it
was true. There is no default, no fallback of ``1.0``, and no rate AlphaLab
computes for itself. An absent rate is a refusal, exactly as it was before this
module existed -- what changes is that a *present* one can now be used.

What this is not, deliberately
------------------------------

ADR-0020's non-goals are kept, each for a reason that survives having a rate
source:

* **No triangulation.** USD/JPY from EUR/USD and EUR/JPY is a rate no source
  quoted, and it is wrong by both spreads. A caller that wants it states it.
* **No implicit inversion.** EUR/USD at 1.08 does not make USD/EUR 1/1.08 --
  that is the mid-market identity, and a real quote has two sides.
  :meth:`FxRates.with_inverses` will mint them, because making every caller type
  both directions of every pair invites a mistyped one, but it is a *deliberate*
  act and each derived rate says so through :attr:`FxRate.derived`.
* **No caching and no reference-currency hierarchy.** A table is a value. Where
  it comes from and how often it is refreshed belong to whoever supplies it.

Precision
---------

**A conversion produces money, so it rounds like money.**
:mod:`alphalab.portfolio.money` states the contract: every monetary amount is an
exact multiple of the currency's minor unit, and ``to_money`` is the only place
rounding happens. A rate is a *price*, not money -- ``500.00 EUR`` at
``1.087343`` is ``543.6715 USD``, which is not a number of cents -- so
:meth:`FxRates.convert` is that rounding point for this path, exactly as
:func:`~alphalab.portfolio.money.notional` is for a fill.

Rounding happens **per conversion, not on the total**, which is money.py's rule
2 applied unchanged: round once at entry and let everything downstream be exact
addition over exact values. Summing unrounded conversions and rounding the sum
would be the second independent rounding that policy exists to remove.

Without this, a converted ``equity`` came back as ``3839.74880000`` while a
single-currency one came back as ``2100.00`` -- the same field with two shapes
depending on whether a rate was used, and sub-cent digits in a figure a human
reads. Found in the v2.16 acceptance review.

Staleness
---------

A rate has an ``as_of``, and valuing a book at ``T`` with a rate from three days
before ``T`` is a real way to be confidently wrong. :attr:`FxRates.max_age_seconds`
is the tolerance, and a rate older than it is **refused rather than used**.

The default is ``None`` -- no check -- for the same reason
``Governance.approval_required_in`` defaults to empty: AlphaLab does not know
what tolerance a given desk runs to, and a default either way would be an
invented policy presented as an architectural one. What it will not do is use a
stale rate silently.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal

from alphalab.portfolio.exceptions import PortfolioError
from alphalab.portfolio.money import to_money

__all__ = [
    "FxConversion",
    "FxRate",
    "FxRates",
    "MissingRateError",
    "StaleRateError",
]


class MissingRateError(PortfolioError):
    """Raised when a conversion needs a rate the table does not hold.

    Distinct from :class:`~alphalab.portfolio.exceptions.MixedCurrencyValuationError`,
    which says *no rates were supplied at all*. This one says rates were
    supplied and this pair is not among them, which is a different thing to fix.
    """


class StaleRateError(PortfolioError):
    """Raised when the only rate for a pair is older than the table tolerates."""


@dataclass(frozen=True, slots=True)
class FxRate:
    """One quoted rate: ``1 base`` buys ``rate`` of ``quote``, as of an instant.

    Attributes:
        base: The currency being converted **from**.
        quote: The currency being converted **to**.
        rate: How much ``quote`` one unit of ``base`` is worth. Must be
            positive: a zero or negative exchange rate is not a quote.
        as_of: When this rate was true, in Unix seconds.
        source: Where it came from. Free-form and never interpreted, but
            required -- an unattributed rate is the "configured rate" ADR-0020
            refuses, and a figure derived from one looks exactly as
            authoritative as a real one.
        derived: Whether this rate was computed rather than quoted. Set only by
            :meth:`FxRates.with_inverses`. A converted figure that used a
            derived rate is still honest, but it is a different claim and says
            so.
    """

    base: str
    quote: str
    rate: Decimal
    as_of: float
    source: str
    derived: bool = False

    def __post_init__(self) -> None:
        if not self.base.strip() or not self.quote.strip():
            raise PortfolioError("An FX rate names both currencies.")
        if self.base == self.quote:
            raise PortfolioError(
                f"{self.base} to {self.quote} is not a conversion. A currency is "
                "worth one of itself, and stating it as a rate invites a table "
                "where it is not."
            )
        if self.rate <= Decimal("0"):
            raise PortfolioError(
                f"{self.base}/{self.quote} is quoted at {self.rate}, which is not an "
                "exchange rate. A non-positive rate would make a long position value "
                "negative without anything refusing it."
            )
        if not self.source.strip():
            raise PortfolioError(
                f"{self.base}/{self.quote} names no source. A rate with no provenance "
                "is the configured rate ADR-0020 refuses: a figure derived from it "
                "looks exactly as authoritative as one derived from a real quote."
            )

    @property
    def pair(self) -> tuple[str, str]:
        """The ``(base, quote)`` this rate converts."""

        return (self.base, self.quote)

    def inverse(self) -> FxRate:
        """The opposite direction, marked as derived.

        Arithmetic, not a quote: a real market has two sides and the true
        inverse differs from ``1/rate`` by the spread.
        """

        return FxRate(
            base=self.quote,
            quote=self.base,
            rate=Decimal("1") / self.rate,
            as_of=self.as_of,
            source=self.source,
            derived=True,
        )


@dataclass(frozen=True, slots=True)
class FxConversion:
    """One conversion a valuation performed, and the rate it used.

    Carried on the valuation so a converted figure is attributable. A number in
    a currency the book is not in, with no statement of how it got there, is the
    thing ADR-0020 removed; this is what stops it coming back in a different
    shape.
    """

    amount: Decimal
    converted: Decimal
    rate: FxRate

    @property
    def rounding(self) -> Decimal:
        """What rounding to the minor unit cost this conversion.

        ``converted - amount * rate``. Never more than half a cent, and kept so
        a reconciliation can account for it rather than discover it.
        """

        return self.converted - self.amount * self.rate.rate

    @property
    def summary(self) -> str:
        """Human-readable, for a report or a refusal message."""

        derived = " (derived)" if self.rate.derived else ""
        return (
            f"{self.amount} {self.rate.base} -> {self.converted} {self.rate.quote} "
            f"at {self.rate.rate} from {self.rate.source!r}{derived}"
        )


@dataclass(frozen=True, slots=True)
class FxRates:
    """An immutable table of supplied rates.

    Value semantics, like every other state in AlphaLab: :meth:`with_rate` and
    :meth:`with_inverses` return new tables.

    Attributes:
        rates: ``(base, quote)`` -> the rate for that pair. One rate per pair:
            a table cannot hold two answers to the same question.
        max_age_seconds: How stale a rate may be at the instant it is used.
            ``None`` disables the check -- see the module docstring for why that
            is the default and not a number AlphaLab chose.
    """

    rates: Mapping[tuple[str, str], FxRate] = field(default_factory=dict)
    max_age_seconds: float | None = None

    @classmethod
    def of(cls, quoted: Iterable[FxRate], max_age_seconds: float | None = None) -> FxRates:
        """Build a table from quoted rates.

        Raises:
            PortfolioError: If two rates quote the same pair. Which one is right
                is not a question this table will answer by picking.
        """

        table: dict[tuple[str, str], FxRate] = {}
        for rate in quoted:
            if rate.pair in table:
                raise PortfolioError(
                    f"Two rates quote {rate.base}/{rate.quote}: "
                    f"{table[rate.pair].rate} from {table[rate.pair].source!r} and "
                    f"{rate.rate} from {rate.source!r}. Supply one."
                )
            table[rate.pair] = rate
        return cls(table, max_age_seconds)

    def with_rate(self, rate: FxRate) -> FxRates:
        """A table with ``rate`` added or replacing the one for its pair."""

        return replace(self, rates={**self.rates, rate.pair: rate})

    def with_inverses(self) -> FxRates:
        """A table that also holds the derived opposite of every quoted rate.

        A deliberate act, and each minted rate carries ``derived=True``. An
        existing quote for a pair is never replaced by a derived one: a real
        quote beats arithmetic.
        """

        table = dict(self.rates)
        for rate in self.rates.values():
            inverse = rate.inverse()
            if inverse.pair not in table:
                table[inverse.pair] = inverse
        return replace(self, rates=table)

    def __bool__(self) -> bool:
        """Whether this table holds any rate at all.

        ``False`` means *no rates were supplied*, which is what makes a mixed
        book unvaluable -- a different answer from "this pair is missing".
        """

        return bool(self.rates)

    def __len__(self) -> int:
        return len(self.rates)

    @property
    def pairs(self) -> tuple[tuple[str, str], ...]:
        """Every pair this table can convert, sorted."""

        return tuple(sorted(self.rates))

    def rate_for(self, base: str, quote: str) -> FxRate | None:
        """The rate for one pair, or ``None``. No triangulation, no inversion."""

        return self.rates.get((base, quote))

    def convert(
        self, amount: Decimal, base: str, quote: str, as_of: float | None = None
    ) -> FxConversion:
        """Convert ``amount`` from ``base`` to ``quote``, and say how.

        A conversion to the same currency is the identity and needs no rate --
        it is not a conversion, and requiring a ``USD/USD`` entry would make
        every table carry a row that means nothing.

        The result is rounded to the currency's minor unit exactly once, here.

        Args:
            amount: The figure to convert.
            base: What it is denominated in.
            quote: What it should be expressed in.
            as_of: The instant the conversion is for, against which staleness is
                measured. ``None`` skips the check even when a tolerance is set,
                because a caller that names no instant is not claiming one.

        Raises:
            MissingRateError: If no rate is held for the pair.
            StaleRateError: If the rate is older than :attr:`max_age_seconds`.
        """

        if base == quote:
            return FxConversion(
                amount,
                to_money(amount),
                FxRate(base, f"{quote}*", Decimal("1"), 0.0, "identity"),
            )

        rate = self.rate_for(base, quote)
        if rate is None:
            raise MissingRateError(
                f"No rate for {base}/{quote}. This table holds {list(self.pairs)}. "
                "AlphaLab does not triangulate or invert a rate it was not given: "
                "supply the pair, or call with_inverses() if the opposite direction "
                "is an acceptable derivation."
            )

        if as_of is not None and self.max_age_seconds is not None:
            age = as_of - rate.as_of
            if age > self.max_age_seconds:
                raise StaleRateError(
                    f"The only {base}/{quote} rate is {age:.0f}s old at {as_of} and this "
                    f"table tolerates {self.max_age_seconds:.0f}s. It came from "
                    f"{rate.source!r} as of {rate.as_of}. A stale rate is refused rather "
                    "than used, because a figure derived from one is confidently wrong."
                )

        # The rounding point for this path. See "Precision" in the module
        # docstring: a rate is a price, and a converted amount is money.
        return FxConversion(amount, to_money(amount * rate.rate), rate)


#: A table holding nothing. The honest state of a run nobody supplied rates to,
#: and what every valuation defaults to -- so behaviour is unchanged for the
#: single-currency books that are every book AlphaLab was used for before v2.16.
NO_RATES = FxRates()
