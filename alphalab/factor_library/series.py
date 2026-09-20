"""A computed feature, and the exact data and definition behind it.

A :class:`FeatureSeries` is where the two identities v3.2 keeps separate finally
meet. A :class:`~alphalab.factor_library.definition.FeatureDefinition` has an
identity of its own and deliberately knows nothing about any dataset; a
:class:`~alphalab.data.dataset.Dataset` has an identity of its own and knows
nothing about any feature. The series carries both, plus the symbol it was
computed for, and that triple is what :func:`derive_series_id` hashes.

So the property Phase 3 of the v3.2 roadmap asks for holds by construction: the
same dataset version and the same definition produce the same
:attr:`FeatureSeries.lineage_id`, on any machine, with no shared state; change
the window, the field, a parameter or the data, and the id changes with it.

The identity is over *inputs*, not values
-----------------------------------------

``lineage_id`` answers "which computation is this?", not "what did it come
out as". Hashing the values as well would answer a different and also useful
question -- whether two runs agreed -- but it would stop the id being derivable
*before* the computation runs, which is what lets a cache key, a fold's
provenance and a study's configuration all name a feature they have not
materialized yet. The "did two runs agree" question is answered where it
belongs, on the study result, whose identity *is* content-derived.

Warmup is reported, never hidden
--------------------------------

``observations`` is how many observations were read and ``warmup_periods`` is
how many of them the window consumed before the first value existed, so
``len(series)`` being smaller than ``observations`` is explained by the series
itself rather than left for a caller to work out. A fold that needs ``n`` usable
rows reads these two numbers to know how much history to ask for.
"""

from __future__ import annotations

import hashlib
from bisect import bisect_left
from dataclasses import dataclass
from typing import Final

from alphalab.factor_library.definition import FeatureDefinition
from alphalab.factor_library.exceptions import FactorInputError

__all__ = [
    "FEATURE_SERIES_KEY_SCHEME",
    "FeatureSeries",
    "canonical_series_key",
    "derive_series_id",
]

#: Scheme tag, and the first line of every canonical feature-series key.
FEATURE_SERIES_KEY_SCHEME: Final = "alphalab.feature_series.v1"


def canonical_series_key(
    feature_version: str, dataset_version: str, symbol: str, timezone_name: str
) -> str:
    """Render the canonical key a feature series' identity is derived from.

    Public so the rendering can be pinned by a test and read by anyone auditing
    a feature's lineage, exactly as
    :func:`~alphalab.data.provenance.canonical_dataset_key` is.

    The zone is part of the key because two ingestions of the same bytes under
    different zones are genuinely different data for a calendar feature, and
    the dataset version already reflects that -- including it here keeps the
    two consistent rather than relying on it.
    """

    return "\n".join(
        [
            FEATURE_SERIES_KEY_SCHEME,
            f"feature={feature_version}",
            f"dataset={dataset_version}",
            f"symbol={symbol}",
            f"timezone={timezone_name}",
        ]
    )


def derive_series_id(
    feature_version: str, dataset_version: str, symbol: str, timezone_name: str
) -> str:
    """SHA-256 over :func:`canonical_series_key`. Derived, never minted."""

    key = canonical_series_key(feature_version, dataset_version, symbol, timezone_name)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class FeatureSeries:
    """One feature, computed for one symbol, over one dataset version.

    Attributes:
        definition: The computation that produced it, identity and all.
        symbol: The symbol it was computed for.
        timestamps: The instants a value exists at, strictly increasing. Under
            :attr:`~alphalab.factor_library.definition.MissingPolicy.SKIP` the
            warmup instants are simply absent, which is why this is stored
            rather than derived from the source frame.
        values: One value per timestamp, in the same order.
        dataset_version: The exact dataset version the observations came from,
            or ``None`` when the dataset recorded no provenance.
        timezone_name: The zone the timestamps are reported in.
        observations: How many observations were read, including the ones the
            warmup consumed.
        warmup_periods: How many leading observations produced no value.
    """

    definition: FeatureDefinition
    symbol: str
    timestamps: tuple[float, ...]
    values: tuple[float, ...]
    dataset_version: str | None
    timezone_name: str
    observations: int
    warmup_periods: int

    def __post_init__(self) -> None:
        if len(self.timestamps) != len(self.values):
            raise FactorInputError(
                f"{self.symbol}/{self.definition.feature_id}: {len(self.timestamps)} "
                f"timestamps and {len(self.values)} values cannot be paired."
            )

    def __len__(self) -> int:
        return len(self.values)

    @property
    def feature_version(self) -> str:
        """The definition's derived identity."""

        return self.definition.feature_version

    @property
    def is_traceable(self) -> bool:
        """Whether this series names the data it was computed from."""

        return self.dataset_version is not None

    @property
    def lineage_id(self) -> str | None:
        """The derived identity of ``(dataset, definition, symbol, zone)``.

        ``None`` when the dataset recorded no provenance, following
        :attr:`~alphalab.data.dataset.Dataset.dataset_version`: there is no
        content to derive an identity from, and minting one would make an
        untraceable series indistinguishable from a traceable one.
        """

        if self.dataset_version is None:
            return None
        return derive_series_id(
            self.feature_version, self.dataset_version, self.symbol, self.timezone_name
        )

    def require_lineage(self) -> str:
        """Return this series' lineage id, or refuse.

        What a caller uses when lineage is not optional -- a study result, a
        piece of evidence, anything auditable. The same contract
        :meth:`~alphalab.data.dataset.Dataset.require_provenance` offers one
        layer down, and the refusal names the path that produces a traceable
        series.

        Raises:
            FactorInputError: If the source dataset recorded no provenance.
        """

        lineage = self.lineage_id
        if lineage is None:
            raise FactorInputError(
                f"The series for {self.definition.feature_id!r} on {self.symbol!r} was "
                "computed from a dataset that carries no provenance, so it cannot name the "
                "data behind it. Ingest through alphalab.api.ingest_csv or ingest_rows with "
                "a RawSource to produce a dataset whose lineage is recorded."
            )
        return lineage

    def value_at(self, timestamp: float) -> float | None:
        """The value at ``timestamp``, or ``None`` if the series has none there.

        ``None`` is the honest answer for an instant inside the warmup or one
        the symbol did not trade at. A caller that must distinguish the two
        reads :attr:`warmup_periods`.

        Binary search rather than a scan: ``timestamps`` is strictly
        increasing, and a linear lookup here would make building a panel over
        ``s`` symbols and ``t`` instants quadratic in ``t``. See
        :mod:`alphalab.factor_library.panel`, which indexes once instead of
        calling this per cell.
        """

        position = bisect_left(self.timestamps, timestamp)
        if position < len(self.timestamps) and self.timestamps[position] == timestamp:
            return self.values[position]
        return None
