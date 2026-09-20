"""The cross-section: one feature, every asset, one instant at a time.

A :class:`FeaturePanel` is a set of :class:`~alphalab.factor_library.series.FeatureSeries`
for the *same* definition, indexed so that "what did every asset look like at
this instant?" is a dictionary lookup rather than a search. Every
cross-sectional operation v3.2 adds -- ranking, neutralization, the information
coefficient, decay, turnover, exposure -- reads one, because each of them is
defined on a cross-section and none of them is defined on a single series.

Ragged is normal, and is not padded
-----------------------------------

Assets do not all trade on the same instants: a holiday differs by venue, a
listing starts late, a series has a gap its cleaning policy dropped. A panel
therefore holds *what exists* and nothing else. A cross-section at an instant
is the assets that have a value there, and it can be smaller than the universe.

Padding it would mean inventing values, which the data layer refuses to do and
which this layer has no better claim to. Reporting only the fully-populated
instants would be worse still, because it silently deletes every date on which
any one asset was missing -- a survivorship filter applied by accident. So the
cross-section carries its own size, and every statistic computed from it
reports the count it was computed over; see
:class:`~alphalab.factor_library.ic.InformationCoefficient`, which refuses to
report a correlation whose sample is below a stated minimum rather than
reporting one nobody should read.

One definition per panel
------------------------

:meth:`FeaturePanel.of` refuses a mixture. Two definitions in one panel would
make ``panel.cross_section(t)`` return values that are not comparable with each
other, and every cross-sectional statistic assumes they are.

Transformations are recorded, not lost
--------------------------------------

Ranking a panel, neutralizing it or standardizing it produces another panel,
and the result is *not* the raw feature any more. :class:`FactorTransform` is
how each step says so, and :meth:`FeaturePanel.derive` appends one -- the same
rule ``alphalab.data`` applies to a dataset, where every applied change is a
``TransformationRecord`` with its reason and both ride into provenance.

The consequence matters for reading a diagnostic: an information coefficient
measured on a beta-neutralized, cross-sectionally ranked factor is a different
number from one measured on the raw factor, and
:attr:`FeaturePanel.lineage` is what lets a result say which of the two it is
rather than leaving a reader to assume.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from alphalab.factor_library.definition import FeatureDefinition
from alphalab.factor_library.exceptions import FactorInputError
from alphalab.factor_library.series import FeatureSeries

__all__ = ["FactorTransform", "FeaturePanel"]


@dataclass(frozen=True, slots=True)
class FactorTransform:
    """One cross-sectional step applied to a panel.

    Attributes:
        operation: What was done, e.g. ``"rank"`` or ``"neutralize_beta"``.
        detail: The parameters it was done with, rendered so that two panels
            transformed differently do not read identically. Deliberately a
            rendered string rather than a mapping: this is a record for a
            reader and for a digest, not a configuration to be re-executed.
    """

    operation: str
    detail: str = ""

    def __str__(self) -> str:
        return self.operation if not self.detail else f"{self.operation}({self.detail})"


@dataclass(frozen=True, slots=True)
class FeaturePanel:
    """One feature across a universe, indexed by instant.

    Attributes:
        definition: The one definition every series in the panel was computed
            from.
        dataset_version: The dataset version every series named, or ``None``
            when they were computed from a dataset with no provenance.
        timezone_name: The zone the timestamps are reported in.
        rows: Timestamp to ``{symbol: value}``. Built once by :meth:`of`.
        symbols: Every symbol present, sorted.
        transforms: Cross-sectional steps applied since the feature was
            computed, in the order they were applied. Empty for a raw panel.
    """

    definition: FeatureDefinition
    dataset_version: str | None
    timezone_name: str
    rows: Mapping[float, Mapping[str, float]]
    symbols: tuple[str, ...]
    transforms: tuple[FactorTransform, ...] = ()

    @staticmethod
    def of(series: Sequence[FeatureSeries]) -> FeaturePanel:
        """Index a set of series into a panel.

        Built by a single pass over each series rather than by looking each
        cell up, so the cost is linear in the number of values rather than
        quadratic in the number of instants.

        Raises:
            FactorInputError: If ``series`` is empty, if two series were
                computed from different definitions, if two carry the same
                symbol, or if they disagree about the dataset version or the
                zone -- a panel assembled from two datasets would produce a
                cross-section whose members were measured on different data,
                and no diagnostic computed from it would mean anything.
        """

        if not series:
            raise FactorInputError(
                "A feature panel needs at least one series. An empty panel would make every "
                "cross-sectional statistic computed over it vacuously true."
            )

        first = series[0]
        for other in series[1:]:
            if other.definition != first.definition:
                raise FactorInputError(
                    f"A panel holds one feature. Got {first.definition.feature_id!r} and "
                    f"{other.definition.feature_id!r}, whose values are not comparable "
                    "within a cross-section."
                )
            if other.dataset_version != first.dataset_version:
                raise FactorInputError(
                    f"A panel's series must come from one dataset version. Got "
                    f"{first.dataset_version!r} and {other.dataset_version!r}."
                )
            if other.timezone_name != first.timezone_name:
                raise FactorInputError(
                    f"A panel's series must share a timezone. Got {first.timezone_name!r} "
                    f"and {other.timezone_name!r}."
                )

        rows: dict[float, dict[str, float]] = {}
        seen: set[str] = set()
        for row in series:
            if row.symbol in seen:
                raise FactorInputError(
                    f"{row.symbol} appears twice in the panel, so its cross-sectional value "
                    "would depend on which series was read last."
                )
            seen.add(row.symbol)
            for stamp, value in zip(row.timestamps, row.values, strict=True):
                rows.setdefault(stamp, {})[row.symbol] = value

        return FeaturePanel(
            definition=first.definition,
            dataset_version=first.dataset_version,
            timezone_name=first.timezone_name,
            rows={stamp: dict(row) for stamp, row in sorted(rows.items())},
            symbols=tuple(sorted(seen)),
        )

    def derive(
        self, rows: Mapping[float, Mapping[str, float]], transform: FactorTransform
    ) -> FeaturePanel:
        """A new panel holding ``rows``, with ``transform`` appended to the chain.

        The one way a transformed panel is built, so that no cross-sectional
        operation can produce a panel that does not say what was done to it.
        The symbols are re-derived from ``rows`` rather than carried over: a
        neutralization can legitimately drop an instant, and claiming a symbol
        the rows no longer hold would make ``symbols`` a promise the panel does
        not keep.
        """

        present = sorted({symbol for row in rows.values() for symbol in row})
        return FeaturePanel(
            definition=self.definition,
            dataset_version=self.dataset_version,
            timezone_name=self.timezone_name,
            rows={stamp: dict(row) for stamp, row in sorted(rows.items())},
            symbols=tuple(present),
            transforms=(*self.transforms, transform),
        )

    @property
    def lineage(self) -> str:
        """The feature version and every transform since, as one readable line.

        What a diagnostic records so that its number can be read against the
        thing it was actually measured on.
        """

        chain = " -> ".join(str(step) for step in self.transforms)
        return (
            self.definition.feature_version
            if not chain
            else f"{self.definition.feature_version} -> {chain}"
        )

    @property
    def timestamps(self) -> tuple[float, ...]:
        """Every instant the panel holds a value at, sorted."""

        return tuple(sorted(self.rows))

    def cross_section(self, timestamp: float) -> Mapping[str, float]:
        """The assets holding a value at ``timestamp``, and their values.

        An empty mapping for an instant the panel does not reach. Callers check
        the size before computing anything over it; see the module docstring.
        """

        return self.rows.get(timestamp, {})

    def __len__(self) -> int:
        return len(self.rows)
