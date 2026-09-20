"""The thing a factor is measured against, and the one quantity that looks ahead.

Every diagnostic in v3.2 -- the information coefficient, decay, hit rate, a
quantile spread -- asks the same question: did the factor at instant ``t``
predict what happened *after* ``t``? Answering it requires a quantity computed
from data the factor was not allowed to see. That is not a leak; it is the
label, and the whole point of the exercise.

It becomes a leak the moment it is mistaken for a feature. So a forward return
is not a :class:`~alphalab.factor_library.panel.FeaturePanel` and cannot be
turned into one: it is a :class:`ForwardReturnPanel`, a separate type that
names its horizon, and no function in this package accepts one where a feature
is expected. The type system is doing the work that a naming convention would
not.

Horizons are periods, not seconds
---------------------------------

A horizon of 5 means five *observations* of that symbol's own series, not five
days. Two symbols trading on different calendars therefore get five of their
own bars each, which is what a cross-sectional comparison needs -- a horizon in
seconds would give a symbol that trades every day five bars and a symbol that
trades weekly less than one, and the cross-section would be comparing different
things.

The last ``horizon`` observations of every symbol have no forward return and
are simply absent, which is what makes
:attr:`ForwardReturnPanel.unrealized_instants` worth reporting: a study run on
data that ends today measures nothing about its final ``horizon`` instants, and
a diagnostic that quietly ignored that would overstate its sample.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from alphalab.factor_library.exceptions import FactorComputationError, FactorInputError
from alphalab.factor_library.observations import ObservationFrame

__all__ = ["ForwardReturnPanel", "forward_returns"]


@dataclass(frozen=True, slots=True)
class ForwardReturnPanel:
    """Realized forward returns at one horizon, indexed by instant.

    Attributes:
        horizon: How many observations ahead the return looks, in periods.
        rows: Instant to ``{symbol: forward return}``. An instant is present
            only for the symbols whose series runs at least ``horizon``
            observations past it.
        dataset_version: The dataset version the prices were read from, or
            ``None`` when the dataset recorded no provenance.
        timezone_name: The zone the timestamps are reported in.
        symbols: Every symbol that produced at least one forward return.
        unrealized_instants: How many instants were dropped because the series
            ended before the horizon did. The tail of the sample nothing is
            known about.
    """

    horizon: int
    rows: Mapping[float, Mapping[str, float]]
    dataset_version: str | None
    timezone_name: str
    symbols: tuple[str, ...]
    unrealized_instants: int

    @property
    def timestamps(self) -> tuple[float, ...]:
        """Every instant a forward return exists at, sorted."""

        return tuple(sorted(self.rows))

    def cross_section(self, timestamp: float) -> Mapping[str, float]:
        """The symbols with a realized forward return at ``timestamp``."""

        return self.rows.get(timestamp, {})

    def __len__(self) -> int:
        return len(self.rows)


def forward_returns(frame: ObservationFrame, horizon: int) -> ForwardReturnPanel:
    """Compute realized forward returns over ``horizon`` observations.

    The return at instant ``t`` for a symbol is ``v[t + horizon] / v[t] - 1``
    using that symbol's own observations, so it is defined for every instant
    except the final ``horizon`` of each series.

    ``frame`` must hold a price-like field. Nothing here checks that a volume
    is not a price -- it cannot, since a fundamental observation is a perfectly
    good thing to compute a growth rate of -- but the value is required to be
    positive, because a return off a non-positive base is undefined rather than
    zero.

    Raises:
        FactorInputError: If ``horizon`` is not positive, or if no symbol in
            the frame has enough observations to realize a single one.
        FactorComputationError: If a base value is not positive.
    """

    if horizon < 1:
        raise FactorInputError(
            f"A forward horizon must be at least 1 observation, got {horizon}. A horizon of "
            "zero is the present, which a factor is allowed to see and therefore cannot "
            "be measured against."
        )

    rows: dict[float, dict[str, float]] = {}
    present: set[str] = set()
    unrealized = 0

    for symbol in frame.symbols:
        row = frame.series[symbol]
        count = len(row)
        unrealized += min(horizon, count)
        for index in range(count - horizon):
            base = row.values[index]
            if base <= 0.0:
                raise FactorComputationError(
                    f"A {horizon}-period forward return is undefined for {symbol} at "
                    f"{row.timestamps[index]!r}: the base value is {base!r}."
                )
            rows.setdefault(row.timestamps[index], {})[symbol] = (
                row.values[index + horizon] / base - 1.0
            )
            present.add(symbol)

    if not rows:
        raise FactorInputError(
            f"No symbol in the frame has more than {horizon} observation(s), so no forward "
            "return at that horizon has been realized. Either the horizon is longer than "
            "the sample or the sample is too short to study."
        )

    return ForwardReturnPanel(
        horizon=horizon,
        rows={stamp: dict(row) for stamp, row in sorted(rows.items())},
        dataset_version=frame.dataset_version,
        timezone_name=frame.timezone_name,
        symbols=tuple(sorted(present)),
        unrealized_instants=unrealized,
    )
