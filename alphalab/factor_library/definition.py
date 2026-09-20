"""What a feature *is*, stated so that two runs can agree they computed it.

A :class:`FeatureDefinition` is the whole specification of one computation: the
field it reads, the window it reads over, the parameters it takes, what it does
when a value is missing, and whether it is a time-series or a cross-sectional
quantity. Nothing about it is implied. That is the difference between this and
:class:`~alphalab.feature_store.metadata.FeatureMetadata`, which is the
*catalogue record* for a registered feature -- its owner, its description, its
lineage tags -- and which deliberately says nothing about how to compute
anything. The two are not duplicates and neither replaces the other: Feature
Store still owns registration and versioning, and this module owns the
computational contract those registrations describe.

Identity is derived, exactly as a dataset's is
----------------------------------------------

:func:`derive_feature_version` renders a canonical key and hashes it, following
:func:`~alphalab.data.provenance.derive_dataset_version` line for line -- a
scheme tag on the first line, ``label=value`` lines joined by newlines, a fixed
field order for the closed fields and a sorted order for the open mapping,
SHA-256 over the result. The same reasons apply: two processes that define the
same feature the same way arrive at the same identity with no shared state, and
changing a window changes the identity rather than quietly reusing it.

What is deliberately *not* in a feature's identity: the dataset it will be
computed over, and the moment it was defined. A definition is reusable across
datasets, which is the point of having one, so binding a dataset into it would
make "the same feature on two universes" two features. The join between a
definition and the data it ran on is
:class:`~alphalab.factor_library.series.FeatureSeries`, which carries both
identities, and that is where lineage lives.

Missing values are never invented
---------------------------------

:class:`MissingPolicy` offers two members and neither of them fills anything.
This is ADR-0036's rule applied one layer up: ``alphalab.data`` has no way to
fill a missing price because the absence is structural, and a feature layer
that forward-filled would reintroduce exactly the bias the data layer refuses
to create -- a forward fill is a statement that yesterday's value was still
true today, which is a claim about the world and not an arithmetic convenience.
So a feature either refuses the gap or omits the observation, and the caller
chooses which in advance.

Why the requirements table lives here
-------------------------------------

:data:`KIND_REQUIREMENTS` is read by ``__post_init__`` to validate a definition
and by :attr:`FeatureDefinition.warmup_periods` to report the boundary a split
needs. Keeping it beside the enum rather than beside the implementations is
what makes ``definition -> primitives`` a one-way edge; a table in
:mod:`~alphalab.factor_library.primitives` would have to be imported back here
and close a module-level cycle, which
``tests/regression/test_import_graph_stays_acyclic.py`` measures on every run.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, auto
from types import MappingProxyType
from typing import Final

from alphalab.factor_library.exceptions import FactorInputError

__all__ = [
    "FEATURE_KEY_SCHEME",
    "KIND_REQUIREMENTS",
    "FeatureDefinition",
    "FeatureField",
    "FeatureKind",
    "FeatureScope",
    "KindRequirement",
    "MissingPolicy",
    "canonical_feature_key",
    "derive_feature_version",
]

#: Scheme tag, and the first line of every canonical feature key.
#:
#: Frozen for the life of the scheme, as ``DATASET_KEY_SCHEME`` is. Changing it
#: changes every feature identity in existence and requires an ADR.
FEATURE_KEY_SCHEME: Final = "alphalab.feature.v1"


class FeatureField(Enum):
    """The observation field a feature reads.

    Named rather than free-form: a string field name would let a typo become a
    feature that computes over nothing, and the set of fields a canonical wire
    record can offer is closed. Which records can supply which field is decided
    by :mod:`alphalab.factor_library.observations`, and a field a record type
    does not carry is refused there rather than read as zero.
    """

    OPEN = auto()
    HIGH = auto()
    LOW = auto()
    CLOSE = auto()
    VOLUME = auto()
    #: Trade price, for tick data.
    PRICE = auto()
    #: Trade size, for tick data.
    SIZE = auto()
    BID = auto()
    ASK = auto()
    #: ``(bid + ask) / 2`` -- derived by the observation layer, not stored.
    MID = auto()
    #: The value of a fundamental, economic or alternative-data observation.
    OBSERVATION = auto()


class FeatureScope(Enum):
    """Whether a feature looks back in time or across a universe.

    The distinction decides what a computation is even allowed to see.
    ``TIME_SERIES`` reads one asset's own history and nothing else;
    ``CROSS_SECTIONAL`` reads every asset at one instant and no history. A
    feature that quietly did both would be impossible to check for look-ahead,
    because there would be no single window to check.
    """

    TIME_SERIES = auto()
    CROSS_SECTIONAL = auto()


class MissingPolicy(Enum):
    """What a computation does when its window is incomplete.

    Two members, and neither fills. See the module docstring.
    """

    #: Raise. The caller asserted the data is complete and wants to know if not.
    REFUSE = auto()
    #: Emit no point at that timestamp. The series is shorter and says so.
    SKIP = auto()


class FeatureKind(Enum):
    """The computation a definition selects.

    A closed set with one uniform contract, rather than a module of free
    functions each with its own signature. Every kind's scope, window and
    parameters are declared in :data:`KIND_REQUIREMENTS`.
    """

    #: Simple return over ``window`` periods: ``v[t] / v[t - window] - 1``.
    RETURN = auto()
    #: ``log(v[t] / v[t - window])``.
    LOG_RETURN = auto()
    #: Arithmetic mean over the trailing window. The simple moving average.
    ROLLING_MEAN = auto()
    #: Unbiased sample standard deviation over the trailing window.
    ROLLING_STD = auto()
    #: Smallest value in the trailing window.
    ROLLING_MIN = auto()
    #: Largest value in the trailing window.
    ROLLING_MAX = auto()
    #: Sum over the trailing window.
    ROLLING_SUM = auto()
    #: Exponentially weighted mean, seeded with the first full window's mean.
    EXPONENTIAL_MEAN = auto()
    #: Annualized standard deviation of one-period simple returns.
    REALIZED_VOLATILITY = auto()
    #: Trailing total return, optionally skipping the most recent periods.
    MOMENTUM = auto()
    #: Negated distance from the trailing mean, in trailing standard deviations.
    MEAN_REVERSION = auto()
    #: Distance from the trailing mean, in trailing standard deviations.
    ROLLING_ZSCORE = auto()
    #: Current value divided by its trailing mean. Volume surges, for instance.
    ROLLING_RATIO = auto()
    #: Short-window volatility divided by long-window volatility.
    VOLATILITY_REGIME = auto()
    #: Seconds elapsed since midnight in the dataset's declared zone.
    TIME_OF_DAY = auto()
    #: Day of week in the dataset's declared zone, Monday 0 through Sunday 6.
    DAY_OF_WEEK = auto()
    #: Rank of the value across the universe at one instant.
    CROSS_SECTIONAL_RANK = auto()
    #: Z-score of the value across the universe at one instant.
    CROSS_SECTIONAL_ZSCORE = auto()
    #: Value less the universe mean at one instant.
    CROSS_SECTIONAL_DEMEAN = auto()


@dataclass(frozen=True, slots=True)
class KindRequirement:
    """What one :class:`FeatureKind` needs in order to be computable.

    Attributes:
        scope: Time-series or cross-sectional. Not the caller's to override.
        needs_window: Whether a trailing lookback must be supplied.
        minimum_window: Smallest window that leaves the computation defined --
            2 wherever a dispersion is taken, since a standard deviation over
            one observation is not zero.
        required_parameters: Parameters with no meaningful default, which the
            definition must supply.
        permitted_parameters: Every parameter the kind reads. One it does not
            read is refused rather than ignored: an unread parameter still
            changes the derived feature version, which would give a single
            computation two identities.
        window_offset: Added to ``window`` to get the index of the first
            observation the kind can produce a value for. ``0`` for the
            difference-like kinds, which need ``window`` *earlier*
            observations; ``-1`` for the aggregate kinds, whose window includes
            the current observation.
        reads_calendar: Whether the kind reads a timestamp's civil time rather
            than a value, and so needs the dataset's declared zone.
    """

    scope: FeatureScope
    needs_window: bool
    minimum_window: int = 1
    required_parameters: tuple[str, ...] = ()
    permitted_parameters: tuple[str, ...] = ()
    window_offset: int = 0
    reads_calendar: bool = False


_TS: Final = FeatureScope.TIME_SERIES
_XS: Final = FeatureScope.CROSS_SECTIONAL

#: The complete table. Every :class:`FeatureKind` appears exactly once, and
#: ``test_every_feature_kind_is_implemented`` asserts that it does.
KIND_REQUIREMENTS: Final[Mapping[FeatureKind, KindRequirement]] = MappingProxyType(
    {
        FeatureKind.RETURN: KindRequirement(_TS, True, 1),
        FeatureKind.LOG_RETURN: KindRequirement(_TS, True, 1),
        FeatureKind.ROLLING_MEAN: KindRequirement(_TS, True, 1, window_offset=-1),
        FeatureKind.ROLLING_STD: KindRequirement(_TS, True, 2, window_offset=-1),
        FeatureKind.ROLLING_MIN: KindRequirement(_TS, True, 1, window_offset=-1),
        FeatureKind.ROLLING_MAX: KindRequirement(_TS, True, 1, window_offset=-1),
        FeatureKind.ROLLING_SUM: KindRequirement(_TS, True, 1, window_offset=-1),
        FeatureKind.EXPONENTIAL_MEAN: KindRequirement(_TS, True, 2, window_offset=-1),
        FeatureKind.REALIZED_VOLATILITY: KindRequirement(
            _TS, True, 2, permitted_parameters=("periods_per_year",)
        ),
        FeatureKind.MOMENTUM: KindRequirement(_TS, True, 1, permitted_parameters=("skip_periods",)),
        FeatureKind.MEAN_REVERSION: KindRequirement(_TS, True, 2, window_offset=-1),
        FeatureKind.ROLLING_ZSCORE: KindRequirement(_TS, True, 2, window_offset=-1),
        FeatureKind.ROLLING_RATIO: KindRequirement(_TS, True, 1, window_offset=-1),
        FeatureKind.VOLATILITY_REGIME: KindRequirement(
            _TS,
            True,
            2,
            required_parameters=("long_window",),
            permitted_parameters=("long_window",),
        ),
        FeatureKind.TIME_OF_DAY: KindRequirement(_TS, False, reads_calendar=True),
        FeatureKind.DAY_OF_WEEK: KindRequirement(_TS, False, reads_calendar=True),
        FeatureKind.CROSS_SECTIONAL_RANK: KindRequirement(_XS, False),
        FeatureKind.CROSS_SECTIONAL_ZSCORE: KindRequirement(_XS, False),
        FeatureKind.CROSS_SECTIONAL_DEMEAN: KindRequirement(_XS, False),
    }
)


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    """One reusable, reproducible feature computation.

    Attributes:
        feature_id: What the caller calls this feature. Carried into
            :class:`~alphalab.factor_library.result.FactorResult` and therefore
            into Feature Store, where it must match a registration.
        kind: The computation.
        source_field: The observation field it reads.
        window: Trailing lookback in *periods*, not seconds -- a period is one
            observation of the series, so the same definition means the same
            thing on daily and on minute data. ``None`` for the kinds that take
            no window.
        parameters: Extra numeric parameters the kind reads. Genuinely
            immutable: copied and wrapped in a ``MappingProxyType`` by
            ``__post_init__``, the idiom
            :class:`~alphalab.research.protocol.ResearchPayload` established
            under ADR-0034.
        missing: What to do with an incomplete window.
        scope: Time-series or cross-sectional.

    Raises:
        FactorInputError: If the definition is not computable as stated -- an
            empty or ``@``-bearing id, a declared scope the kind does not have,
            a window the kind requires and was not given, a window the kind
            does not take, a window below the kind's minimum, a missing
            required parameter, an unrecognised parameter, or a volatility
            regime whose long window does not exceed its short one. Every one
            of these would otherwise become a default chosen here, which is the
            thing this repository refuses to do on a caller's behalf.
    """

    feature_id: str
    kind: FeatureKind
    source_field: FeatureField
    window: int | None = None
    parameters: Mapping[str, float] = field(default_factory=dict)
    missing: MissingPolicy = MissingPolicy.SKIP
    scope: FeatureScope = FeatureScope.TIME_SERIES

    def __post_init__(self) -> None:
        # Copy first, then wrap: the proxy alone would stay a live view of the
        # caller's dictionary, and a definition whose parameters could change
        # afterwards would not identify the computation it was hashed from.
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))

        if not self.feature_id.strip():
            raise FactorInputError("A feature definition must be named before it can be used.")
        if "@" in self.feature_id:
            raise FactorInputError(
                f"A feature_id may not contain '@': {self.feature_id!r}. The character "
                "separates the name from the digest in a feature version."
            )

        requirement = KIND_REQUIREMENTS[self.kind]

        if requirement.scope is not self.scope:
            raise FactorInputError(
                f"{self.kind.name} is a {requirement.scope.name} computation and the "
                f"definition declares {self.scope.name}. The scope decides what the "
                "computation is allowed to see, so it is not the caller's to override."
            )

        if requirement.needs_window and self.window is None:
            raise FactorInputError(
                f"{self.kind.name} reads a trailing window and none was given. State the "
                "lookback in periods; there is no default, because a default lookback is a "
                "research decision made by the library."
            )
        if not requirement.needs_window and self.window is not None:
            raise FactorInputError(
                f"{self.kind.name} takes no window and one was given ({self.window}). A "
                "window that is accepted and ignored leaves the caller believing they set it."
            )
        if self.window is not None and self.window < requirement.minimum_window:
            raise FactorInputError(
                f"{self.kind.name} needs a window of at least {requirement.minimum_window}, "
                f"got {self.window}."
            )

        missing_parameters = sorted(set(requirement.required_parameters) - set(self.parameters))
        if missing_parameters:
            raise FactorInputError(
                f"{self.kind.name} requires the parameter(s) {missing_parameters} and the "
                f"definition supplies {sorted(self.parameters)}."
            )
        unknown = sorted(set(self.parameters) - set(requirement.permitted_parameters))
        if unknown:
            raise FactorInputError(
                f"{self.kind.name} does not read the parameter(s) {unknown}; it reads "
                f"{sorted(requirement.permitted_parameters)}. An unread parameter would "
                "still change this feature's derived version, giving one computation two "
                "identities."
            )

        if self.kind is FeatureKind.VOLATILITY_REGIME:
            long_window = int(self.parameters["long_window"])
            if self.window is not None and long_window <= self.window:
                raise FactorInputError(
                    f"A volatility regime needs its long window to exceed its short one, "
                    f"got long_window={long_window} and window={self.window}. Equal windows "
                    "make the ratio 1.0 everywhere, which measures nothing."
                )

    @property
    def feature_version(self) -> str:
        """This definition's derived, reproducible identity."""

        return derive_feature_version(self)

    @property
    def warmup_periods(self) -> int:
        """Leading observations for which this definition produces no value.

        Equivalently, the index of the first observation it *can* produce one
        for. A split boundary reads this to know how much history a fold needs
        before its first usable row, and a leakage test reads it to know which
        positions are legitimately absent.
        """

        if self.window is None:
            return 0
        if self.kind is FeatureKind.VOLATILITY_REGIME:
            return int(self.parameters["long_window"])
        skip = int(self.parameters.get("skip_periods", 0.0))
        return self.window + KIND_REQUIREMENTS[self.kind].window_offset + skip


def canonical_feature_key(definition: FeatureDefinition) -> str:
    """Render the canonical key a feature's version is derived from.

    Field order is fixed and part of the specification, following
    :func:`~alphalab.data.provenance.canonical_dataset_key`. Parameters are
    rendered in sorted order because a mapping has no inherent order, and each
    value with ``repr`` so a float round-trips exactly rather than through a
    formatting that could lose a digit.

    Public so the rendering can be pinned by a test and read by anyone auditing
    a feature version.
    """

    parameters = definition.parameters
    return "\n".join(
        [
            FEATURE_KEY_SCHEME,
            f"feature_id={definition.feature_id}",
            f"kind={definition.kind.name}",
            f"field={definition.source_field.name}",
            f"window={'none' if definition.window is None else definition.window}",
            f"missing={definition.missing.name}",
            f"scope={definition.scope.name}",
            "parameters",
            *(f"{name}={parameters[name]!r}" for name in sorted(parameters)),
        ]
    )


def derive_feature_version(definition: FeatureDefinition) -> str:
    """Derive the immutable identity of one feature definition.

    Returns ``"<feature_id>@<digest>"``, where the digest is the SHA-256 of
    :func:`canonical_feature_key`. The name is carried in front so a version is
    readable in a log and sorts sensibly; the digest is what makes it an
    identity. The full digest is used rather than a prefix, matching
    ``evidence_id`` and ``dataset_version``, so no collision argument is needed.
    """

    key = canonical_feature_key(definition)
    return f"{definition.feature_id}@{hashlib.sha256(key.encode('utf-8')).hexdigest()}"
