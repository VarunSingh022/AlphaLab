"""AlphaLab Factor Library.

The computation engine. Feature Store owns registration, versioning, metadata
and caching and deliberately computes nothing; this package is the consumer it
was designed for, and its results are shaped to satisfy
`alphalab.feature_store.protocol.FeatureValueProtocol` so they can be written
into Feature Store without Feature Store ever importing this package.

Two layers, one vocabulary
--------------------------

* **Style factors** (v2) -- `compute_momentum` and its five siblings: one
  asset, one instant, one `FactorResult`, computed from a `PriceSeries` of
  domain bars or a `FundamentalSnapshot`.
* **The feature framework** (v3.2) -- a typed `FeatureDefinition` with a
  derived identity, computed over an `ObservationFrame` read from a canonical
  `Dataset` into a `FeatureSeries` per symbol and a `FeaturePanel` across the
  universe, with cross-sectional ranking, neutralization, information
  coefficient, decay, turnover and exposure on top.

They share the `feature_id` vocabulary and the Feature Store seam;
`to_factor_results` converts a series into the `FactorResult` values the older
layer already produces one at a time.

* **Point-in-time inputs** (v3.7) -- `observation_frame`, `event_frame` and
  `fundamental_frame` sample what was *knowable* about each subject at each
  research instant from an `alphalab.alt_data` observation set, as a
  `KnowledgeFrame` whose `ObservationFrame` the feature engine computes over
  unchanged; `fundamental_snapshot_as_of` builds the style factors'
  `FundamentalSnapshot` from point-in-time statements. The package reads
  `alphalab.alt_data` and nothing there reads this package.
"""

from alphalab.factor_library.applicability import (
    FIELD_AVAILABILITY,
    MULTIPLICATIVE_CLASSES,
    RATIO_KINDS,
    Applicability,
    Verdict,
    feature_applicability,
    require_applicable,
)
from alphalab.factor_library.carry import compute_carry
from alphalab.factor_library.catalog import (
    FACTOR_CATALOG,
    FactorCategory,
    FactorSpec,
    RequiredInput,
    get_spec,
)
from alphalab.factor_library.compute import (
    compute_feature,
    compute_features,
    compute_panel,
    to_factor_results,
)
from alphalab.factor_library.decay import DecayProfile, factor_decay
from alphalab.factor_library.definition import (
    FEATURE_KEY_SCHEME,
    KIND_REQUIREMENTS,
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    FeatureScope,
    KindRequirement,
    MissingPolicy,
    canonical_feature_key,
    derive_feature_version,
)
from alphalab.factor_library.exceptions import (
    FactorComputationError,
    FactorInputError,
    FactorLibraryError,
)
from alphalab.factor_library.exposure import ExposureReport, factor_exposure
from alphalab.factor_library.forward_returns import ForwardReturnPanel, forward_returns
from alphalab.factor_library.fundamentals import (
    SnapshotSpecification,
    fundamental_frame,
    fundamental_snapshot_as_of,
)
from alphalab.factor_library.ic import InformationCoefficient, information_coefficient
from alphalab.factor_library.inputs import FundamentalSnapshot, PriceSeries
from alphalab.factor_library.knowledge import (
    KNOWLEDGE_FRAME_SCHEME,
    FrameRatio,
    KnowledgeFrame,
    KnowledgeStep,
    ResearchClock,
    align_prices,
    derive_frame_id,
    divide_frames,
    event_frame,
    observation_frame,
    sample_knowledge,
)
from alphalab.factor_library.liquidity import compute_liquidity
from alphalab.factor_library.momentum import compute_momentum
from alphalab.factor_library.neutralization import (
    NeutralizationReport,
    neutralize_beta,
    neutralize_group,
    neutralize_mean,
)
from alphalab.factor_library.observations import (
    FIELD_READERS,
    ObservationFrame,
    ObservationSeries,
    observations_from_dataset,
    observations_from_price_series,
    observations_from_records,
)
from alphalab.factor_library.panel import FactorTransform, FeaturePanel
from alphalab.factor_library.primitives import compute_cross_section, compute_time_series
from alphalab.factor_library.quality import compute_quality
from alphalab.factor_library.ranking import (
    FactorRanking,
    bucket_panel,
    percentile_rank_panel,
    rank_panel,
)
from alphalab.factor_library.result import FactorResult
from alphalab.factor_library.series import (
    FEATURE_SERIES_KEY_SCHEME,
    FeatureSeries,
    canonical_series_key,
    derive_series_id,
)
from alphalab.factor_library.turnover import (
    FactorTurnover,
    TurnoverConvention,
    factor_turnover,
    weights_from_buckets,
)
from alphalab.factor_library.value import compute_value
from alphalab.factor_library.volatility import compute_volatility

__all__ = [
    "FACTOR_CATALOG",
    "FEATURE_KEY_SCHEME",
    "FEATURE_SERIES_KEY_SCHEME",
    "FIELD_AVAILABILITY",
    "FIELD_READERS",
    "KIND_REQUIREMENTS",
    "KNOWLEDGE_FRAME_SCHEME",
    "MULTIPLICATIVE_CLASSES",
    "RATIO_KINDS",
    "Applicability",
    "DecayProfile",
    "ExposureReport",
    "FactorCategory",
    "FactorComputationError",
    "FactorInputError",
    "FactorLibraryError",
    "FactorRanking",
    "FactorResult",
    "FactorSpec",
    "FactorTransform",
    "FactorTurnover",
    "FeatureDefinition",
    "FeatureField",
    "FeatureKind",
    "FeaturePanel",
    "FeatureScope",
    "FeatureSeries",
    "ForwardReturnPanel",
    "FrameRatio",
    "FundamentalSnapshot",
    "InformationCoefficient",
    "KindRequirement",
    "KnowledgeFrame",
    "KnowledgeStep",
    "MissingPolicy",
    "NeutralizationReport",
    "ObservationFrame",
    "ObservationSeries",
    "PriceSeries",
    "RequiredInput",
    "ResearchClock",
    "SnapshotSpecification",
    "TurnoverConvention",
    "Verdict",
    "align_prices",
    "bucket_panel",
    "canonical_feature_key",
    "canonical_series_key",
    "compute_carry",
    "compute_cross_section",
    "compute_feature",
    "compute_features",
    "compute_liquidity",
    "compute_momentum",
    "compute_panel",
    "compute_quality",
    "compute_time_series",
    "compute_value",
    "compute_volatility",
    "derive_feature_version",
    "derive_frame_id",
    "derive_series_id",
    "divide_frames",
    "event_frame",
    "factor_decay",
    "factor_exposure",
    "factor_turnover",
    "feature_applicability",
    "forward_returns",
    "fundamental_frame",
    "fundamental_snapshot_as_of",
    "get_spec",
    "information_coefficient",
    "neutralize_beta",
    "neutralize_group",
    "neutralize_mean",
    "observation_frame",
    "observations_from_dataset",
    "observations_from_price_series",
    "observations_from_records",
    "percentile_rank_panel",
    "rank_panel",
    "require_applicable",
    "sample_knowledge",
    "to_factor_results",
    "weights_from_buckets",
]
