"""The experiment contract: what was asked for, and what came back.

A :class:`ResearchStudy` is the complete statement of a v3.2 research run --
the dataset version, the universe, the features, the signal, the split
methodology, the robustness configuration and the seed -- and it has a derived
identity for the same reason a dataset and a feature do. Two processes that
describe the same experiment arrive at the same ``study_id`` with no shared
state, and changing any part of the description changes it.

A :class:`StudyResult` is what one study produced. Its identity is derived from
the study's identity *and* the numbers it produced, which is the opposite
choice from :attr:`~alphalab.factor_library.series.FeatureSeries.lineage_id`
and deliberately so: a study id answers "which experiment is this?" and must be
knowable before the experiment runs, while a result id answers "did two runs of
it agree?" and can only be known afterwards. Both questions are worth asking
and neither identity can answer the other.

The dataset cannot be switched
------------------------------

:meth:`ResearchStudy.require_dataset` refuses a study that names no dataset
version, and :func:`build_result` refuses to record a result whose dataset
version differs from the study's. Together they close the hole the v3.1 notes
describe under ADR-0017: an identity that hashes a string somebody typed is
only as trustworthy as the typing. Here the study names the version, the
inputs carry the version they were computed from, and the two are compared
rather than assumed equal.

Wall-clock time is recorded and never hashed
--------------------------------------------

``produced_at`` sits on the result as a fact and is absent from both digests,
following :class:`~alphalab.data.provenance.DatasetProvenance`, which records
``retrieved_at`` and excludes it from the dataset version for exactly this
reason: re-running the same study tomorrow must produce the same identity, and
a clock reading is a fact about the run rather than about the experiment.

This is not a second research engine
------------------------------------

:class:`~alphalab.research.engine.ResearchEngine` scores a *completed run's*
returns and trades and is unchanged. A study here describes work that happens
*before* there is a return series to score: it runs from a dataset through
features to diagnostics. The two meet at
:mod:`alphalab.lifecycle.evidence`, where either can be recorded as evidence,
and nowhere else.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

from alphalab.factor_library.definition import FeatureDefinition
from alphalab.research.exceptions import ResearchValidationError
from alphalab.research.splits import SplitReport

__all__ = [
    "RESULT_KEY_SCHEME",
    "STUDY_KEY_SCHEME",
    "ResearchStudy",
    "StudyResult",
    "build_result",
    "canonical_result_key",
    "canonical_study_key",
    "derive_result_id",
    "derive_study_id",
]

#: Scheme tags, and the first line of each canonical key. Frozen for the life
#: of the scheme, as ``DATASET_KEY_SCHEME`` is.
STUDY_KEY_SCHEME: Final = "alphalab.study.v1"
RESULT_KEY_SCHEME: Final = "alphalab.study_result.v1"


@dataclass(frozen=True, slots=True)
class ResearchStudy:
    """One reproducible research experiment, stated completely.

    Attributes:
        study_name: What the experiment is called. Free-form, and part of the
            identity -- two studies that differ only in name are two studies,
            because a caller who renamed one meant something by it.
        dataset_version: The exact dataset version the study runs on, or
            ``None`` for a study over data with no provenance. A study meant to
            be auditable calls :meth:`require_dataset`.
        universe: The symbols in scope, sorted on construction so that two
            studies listing the same names in different orders are one study.
        features: The feature definitions the study computes, in the order
            given. Order is preserved rather than sorted: a pipeline that
            neutralizes before ranking is not the same pipeline as one that
            ranks before neutralizing.
        horizons: The forward horizons measured, sorted.
        splits: The split methodology, as the rendered scheme string a
            :class:`~alphalab.research.splits.SplitReport` carries. ``""`` for
            a study that does no splitting.
        parameters: Any other numeric configuration. Genuinely immutable.
        seed: The seed every stochastic step draws from, or ``None`` for a
            study with none. A study with stochastic steps and no seed is not
            reproducible and :meth:`require_seed` refuses it.
        notes: Free text for a human. Part of the identity, because two studies
            whose notes differ were described differently on purpose.

    Raises:
        ResearchValidationError: If the name is empty or contains ``"@"``, if
            the universe is empty, if it repeats a symbol, if no feature is
            given, if two features share a derived version, or if a horizon is
            not positive.
    """

    study_name: str
    dataset_version: str | None
    universe: tuple[str, ...]
    features: tuple[FeatureDefinition, ...]
    horizons: tuple[int, ...] = ()
    splits: str = ""
    parameters: Mapping[str, float] = field(default_factory=dict)
    seed: int | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))
        object.__setattr__(self, "universe", tuple(sorted(self.universe)))
        object.__setattr__(self, "horizons", tuple(sorted(self.horizons)))

        if not self.study_name.strip():
            raise ResearchValidationError("A study must be named before it can be identified.")
        if "@" in self.study_name:
            raise ResearchValidationError(
                f"A study_name may not contain '@': {self.study_name!r}. The character "
                "separates the name from the digest in a study id."
            )
        if not self.universe:
            raise ResearchValidationError(
                "A study must name the symbols it runs on. An empty universe would let the "
                "same study description cover any data at all."
            )
        if len(set(self.universe)) != len(self.universe):
            raise ResearchValidationError(
                f"The universe repeats a symbol: {sorted(self.universe)}."
            )
        if not self.features:
            raise ResearchValidationError(
                "A study must name at least one feature definition; without one there is "
                "nothing to compute."
            )
        versions = [definition.feature_version for definition in self.features]
        if len(set(versions)) != len(versions):
            raise ResearchValidationError(
                "Two of the study's features have the same derived version, which means the "
                "same computation was requested twice. Compute it once and read it twice."
            )
        for horizon in self.horizons:
            if horizon < 1:
                raise ResearchValidationError(
                    f"A forward horizon must be at least 1 observation, got {horizon}."
                )

    @property
    def study_id(self) -> str:
        """This study's derived, reproducible identity."""

        return derive_study_id(self)

    @property
    def feature_versions(self) -> tuple[str, ...]:
        """Each feature's derived version, in the study's own order."""

        return tuple(definition.feature_version for definition in self.features)

    def require_dataset(self) -> str:
        """Return the study's dataset version, or refuse.

        What a caller uses when lineage is not optional. The refusal names the
        path that produces a versioned dataset, matching
        :meth:`~alphalab.data.dataset.Dataset.require_provenance`.

        Raises:
            ResearchValidationError: If the study names no dataset version.
        """

        if self.dataset_version is None:
            raise ResearchValidationError(
                f"Study {self.study_name!r} names no dataset version, so its result could "
                "not say what data it was measured on. Ingest through "
                "alphalab.api.ingest_csv or ingest_rows with a RawSource, and take the "
                "version from Dataset.require_provenance()."
            )
        return self.dataset_version

    def require_seed(self) -> int:
        """Return the study's seed, or refuse.

        What a study with any stochastic step calls before running one.

        Raises:
            ResearchValidationError: If the study records no seed.
        """

        if self.seed is None:
            raise ResearchValidationError(
                f"Study {self.study_name!r} records no seed, so a stochastic step in it "
                "would produce a result nobody could reproduce. State a seed on the study; "
                "there is no default, because a default seed hidden in a library makes a "
                "result reproducible only for as long as the library does not change it."
            )
        return self.seed


def canonical_study_key(study: ResearchStudy) -> str:
    """Render the canonical key a study's identity is derived from.

    Field order is fixed and part of the specification, following
    :func:`~alphalab.data.provenance.canonical_dataset_key`. The universe and
    the horizons are already sorted by construction; the features are rendered
    in the study's own order because that order is part of the pipeline, and
    the parameters are sorted because a mapping has none.

    Public so the rendering can be pinned by a test and read by anyone auditing
    a study id.
    """

    parameters = study.parameters
    return "\n".join(
        [
            STUDY_KEY_SCHEME,
            f"name={study.study_name}",
            f"dataset={study.dataset_version or 'none'}",
            f"splits={study.splits or 'none'}",
            f"seed={'none' if study.seed is None else study.seed}",
            f"notes={study.notes}",
            "universe",
            *study.universe,
            "features",
            *study.feature_versions,
            "horizons",
            *(str(horizon) for horizon in study.horizons),
            "parameters",
            *(f"{name}={parameters[name]!r}" for name in sorted(parameters)),
        ]
    )


def derive_study_id(study: ResearchStudy) -> str:
    """``"<study_name>@<sha256 of the canonical key>"``. Derived, never minted."""

    key = canonical_study_key(study)
    return f"{study.study_name}@{hashlib.sha256(key.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True, slots=True)
class StudyResult:
    """What one study produced, and everything needed to reproduce it.

    Attributes:
        result_id: Digest over the study's identity and these numbers, from
            :func:`derive_result_id`.
        study: The experiment that produced it, identity and all.
        metrics: The numbers, flat and named. Flat because this is what
            :func:`~alphalab.lifecycle.evidence.build_evidence` consumes, and
            reshaping on the way there is where a metric gets renamed.
        splits: The full split report, when the study split; ``None``
            otherwise. Carried rather than summarized so every fold stays
            independently inspectable.
        feature_lineage: Each feature version to the lineage id of the series
            it produced, so a result names not just which features it used but
            which materialized computations.
        findings: Sentences the run produced -- a threshold crossed, a sample
            too small, a regime skipped. Empty means nothing objected, which is
            not the same as nothing being wrong.
        warnings: Sentences about the run's own validity, as distinct from its
            findings about the data.
        produced_at: Unix timestamp the result was assembled. A recorded fact,
            deliberately outside the digest.

    Raises:
        ResearchValidationError: If ``result_id`` is empty.
    """

    result_id: str
    study: ResearchStudy
    metrics: Mapping[str, float] = field(default_factory=dict)
    splits: SplitReport | None = None
    feature_lineage: Mapping[str, str] = field(default_factory=dict)
    findings: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    produced_at: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))
        object.__setattr__(self, "feature_lineage", MappingProxyType(dict(self.feature_lineage)))
        if not self.result_id.strip():
            raise ResearchValidationError("A study result must carry its derived identity.")

    @property
    def dataset_version(self) -> str | None:
        """The data the study ran on. Read through the study, never stored twice."""

        return self.study.dataset_version

    @property
    def study_id(self) -> str:
        """The experiment's identity."""

        return self.study.study_id

    def verify(self) -> bool:
        """Whether the stored id still matches this result's content.

        The same tamper check
        :func:`~alphalab.lifecycle.evidence.verify_evidence_id` performs, and
        for the same reason: numbers edited after the fact are not a result.
        """

        return self.result_id == derive_result_id(self.study, self.metrics)


def canonical_result_key(study: ResearchStudy, metrics: Mapping[str, float]) -> str:
    """Render the canonical key a result's identity is derived from.

    The study's *identity* rather than its full key, so a result's digest
    changes whenever the experiment's description does without this rendering
    having to repeat it. Metric names are sorted and each value rendered with
    ``repr`` so a float round-trips exactly -- the construction
    :func:`~alphalab.lifecycle.evidence.evidence_id_for` uses.
    """

    return "\n".join(
        [
            RESULT_KEY_SCHEME,
            f"study={derive_study_id(study)}",
            "metrics",
            *(f"{name}={metrics[name]!r}" for name in sorted(metrics)),
        ]
    )


def derive_result_id(study: ResearchStudy, metrics: Mapping[str, float]) -> str:
    """SHA-256 over :func:`canonical_result_key`."""

    return hashlib.sha256(canonical_result_key(study, metrics).encode("utf-8")).hexdigest()


def build_result(
    study: ResearchStudy,
    metrics: Mapping[str, float],
    produced_at: float,
    splits: SplitReport | None = None,
    feature_lineage: Mapping[str, str] | None = None,
    findings: Sequence[str] = (),
    warnings: Sequence[str] = (),
) -> StudyResult:
    """Assemble a :class:`StudyResult` with its identity computed.

    ``feature_lineage`` maps a feature version to the lineage id of the series
    computed for it. Every key must be one of the study's own feature versions:
    a result that named a lineage for a feature the study does not run would be
    recording a computation nobody asked for, which is the study-level form of
    the dataset substitution ADR-0017 closed.

    Raises:
        ResearchValidationError: If ``metrics`` is empty -- a result with no
            numbers is not a result -- or if ``feature_lineage`` names a
            feature version the study does not carry.
    """

    if not metrics:
        raise ResearchValidationError(
            f"Study {study.study_name!r} produced no metrics, so there is nothing to record "
            "as a result. A study whose diagnostics were all unmeasurable should say so in "
            "its findings rather than be recorded as an empty pass."
        )

    lineage = dict(feature_lineage or {})
    known = set(study.feature_versions)
    unknown = sorted(set(lineage) - known)
    if unknown:
        raise ResearchValidationError(
            f"The result names lineage for {len(unknown)} feature version(s) the study does "
            f"not run: {[version.split('@')[0] for version in unknown][:5]}. A result that "
            "can name a computation the experiment did not request is a result whose inputs "
            "cannot be trusted."
        )

    frozen = dict(metrics)
    return StudyResult(
        result_id=derive_result_id(study, frozen),
        study=study,
        metrics=frozen,
        splits=splits,
        feature_lineage=lineage,
        findings=tuple(findings),
        warnings=tuple(warnings),
        produced_at=produced_at,
    )
