"""Reading a scalar series out of canonical records, and refusing what is not there.

A feature computation needs one number per observation. A canonical wire record
carries several, and which of them it carries depends on what kind of record it
is: a :class:`~alphalab.data.feed.Bar` has a close and a volume, a
:class:`~alphalab.data.feed.Quote` has a bid and an ask, a
:class:`~alphalab.data.feed.FundamentalRecord` has one unnamed metric value and
nothing else. :data:`FIELD_READERS` is the complete table of which record type
can answer which :class:`~alphalab.factor_library.definition.FeatureField`, and
a field a record does not carry is **refused** rather than read as zero.

That refusal is the whole point of this module, and it is what Phase 13 of the
v3.2 roadmap asks for in practice: a ``VOLUME`` feature on an FX series is not
a feature that happens to be zero, it is a feature that does not apply, and the
error says so by name. See :mod:`alphalab.factor_library.applicability` for the
same question asked one level up, per asset class rather than per record.

Not a second price series
-------------------------

:class:`~alphalab.factor_library.inputs.PriceSeries` already exists and is not
replaced. It holds *domain bars* -- ``Decimal`` prices keyed by ``asset_id`` --
for one asset, and it is what the six style factors
(:func:`~alphalab.factor_library.momentum.compute_momentum` and its siblings)
consume. An :class:`ObservationFrame` holds *one extracted float per
observation*, for many assets at once, which is the shape a panel computation
and a cross-section need and which a ``PriceSeries`` has no room for. They are
different shapes for different questions, and
``tests/regression/test_shared_names_stay_distinct.py`` records that they stay
apart. :func:`observations_from_price_series` is the bridge, so a caller
holding the existing type never has to rebuild one.

Duplicates are ambiguity, and ambiguity is refused
--------------------------------------------------

Two records for one symbol at one timestamp make "the previous observation"
undefined, and a trailing window over them would silently measure whichever
order the file happened to be in. :func:`observations_from_records` refuses,
naming the symbol and the instant, and points at the cleaning policy that
resolves it -- ``alphalab.data`` already has one, and inventing a second rule
here would put the decision in two places.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from alphalab.data.dataset import Dataset
from alphalab.data.feed import (
    AlternativeDataRecord,
    Bar,
    CanonicalRecord,
    Dividend,
    EconomicEvent,
    FundamentalRecord,
    OrderBook,
    Quote,
    Split,
    Trade,
)
from alphalab.factor_library.definition import FeatureField
from alphalab.factor_library.exceptions import FactorInputError
from alphalab.factor_library.inputs import PriceSeries

__all__ = [
    "FIELD_READERS",
    "ObservationFrame",
    "ObservationSeries",
    "observations_from_dataset",
    "observations_from_price_series",
    "observations_from_records",
]


def _mid(book: OrderBook) -> float:
    if not book.bids or not book.asks:
        raise FactorInputError(
            f"{book.symbol} has a one-sided book at {book.timestamp!r}, so it has no mid."
        )
    return (book.bids[0].price + book.asks[0].price) / 2.0


#: Which record type can answer which field, and how.
#:
#: Read as ``FIELD_READERS[type(record)][field]``. A record type absent from
#: the table, or a field absent from its entry, is refused by
#: :func:`observations_from_records` with a message naming both -- which is how
#: "this feature does not apply to this data" reaches a caller as a sentence
#: rather than as a zero.
FIELD_READERS: Final[Mapping[type, Mapping[FeatureField, Callable[..., float]]]] = {
    Bar: {
        FeatureField.OPEN: lambda record: float(record.open),
        FeatureField.HIGH: lambda record: float(record.high),
        FeatureField.LOW: lambda record: float(record.low),
        FeatureField.CLOSE: lambda record: float(record.close),
        FeatureField.VOLUME: lambda record: float(record.volume),
    },
    Trade: {
        FeatureField.PRICE: lambda record: float(record.price),
        FeatureField.SIZE: lambda record: float(record.size),
    },
    Quote: {
        FeatureField.BID: lambda record: float(record.bid),
        FeatureField.ASK: lambda record: float(record.ask),
        FeatureField.MID: lambda record: (float(record.bid) + float(record.ask)) / 2.0,
    },
    OrderBook: {
        FeatureField.BID: lambda record: (
            float(record.bids[0].price) if record.bids else _raise_empty(record, "bid")
        ),
        FeatureField.ASK: lambda record: (
            float(record.asks[0].price) if record.asks else _raise_empty(record, "ask")
        ),
        FeatureField.MID: _mid,
    },
    FundamentalRecord: {FeatureField.OBSERVATION: lambda record: float(record.metric_value)},
    EconomicEvent: {FeatureField.OBSERVATION: lambda record: float(record.actual)},
    AlternativeDataRecord: {FeatureField.OBSERVATION: lambda record: float(record.sentiment_score)},
    Dividend: {FeatureField.OBSERVATION: lambda record: float(record.amount)},
    Split: {FeatureField.OBSERVATION: lambda record: float(record.ratio)},
}


def _raise_empty(book: OrderBook, side: str) -> float:
    raise FactorInputError(
        f"{book.symbol} has no {side} level at {book.timestamp!r}, so that side has no price."
    )


@dataclass(frozen=True, slots=True)
class ObservationSeries:
    """One asset's scalar observations, in chronological order.

    Attributes:
        symbol: The provider symbol the records were keyed by. Deliberately
            *not* an ``asset_id``: research runs on the data as ingested, and
            resolving an identity is the execution path's job through
            :mod:`alphalab.market.normalization`.
        timestamps: Strictly increasing Unix seconds.
        values: One value per timestamp, in the same order.
    """

    symbol: str
    timestamps: tuple[float, ...]
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.timestamps) != len(self.values):
            raise FactorInputError(
                f"{self.symbol}: {len(self.timestamps)} timestamps and {len(self.values)} "
                "values cannot be paired."
            )

    def __len__(self) -> int:
        return len(self.values)


@dataclass(frozen=True, slots=True)
class ObservationFrame:
    """Every asset's observations of one field, and where they came from.

    Attributes:
        source_field: The field that was read. Carried so a computation cannot
            be handed a frame of volumes while believing it holds closes.
        series: Symbol to that symbol's observations. Sorted insertion is not
            relied upon; callers iterate ``sorted(frame.series)`` where order
            matters to a result.
        timezone_name: The zone the timestamps are reported in, taken from the
            dataset rather than from the machine.
        dataset_version: The exact dataset version these observations were read
            from, or ``None`` when the dataset recorded no provenance. Carried
            into every :class:`~alphalab.factor_library.series.FeatureSeries`
            computed from this frame, which is what makes feature lineage
            exact.
    """

    source_field: FeatureField
    series: Mapping[str, ObservationSeries]
    timezone_name: str
    dataset_version: str | None = None

    @property
    def symbols(self) -> tuple[str, ...]:
        """Every symbol present, in sorted order."""

        return tuple(sorted(self.series))

    @property
    def timestamps(self) -> tuple[float, ...]:
        """Every timestamp any symbol observed, sorted and deduplicated.

        The union rather than the intersection: an asset that did not trade on
        a day is absent from that cross-section, and reporting the intersection
        would silently drop every day on which any one asset was missing.
        """

        return tuple(sorted({stamp for row in self.series.values() for stamp in row.timestamps}))


def observations_from_records(
    records: Sequence[CanonicalRecord],
    source_field: FeatureField,
    timezone_name: str,
    dataset_version: str | None = None,
) -> ObservationFrame:
    """Extract one field from canonical records into a per-symbol frame.

    Records are grouped by symbol and ordered by timestamp. Ordering is a
    *reading* of the data, not a change to it -- nothing is dropped, combined
    or filled -- and it is required because a trailing window over unordered
    observations does not mean anything.

    Raises:
        FactorInputError: If ``records`` is empty; if a record type cannot
            answer ``source_field``; if one symbol carries two record types, so
            that "the series" is ambiguous; or if a symbol has two records at
            the same instant, which leaves the previous observation undefined.
    """

    if not records:
        raise FactorInputError(
            "There are no records to read observations from. An empty frame would make "
            "every feature computed over it vacuously complete."
        )

    grouped: dict[str, list[CanonicalRecord]] = {}
    kinds: dict[str, type] = {}
    for record in records:
        found = kinds.setdefault(record.symbol, type(record))
        if found is not type(record):
            raise FactorInputError(
                f"{record.symbol} carries both {found.__name__} and {type(record).__name__} "
                "records, so which series a feature should read is ambiguous. Select one "
                "record type before computing features over it."
            )
        grouped.setdefault(record.symbol, []).append(record)

    frame: dict[str, ObservationSeries] = {}
    for symbol, rows in grouped.items():
        record_type = kinds[symbol]
        readers = FIELD_READERS.get(record_type)
        if readers is None:
            raise FactorInputError(
                f"{record_type.__name__} carries no numeric observation, so no feature can "
                f"be computed from it. Readable record types are "
                f"{sorted(t.__name__ for t in FIELD_READERS)}."
            )
        reader = readers.get(source_field)
        if reader is None:
            raise FactorInputError(
                f"{record_type.__name__} has no {source_field.name} field, so "
                f"{symbol} cannot answer it. {record_type.__name__} offers "
                f"{sorted(f.name for f in readers)}. A field a record does not carry is not "
                "zero; it does not apply."
            )

        ordered = sorted(rows, key=lambda row: row.timestamp)
        stamps: list[float] = []
        values: list[float] = []
        for row in ordered:
            if stamps and row.timestamp == stamps[-1]:
                raise FactorInputError(
                    f"{symbol} has two {record_type.__name__} records at {row.timestamp!r}. "
                    "Which one precedes the other is undefined, so a trailing window over "
                    "them would measure whichever order the source happened to be in. "
                    "Clean the dataset with a DuplicatePolicy before computing features."
                )
            stamps.append(row.timestamp)
            values.append(reader(row))
        frame[symbol] = ObservationSeries(symbol, tuple(stamps), tuple(values))

    return ObservationFrame(
        source_field=source_field,
        series=frame,
        timezone_name=timezone_name,
        dataset_version=dataset_version,
    )


def observations_from_dataset(dataset: Dataset, source_field: FeatureField) -> ObservationFrame:
    """Read a field out of a canonical dataset, carrying its identity along.

    The frame names ``dataset.dataset_version``, which is ``None`` for a
    dataset built from rows already in memory. That ``None`` travels: a feature
    computed from it reports no dataset lineage, and
    :meth:`~alphalab.factor_library.series.FeatureSeries.require_lineage`
    refuses it where lineage is not optional -- the same rule
    :meth:`~alphalab.data.dataset.Dataset.require_provenance` applies one layer
    down.

    Raises:
        FactorInputError: For every reason :func:`observations_from_records`
            does.
    """

    return observations_from_records(
        dataset.records, source_field, dataset.timezone, dataset.dataset_version
    )


def observations_from_price_series(
    prices: PriceSeries, source_field: FeatureField, timezone_name: str
) -> ObservationFrame:
    """Read a field out of the domain-bar series the style factors consume.

    The bridge from :class:`~alphalab.factor_library.inputs.PriceSeries`, so a
    caller already holding one does not have to rebuild their data in a second
    shape to compute a feature over it. The frame carries no dataset version: a
    ``PriceSeries`` is assembled from domain bars and records no provenance, and
    inventing one here would make an untraceable series look traceable.

    Raises:
        FactorInputError: If ``source_field`` is not one a bar carries.
    """

    readers = FIELD_READERS[Bar]
    reader = readers.get(source_field)
    if reader is None:
        raise FactorInputError(
            f"A bar has no {source_field.name} field; it offers {sorted(f.name for f in readers)}."
        )

    ordered = sorted(prices.bars, key=lambda bar: bar.timestamp)
    series = ObservationSeries(
        symbol=prices.asset_id,
        timestamps=tuple(bar.timestamp for bar in ordered),
        values=tuple(reader(bar) for bar in ordered),
    )
    return ObservationFrame(
        source_field=source_field,
        series={prices.asset_id: series},
        timezone_name=timezone_name,
        dataset_version=None,
    )
