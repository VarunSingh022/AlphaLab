"""AlphaLab Universal Data Engine.

The path from a raw source to research data, and the one owner of each step:

======================= ====================================================
Source provenance       :mod:`alphalab.data.source`
Delimited reading       :mod:`alphalab.data.csv_source`
Schema detection        :mod:`alphalab.data.schema`
Timestamps, frequency   :mod:`alphalab.data.time`
Validation findings     :mod:`alphalab.data.validation`
Cleaning and policy     :mod:`alphalab.data.cleaning`
Quality reporting       :mod:`alphalab.data.quality`
Asset-class semantics   :mod:`alphalab.data.assets`
Market calendars        :mod:`alphalab.data.calendar`
Corporate actions       :mod:`alphalab.data.corporate_actions`
Provenance and identity :mod:`alphalab.data.provenance`
The canonical dataset   :mod:`alphalab.data.dataset`
The pipeline            :mod:`alphalab.data.ingestion`
Engine state            :mod:`alphalab.data.engine`
======================= ====================================================

:mod:`alphalab.api` is the application-facing surface and is deliberately
**not** imported here, so that ``import alphalab.data`` pulls in no part of the
execution path. Import it by name.
"""

from alphalab.data.adapter import DataAdapter
from alphalab.data.assets import (
    CommoditySpec,
    CryptoSpec,
    EquitySpec,
    FutureSpec,
    FxSpec,
    IndexSpec,
    InstrumentSpec,
    OptionSpec,
    RateSpec,
    asset_class_of,
)
from alphalab.data.calendar import CONTINUOUS_SESSION, MarketCalendar, SessionWindow
from alphalab.data.catalog import CatalogRecord
from alphalab.data.cleaning import (
    REFUSE_EVERYTHING,
    CleaningOutcome,
    CleaningPolicy,
    DuplicatePolicy,
    InvalidRecordPolicy,
    MissingValuePolicy,
    OrderingPolicy,
    TransformationRecord,
    clean_records,
    is_internally_consistent,
    remove_duplicates,
    remove_invalid_ohlc,
)
from alphalab.data.conversion import resample_bars
from alphalab.data.corporate_actions import (
    AdjustmentOutcome,
    AdjustmentRecord,
    Delisting,
    InstrumentLifecycleEvent,
    Merger,
    PriceBasis,
    SymbolChange,
    apply_adjustments,
)
from alphalab.data.csv_source import (
    DELIMITER_CANDIDATES,
    CsvDialect,
    MalformedRow,
    RawRow,
    RawTable,
    decode_text,
    detect_delimiter,
    read_delimited,
)
from alphalab.data.dataset import Dataset
from alphalab.data.engine import UniversalDataEngine
from alphalab.data.events import (
    DataEvent,
    DatasetCataloged,
    DatasetCleaned,
    DatasetIngested,
    DatasetResampled,
    QualityReportGenerated,
)
from alphalab.data.exceptions import (
    DataQualityError,
    DataValidationError,
    InvalidDataStateError,
    UniversalDataError,
)
from alphalab.data.feed import (
    AlternativeDataRecord,
    Bar,
    CanonicalRecord,
    CorporateAction,
    Dividend,
    EconomicEvent,
    FundamentalRecord,
    OrderBook,
    OrderBookLevel,
    Quote,
    Split,
    Trade,
)
from alphalab.data.formats import COLUMN_ALIASES, canonical_field
from alphalab.data.ingestion import (
    IngestionRequest,
    IngestionResult,
    ingest_table,
    parse_and_load,
)
from alphalab.data.loader import create_dataset
from alphalab.data.manager import DataManager, validate_dataset_ingestion
from alphalab.data.metadata import DatasetMetadata
from alphalab.data.normalization import normalize_prices
from alphalab.data.parser import parse_raw_rows
from alphalab.data.protocol import DataExtractorProtocol
from alphalab.data.provenance import (
    DATASET_KEY_SCHEME,
    DatasetProvenance,
    canonical_dataset_key,
    derive_dataset_version,
    derive_transformed_version,
)
from alphalab.data.quality import (
    DataQualityReport,
    QualityReport,
    evaluate_bar_quality,
    evaluate_quality,
)
from alphalab.data.registry import DatasetRegistry
from alphalab.data.schema import (
    AmbiguousField,
    DatasetSchema,
    FieldBinding,
    FieldRole,
    RecordType,
    SchemaDetection,
    detect_schema,
)
from alphalab.data.source import (
    CONTENT_DIGEST_SCHEME,
    RawSource,
    SourceKind,
    content_digest,
    raw_source_from_bytes,
    raw_source_from_path,
)
from alphalab.data.state import UniversalDataState
from alphalab.data.symbols import DataAssetClass
from alphalab.data.time import (
    DateOnlyPolicy,
    TimeFrequency,
    TimestampFormat,
    detect_timestamp_format,
    frequency_seconds,
    infer_frequency,
    parse_timestamp,
    resolve_zone,
)
from alphalab.data.validation import (
    FindingKind,
    RowRejection,
    Severity,
    ValidationFinding,
    coerce_row,
    validate_records,
)
from alphalab.data.views import (
    catalog_summary,
    dataset_lineage,
    dataset_provenance,
    dataset_summary,
    metadata_view,
    quality_report,
    schema_report,
)

__all__ = [
    "COLUMN_ALIASES",
    "CONTENT_DIGEST_SCHEME",
    "CONTINUOUS_SESSION",
    "DATASET_KEY_SCHEME",
    "DELIMITER_CANDIDATES",
    "REFUSE_EVERYTHING",
    "AdjustmentOutcome",
    "AdjustmentRecord",
    "AlternativeDataRecord",
    "AmbiguousField",
    "Bar",
    "CanonicalRecord",
    "CatalogRecord",
    "CleaningOutcome",
    "CleaningPolicy",
    "CommoditySpec",
    "CorporateAction",
    "CryptoSpec",
    "CsvDialect",
    "DataAdapter",
    "DataAssetClass",
    "DataEvent",
    "DataExtractorProtocol",
    "DataManager",
    "DataQualityError",
    "DataQualityReport",
    "DataValidationError",
    "Dataset",
    "DatasetCataloged",
    "DatasetCleaned",
    "DatasetIngested",
    "DatasetMetadata",
    "DatasetProvenance",
    "DatasetRegistry",
    "DatasetResampled",
    "DatasetSchema",
    "DateOnlyPolicy",
    "Delisting",
    "Dividend",
    "DuplicatePolicy",
    "EconomicEvent",
    "EquitySpec",
    "FieldBinding",
    "FieldRole",
    "FindingKind",
    "FundamentalRecord",
    "FutureSpec",
    "FxSpec",
    "IndexSpec",
    "IngestionRequest",
    "IngestionResult",
    "InstrumentLifecycleEvent",
    "InstrumentSpec",
    "InvalidDataStateError",
    "InvalidRecordPolicy",
    "MalformedRow",
    "MarketCalendar",
    "Merger",
    "MissingValuePolicy",
    "OptionSpec",
    "OrderBook",
    "OrderBookLevel",
    "OrderingPolicy",
    "PriceBasis",
    "QualityReport",
    "QualityReportGenerated",
    "Quote",
    "RateSpec",
    "RawRow",
    "RawSource",
    "RawTable",
    "RecordType",
    "RowRejection",
    "SchemaDetection",
    "SessionWindow",
    "Severity",
    "SourceKind",
    "Split",
    "SymbolChange",
    "TimeFrequency",
    "TimestampFormat",
    "Trade",
    "TransformationRecord",
    "UniversalDataEngine",
    "UniversalDataError",
    "UniversalDataState",
    "ValidationFinding",
    "apply_adjustments",
    "asset_class_of",
    "canonical_dataset_key",
    "canonical_field",
    "catalog_summary",
    "clean_records",
    "coerce_row",
    "content_digest",
    "create_dataset",
    "dataset_lineage",
    "dataset_provenance",
    "dataset_summary",
    "decode_text",
    "derive_dataset_version",
    "derive_transformed_version",
    "detect_delimiter",
    "detect_schema",
    "detect_timestamp_format",
    "evaluate_bar_quality",
    "evaluate_quality",
    "frequency_seconds",
    "infer_frequency",
    "ingest_table",
    "is_internally_consistent",
    "metadata_view",
    "normalize_prices",
    "parse_and_load",
    "parse_raw_rows",
    "parse_timestamp",
    "quality_report",
    "raw_source_from_bytes",
    "raw_source_from_path",
    "read_delimited",
    "remove_duplicates",
    "remove_invalid_ohlc",
    "resample_bars",
    "resolve_zone",
    "schema_report",
    "validate_dataset_ingestion",
    "validate_records",
]
