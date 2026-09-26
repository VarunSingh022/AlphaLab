"""What exact inputs produced this result -- and can it be produced again?

Two questions every research result has to be able to answer, and AlphaLab
answered each only in part:

* A :class:`~alphalab.backtesting.state.BacktestResult` knows its dataset
  (``RunState.source_id``), its seed and its configuration -- and nothing about
  the code that ran, what that code depended on, or which engine executed it.
* A :class:`~alphalab.research.study.StudyResult` knows its study, its dataset
  version and its seed -- and, again, not the engine.
* :class:`~alphalab.lifecycle.evidence.ValidationEvidence` knows a dataset, a
  seed and fifteen numbers. It was never meant to know more: its digest is
  frozen (ADR-0017), and every field added to it would either enter that digest
  and break every recorded promotion, or sit outside it untamper-evidently.

A :class:`ReproducibilityManifest` is the record that joins them. It stores no
result -- there is no result store here and this module does not start one --
and it re-derives nothing a producer already identified. Every identity in it is
*read* from the authority that owns it:

=========================== ===================================================
The dataset                 ``Dataset.require_provenance()`` -- the derived
                            version and the content digest of the exact bytes,
                            checked against the version the result names.
The strategy                a :class:`~alphalab.lifecycle.fingerprint.StrategyFingerprint`
                            -- code, dependencies, parameters, research
                            configuration and the engine it was researched on.
The configuration           a run's own record of it, from
                            :func:`alphalab.runtime.run_snapshot.capture`; a
                            study's canonical key, from
                            :func:`alphalab.research.study.canonical_study_key`.
The seed                    ``RunConfig.seed`` or ``ResearchStudy.seed``, with
                            what the seed *does* stated by :class:`SeedRole`.
The result                  a run's complete canonical record; a study's
                            ``result_id``.
The engine                  supplied by the producer, as an
                            :class:`~alphalab.lifecycle.fingerprint.EngineIdentity`.
=========================== ===================================================

A run's identity is its record, not a summary of it
---------------------------------------------------

:func:`digest_run` hashes the run's complete snapshot, serialized by
:func:`alphalab.persistence.serialize` -- the payload ADR-0029 proves
byte-identical across a real process boundary. Every order, fill, cash movement
and equity point is in it, so a rerun that differs anywhere at all produces a
different digest. The cost is one canonical serialization, linear in the run.

That makes the seed load-bearing. An unseeded run mints its identifiers from
``uuid4`` and **no** rerun can reproduce its record, however deterministic its
economics are; :func:`manifest_for_run` therefore refuses one rather than
recording a reproducibility the result cannot have.

Four questions, kept apart
--------------------------

:func:`assess_reproducibility` answers them separately, because collapsing them
into one boolean is how a result comes to be called reproducible because a
digest exists:

1. **Identity** -- does the manifest recompute from its own content?
2. **Metadata completeness** -- is anything it rests on approximate: code
   identified by name rather than content, dependencies short of an exact
   closure?
3. **Rerun** -- did somebody produce the result again from the declared inputs,
   and did it come out the same? :class:`RerunOutcome` distinguishes a rerun
   that diverged from one that was not a rerun of the same inputs at all.
4. **External dependencies** -- what a rerun needs that AlphaLab does not hold.
   Always non-empty: AlphaLab stores no dataset bytes and no strategy code, so
   it never claims self-contained reproduction. A live run's venue executions
   are listed as not recreatable at all.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Final, Protocol

from alphalab.backtesting.state import BacktestResult
from alphalab.lifecycle.exceptions import LifecycleInputError
from alphalab.lifecycle.fingerprint import (
    EngineIdentity,
    StrategyFingerprint,
    verify_fingerprint,
)
from alphalab.persistence.exceptions import PersistenceError
from alphalab.persistence.serializer import serialize
from alphalab.research.study import StudyResult, canonical_study_key
from alphalab.runtime.run import ExecutionMode
from alphalab.runtime.run_snapshot import RunSnapshot
from alphalab.runtime.run_snapshot import capture as capture_run
from alphalab.strategy.adaptive import AdaptiveReplay

__all__ = [
    "REPRODUCIBILITY_MANIFEST_SCHEME",
    "AdaptiveReplayAssessment",
    "DatasetProvenanceView",
    "ExternalInput",
    "ExternalRequirement",
    "ReproducibilityAssessment",
    "ReproducibilityManifest",
    "RerunOutcome",
    "ResultKind",
    "RunDigest",
    "SeedRole",
    "SourceBytesView",
    "VersionedDataset",
    "assess_adaptive_replay",
    "assess_reproducibility",
    "canonical_manifest_key",
    "derive_manifest_id",
    "digest_run",
    "external_requirements",
    "manifest_for_run",
    "manifest_for_study",
    "manifest_gaps",
    "verify_manifest",
]

#: Scheme tag, and the first line of every canonical manifest key. Frozen for
#: the life of the scheme; changing it is an ADR.
REPRODUCIBILITY_MANIFEST_SCHEME: Final = "alphalab.reproducibility_manifest.v1"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SourceBytesView(Protocol):
    """How many bytes a dataset's recorded source held."""

    @property
    def byte_count(self) -> int:
        """The length of the recorded source payload."""
        ...


class DatasetProvenanceView(Protocol):
    """The facts a manifest reads from a dataset's provenance."""

    @property
    def dataset_version(self) -> str:
        """The derived dataset version."""
        ...

    @property
    def content_hash(self) -> str:
        """The digest of the source bytes the version was derived from."""
        ...

    @property
    def source(self) -> SourceBytesView:
        """The recorded source, for the size of what it held."""
        ...


class VersionedDataset(Protocol):
    """A dataset that can state its provenance, or refuse to.

    Structural, so this module reaches a dataset without importing
    :mod:`alphalab.data`: :class:`~alphalab.data.dataset.Dataset` satisfies it
    as it stands, and its ``require_provenance`` is the refusal a dataset with
    no lineage meets here. ``alphalab.api`` is the one module that imports both
    the data layer and the research layer, and a manifest over a study needs
    the second (``test_one_research_authority_per_concept.py``).
    """

    def require_provenance(self) -> DatasetProvenanceView:
        """The dataset's provenance; raises when none was recorded."""
        ...


class ResultKind(Enum):
    """What produced the result a manifest identifies."""

    #: A run through the execution path -- a backtest, a replay, a paper run or
    #: a live run. Which one is recorded in its configuration's ``mode``.
    RUN = auto()

    #: A v3.2 research study.
    STUDY = auto()


class SeedRole(Enum):
    """What a manifest's seed does, so absence and presence each mean one thing."""

    #: ``RunConfig.seed``: the identifier stream a run draws from (ADR-0022).
    #: Required for a run, because without it no rerun reproduces the record.
    IDENTIFIER_STREAM = auto()

    #: ``ResearchStudy.seed``: what every stochastic step of a study draws from.
    STOCHASTIC_STEPS = auto()

    #: The result declares no seed -- a study with no stochastic step. Recorded
    #: as absent, never replaced by a seed nobody chose.
    ABSENT = auto()


class ExternalInput(Enum):
    """What a rerun needs that AlphaLab does not hold."""

    #: The dataset's bytes. AlphaLab records their digest, not the bytes.
    DATASET_BYTES = auto()

    #: The strategy's code, as its fingerprint identifies it.
    STRATEGY_CODE = auto()

    #: The declared dependency set.
    DEPENDENCIES = auto()

    #: The engine version that produced the result.
    ENGINE = auto()

    #: The objects a run records by type only (ADR-0023): its sizing model,
    #: simulator, fill policy, instrument registry and strategy instances.
    LIVE_OBJECTS = auto()

    #: The ``NormalizationPolicy`` that turned the dataset into market records.
    #: A run records the records it read, not the policy that produced them.
    NORMALIZATION_POLICY = auto()

    #: The strategy context factory a run was driven with.
    CONTEXT_FACTORY = auto()

    #: A live run's fills, which a venue decided. No rerun recreates them.
    VENUE_EXECUTIONS = auto()

    #: A versioned input a study named beside its dataset (v3.7) -- an event
    #: set, an observation set, fundamentals, a regime definition -- identified
    #: in the study's configuration and held, like the dataset, by the caller.
    AUXILIARY_DATA = auto()


@dataclass(frozen=True, slots=True)
class ExternalRequirement:
    """One input a rerun must be given from outside AlphaLab.

    Attributes:
        input: Which kind of input.
        detail: Exactly what, identified as precisely as the manifest can.
        recreatable: Whether any rerun could supply it at all. ``False`` only
            for a live run's venue executions.
    """

    input: ExternalInput
    detail: str
    recreatable: bool


# --------------------------------------------------------------------------- #
# A run's record
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RunDigest:
    """What one run's own canonical record says it was, and what it produced.

    Attributes:
        result_id: SHA-256 of the run's complete record: its
            :class:`~alphalab.runtime.run_snapshot.RunSnapshot`, serialized by
            :func:`alphalab.persistence.serialize`.
        configuration: The recorded configuration as canonical JSON -- the
            pipeline configuration record, the run-level configuration, and
            each strategy's identity, type, runtime configuration and
            subscriptions. Everything the run records about *how* it was
            configured, and nothing about what it did. The seed and the dataset
            are separate fields, so a change to either is attributable.
        configuration_id: SHA-256 of :attr:`configuration`.
        mode: Which environment the run was in.
        seed: The identifier seed, or ``None`` for an unseeded run.
        dataset_id: The stream the run read -- ``RunState.source_id``.
        strategy_ids: Every strategy the run executed, sorted.
        live_objects: ``role=Type`` for every object the run records by type
            only, sorted. Their parameters are not in the record (ADR-0023).
    """

    result_id: str
    configuration: str
    configuration_id: str
    mode: ExecutionMode
    seed: int | None
    dataset_id: str | None
    strategy_ids: tuple[str, ...]
    live_objects: tuple[str, ...]


def _recorded_configuration(snapshot: RunSnapshot) -> dict[str, Any]:
    strategies = sorted(snapshot.pipeline.strategy, key=lambda record: record.strategy_id)
    return {
        "pipeline": snapshot.pipeline.config,
        "mode": snapshot.mode,
        "start_timestamp": snapshot.start_timestamp,
        "ordering": snapshot.ordering,
        "max_market_data_age_seconds": snapshot.max_market_data_age_seconds,
        "years_elapsed": snapshot.years_elapsed,
        "risk_free_rate": snapshot.risk_free_rate,
        "compile_analytics": snapshot.compile_analytics,
        "fill_policy_type": snapshot.fill_policy_type,
        "strategies": [
            {
                "strategy_id": record.strategy_id,
                "instance_type": record.instance_type,
                "config": record.config,
                "subscriptions": list(record.subscriptions),
            }
            for record in strategies
        ],
    }


def digest_run(result: BacktestResult) -> RunDigest:
    """Read a finished run's identities off its own canonical record.

    One capture and one serialization of the whole run: linear in what the run
    did, and the price of an identity that covers all of it.

    Raises:
        LifecycleInputError: If the run's record has no deterministic JSON form
            -- a strategy configured with a value the encoder cannot write --
            in which case it has no canonical identity either.
    """

    try:
        snapshot = capture_run(result.run)
        record = serialize(snapshot)
        configuration = serialize(_recorded_configuration(snapshot))
    except PersistenceError as error:
        raise LifecycleInputError(
            "The run's record cannot be serialized deterministically, so it has no "
            f"canonical identity: {error}"
        ) from error

    config = snapshot.pipeline.config
    objects = [
        f"sizing_model={config.sizing_model_type}",
        f"simulator={config.simulator_type}",
        f"fill_policy={snapshot.fill_policy_type}",
    ]
    if config.instruments_type is not None:
        objects.append(f"instruments={config.instruments_type}")
    objects.extend(
        f"strategy:{record.strategy_id}={record.instance_type}"
        for record in snapshot.pipeline.strategy
    )
    return RunDigest(
        result_id=_sha256(record),
        configuration=configuration,
        configuration_id=_sha256(configuration),
        mode=snapshot.mode,
        seed=snapshot.seed,
        dataset_id=snapshot.source_id,
        strategy_ids=tuple(sorted(record.strategy_id for record in snapshot.pipeline.strategy)),
        live_objects=tuple(sorted(objects)),
    )


# --------------------------------------------------------------------------- #
# The manifest
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ReproducibilityManifest:
    """Everything one result was produced from, and the result's own identity.

    Built by :func:`manifest_for_run` or :func:`manifest_for_study`, which read
    every field from the authority that owns it. Constructing one by hand is
    possible and :func:`verify_manifest` will say whether it holds together.

    Attributes:
        manifest_id: SHA-256 of :func:`canonical_manifest_key`.
        kind: What produced the result.
        result_id: The result's identity -- a run's record digest, or a study's
            ``result_id``.
        dataset_version: The derived dataset version the result was measured
            on.
        dataset_content_hash: The digest of that dataset's source bytes, so a
            rerun knows exactly which bytes to supply.
        configuration: The configuration as its owner renders it: a run's
            recorded configuration as canonical JSON, or a study's canonical
            key.
        configuration_id: SHA-256 of :attr:`configuration`. For a study this is
            the digest half of its ``study_id``.
        seed: The seed, or ``None``.
        seed_role: What the seed does -- and, for ``None``, why there is none.
        engine: The engine that produced the result. Supplied by the producer;
            it may legitimately differ from the engine the strategy was
            fingerprinted under, and :func:`assess_reproducibility` says so when
            it does.
        fingerprint: The strategy, or ``None`` for a study that measured
            features rather than a strategy.
    """

    manifest_id: str
    kind: ResultKind
    result_id: str
    dataset_version: str
    dataset_content_hash: str
    configuration: str
    configuration_id: str
    seed: int | None
    seed_role: SeedRole
    engine: EngineIdentity
    fingerprint: StrategyFingerprint | None

    @property
    def mode(self) -> ExecutionMode | None:
        """The environment a run was in, read from its configuration.

        ``None`` for a study, and for a run whose configuration does not name a
        mode this build knows -- which :func:`verify_manifest` would also
        refuse, because only an altered configuration can say that.
        """

        recorded = _recorded_run(self)
        if recorded is None:
            return None
        for mode in ExecutionMode:
            if str(mode) == recorded.get("mode"):
                return mode
        return None


def _recorded_run(manifest: ReproducibilityManifest) -> dict[str, Any] | None:
    """A run manifest's recorded configuration, parsed; ``None`` when unreadable."""

    if manifest.kind is not ResultKind.RUN:
        return None
    try:
        recorded = json.loads(manifest.configuration)
    except json.JSONDecodeError:
        return None
    return recorded if isinstance(recorded, dict) else None


def canonical_manifest_key(
    kind: ResultKind,
    result_id: str,
    dataset_version: str,
    dataset_content_hash: str,
    configuration_id: str,
    seed: int | None,
    seed_role: SeedRole,
    engine: EngineIdentity,
    fingerprint: str | None,
) -> str:
    """Render the canonical key a manifest's identity is derived from.

    Public so the rendering can be pinned by a test and read by anyone auditing
    a manifest. The configuration enters through its digest; the fingerprint
    through its own derived identity, which already commits to the code,
    dependencies, parameters, research configuration and engine it names.
    """

    return "\n".join(
        [
            REPRODUCIBILITY_MANIFEST_SCHEME,
            f"kind={kind.name}",
            f"result={result_id!r}",
            f"dataset={dataset_version!r}",
            f"content={dataset_content_hash!r}",
            f"configuration={configuration_id!r}",
            f"seed_role={seed_role.name}",
            f"seed={seed!r}",
            f"strategy={fingerprint!r}",
            f"engine.name={engine.name!r}",
            f"engine.version={engine.version!r}",
        ]
    )


def derive_manifest_id(
    kind: ResultKind,
    result_id: str,
    dataset_version: str,
    dataset_content_hash: str,
    configuration_id: str,
    seed: int | None,
    seed_role: SeedRole,
    engine: EngineIdentity,
    fingerprint: str | None,
) -> str:
    """SHA-256 over :func:`canonical_manifest_key`. Derived, never minted."""

    return _sha256(
        canonical_manifest_key(
            kind,
            result_id,
            dataset_version,
            dataset_content_hash,
            configuration_id,
            seed,
            seed_role,
            engine,
            fingerprint,
        )
    )


def _manifest(
    kind: ResultKind,
    result_id: str,
    dataset_version: str,
    dataset_content_hash: str,
    configuration: str,
    seed: int | None,
    seed_role: SeedRole,
    engine: EngineIdentity,
    fingerprint: StrategyFingerprint | None,
) -> ReproducibilityManifest:
    configuration_id = _sha256(configuration)
    return ReproducibilityManifest(
        manifest_id=derive_manifest_id(
            kind,
            result_id,
            dataset_version,
            dataset_content_hash,
            configuration_id,
            seed,
            seed_role,
            engine,
            None if fingerprint is None else fingerprint.fingerprint,
        ),
        kind=kind,
        result_id=result_id,
        dataset_version=dataset_version,
        dataset_content_hash=dataset_content_hash,
        configuration=configuration,
        configuration_id=configuration_id,
        seed=seed,
        seed_role=seed_role,
        engine=engine,
        fingerprint=fingerprint,
    )


def _identifying(dataset: VersionedDataset) -> DatasetProvenanceView:
    """A dataset's provenance, refused when its version cannot identify its content.

    A dataset version is derived from the source bytes its provenance records,
    and :func:`alphalab.api.ingest_rows` records the source a caller supplies,
    as given (ADR-0036). A source recorded with an empty payload gives every set
    of rows ingested under one name and configuration the *same* version -- so
    a manifest naming it would claim one dataset for any data at all.
    """

    provenance = dataset.require_provenance()
    if provenance.source.byte_count == 0:
        raise LifecycleInputError(
            f"Dataset {provenance.dataset_version} records an empty source payload, so its "
            "version cannot tell it from any other rows ingested under the same name and "
            "configuration. Record the bytes the rows came from -- raw_source_from_bytes "
            "with the rows' own payload -- or ingest the file with ingest_csv, which records "
            "them itself."
        )
    return provenance


def _require_verified(fingerprint: StrategyFingerprint) -> None:
    if not verify_fingerprint(fingerprint):
        raise LifecycleInputError(
            f"Fingerprint {fingerprint.fingerprint} does not match its own content; it was "
            "altered after it was derived, and a manifest resting on it would name a strategy "
            "that does not exist."
        )


def manifest_for_run(
    result: BacktestResult,
    dataset: VersionedDataset,
    fingerprint: StrategyFingerprint,
    engine: EngineIdentity,
) -> ReproducibilityManifest:
    """The manifest of a finished run, with every identity read from its owner.

    Args:
        result: The finished run. Its dataset, seed and configuration are read
            from its own record.
        dataset: The dataset it read, so the manifest can name the exact bytes.
            Checked, not trusted: its derived version must be the one the run
            recorded.
        fingerprint: The strategy that ran. Checked: it must verify, and its
            ``strategy_id`` must be one the run actually executed.
        engine: The engine that executed the run -- typically
            :func:`~alphalab.lifecycle.fingerprint.running_engine` in the
            process that ran it.

    Raises:
        DataValidationError: If ``dataset`` carries no provenance.
        LifecycleInputError: If the dataset's provenance records an empty source
            payload -- its version then identifies no content -- or if the run
            names no dataset or a different one, is
            unseeded, executed no strategy the fingerprint names, or the
            fingerprint does not verify.
    """

    digest = digest_run(result)
    provenance = _identifying(dataset)
    if digest.dataset_id is None:
        raise LifecycleInputError(
            "The run names no dataset, so a manifest could not say what it was measured on. "
            "A run driven through BacktestEngine.run carries its dataset's identity."
        )
    if provenance.dataset_version != digest.dataset_id:
        raise LifecycleInputError(
            f"The run read {digest.dataset_id!r} and the dataset supplied is "
            f"{provenance.dataset_version!r}. A manifest naming the second would send every "
            "rerun to data the result was never measured on."
        )
    if digest.seed is None:
        raise LifecycleInputError(
            "The run is unseeded, so its identifiers came from uuid4 and no rerun can "
            "reproduce its record, however deterministic its economics are. Seed the run "
            "(RunConfig.seed) to make its result reproducible; recording one as reproducible "
            "without a seed would be a claim it cannot keep."
        )
    _require_verified(fingerprint)
    if fingerprint.strategy_id not in digest.strategy_ids:
        raise LifecycleInputError(
            f"The fingerprint is of strategy {fingerprint.strategy_id!r} and the run "
            f"executed {list(digest.strategy_ids)}. A manifest joining them would say one "
            "strategy produced another's result."
        )
    return _manifest(
        kind=ResultKind.RUN,
        result_id=digest.result_id,
        dataset_version=provenance.dataset_version,
        dataset_content_hash=provenance.content_hash,
        configuration=digest.configuration,
        seed=digest.seed,
        seed_role=SeedRole.IDENTIFIER_STREAM,
        engine=engine,
        fingerprint=fingerprint,
    )


def manifest_for_study(
    result: StudyResult,
    dataset: VersionedDataset,
    engine: EngineIdentity,
    fingerprint: StrategyFingerprint | None = None,
) -> ReproducibilityManifest:
    """The manifest of a v3.2 research study's result.

    The configuration is the study's canonical key, so the manifest's
    configuration digest is exactly the digest half of ``study_id`` -- the
    study's own identity, not a second rendering of it. A study with no seed is
    recorded as :attr:`SeedRole.ABSENT`, never given one.

    Args:
        result: The study's result. It must still match its own metrics.
        dataset: The dataset it ran on, checked against the study's version.
        engine: The engine that ran the study.
        fingerprint: The strategy the study was of, if it was of one. When its
            research configuration names a study, it must be this one.

    Raises:
        DataValidationError: If ``dataset`` carries no provenance.
        LifecycleInputError: If the dataset's provenance records an empty source
            payload, or if the result was altered, names no dataset or a
            different one, or the fingerprint does not verify or names a
            different study.
    """

    if not result.verify():
        raise LifecycleInputError(
            f"Study result {result.result_id} does not match its own metrics; it was altered "
            "after it was recorded, and a manifest for it would identify numbers nobody "
            "produced."
        )
    if result.dataset_version is None:
        raise LifecycleInputError(
            f"Study {result.study.study_name!r} names no dataset version, so a manifest could "
            "not say what it was measured on."
        )
    provenance = _identifying(dataset)
    if provenance.dataset_version != result.dataset_version:
        raise LifecycleInputError(
            f"The study ran on {result.dataset_version!r} and the dataset supplied is "
            f"{provenance.dataset_version!r}."
        )
    if fingerprint is not None:
        _require_verified(fingerprint)
        claimed = fingerprint.research.study_id
        if claimed is not None and claimed != result.study_id:
            raise LifecycleInputError(
                f"The fingerprint was researched under study {claimed!r} and this result is "
                f"from {result.study_id!r}."
            )
    seed = result.study.seed
    return _manifest(
        kind=ResultKind.STUDY,
        result_id=result.result_id,
        dataset_version=provenance.dataset_version,
        dataset_content_hash=provenance.content_hash,
        configuration=canonical_study_key(result.study),
        seed=seed,
        seed_role=SeedRole.ABSENT if seed is None else SeedRole.STOCHASTIC_STEPS,
        engine=engine,
        fingerprint=fingerprint,
    )


def verify_manifest(manifest: ReproducibilityManifest) -> bool:
    """Whether the manifest's identity recomputes from its own declared content.

    Checks the manifest digest, the configuration digest, the embedded
    fingerprint, and that the seed and its role agree with each other and with
    the kind of result.
    """

    if manifest.configuration_id != _sha256(manifest.configuration):
        return False
    if manifest.fingerprint is not None and not verify_fingerprint(manifest.fingerprint):
        return False
    if (manifest.seed is None) != (manifest.seed_role is SeedRole.ABSENT):
        return False
    expected_role = (
        SeedRole.IDENTIFIER_STREAM
        if manifest.kind is ResultKind.RUN
        else (SeedRole.ABSENT if manifest.seed is None else SeedRole.STOCHASTIC_STEPS)
    )
    if manifest.seed_role is not expected_role:
        return False
    return manifest.manifest_id == derive_manifest_id(
        manifest.kind,
        manifest.result_id,
        manifest.dataset_version,
        manifest.dataset_content_hash,
        manifest.configuration_id,
        manifest.seed,
        manifest.seed_role,
        manifest.engine,
        None if manifest.fingerprint is None else manifest.fingerprint.fingerprint,
    )


def manifest_gaps(manifest: ReproducibilityManifest) -> tuple[str, ...]:
    """What the manifest rests on that is approximate or missing.

    Empty means complete *for what a manifest can record*. Each entry is a
    reason a rerun elsewhere could come out differently even though every
    recorded identity matched.
    """

    gaps: list[str] = []
    fingerprint = manifest.fingerprint
    if fingerprint is None:
        if manifest.kind is ResultKind.RUN:
            gaps.append("no strategy fingerprint: the code that ran is not identified.")
        return tuple(gaps)
    code = fingerprint.code
    if not code.content_addressed:
        what = "package and version" if code.version is not None else "package name alone"
        gaps.append(
            f"the strategy code is identified by its {what}; its source was not hashed, so "
            "different code under the same declaration would not be told apart."
        )
    if not fingerprint.dependencies.exact:
        gaps.append(
            f"dependencies are {fingerprint.dependencies.completeness.name}: the exact "
            "dependency closure is not recorded, so a rerun can resolve different versions."
        )
    return tuple(gaps)


def _live_objects(manifest: ReproducibilityManifest) -> list[str]:
    """``role=Type`` for every object the recorded configuration names by type."""

    unreadable = ["(the recorded configuration is unreadable)"]
    recorded = _recorded_run(manifest)
    if recorded is None:
        return unreadable
    try:
        pipeline = recorded["pipeline"]
        objects = [
            f"sizing_model={pipeline['sizing_model_type']}",
            f"simulator={pipeline['simulator_type']}",
            f"fill_policy={recorded['fill_policy_type']}",
        ]
        if pipeline["instruments_type"] is not None:
            objects.append(f"instruments={pipeline['instruments_type']}")
        objects.extend(
            f"strategy:{entry['strategy_id']}={entry['instance_type']}"
            for entry in recorded["strategies"]
        )
    except (KeyError, TypeError):
        return unreadable
    return sorted(objects)


def external_requirements(manifest: ReproducibilityManifest) -> tuple[ExternalRequirement, ...]:
    """Every input a rerun needs that AlphaLab does not hold, in a fixed order.

    Never empty: AlphaLab holds neither dataset bytes nor strategy code, so no
    manifest describes a self-contained reproduction.
    """

    requirements = [
        ExternalRequirement(
            ExternalInput.DATASET_BYTES,
            f"the bytes of {manifest.dataset_version} (content digest "
            f"{manifest.dataset_content_hash}), ingested under the same schema, zone, "
            "calendar, cleaning policy and transformations, so that the derived version is "
            "the same",
            recreatable=True,
        )
    ]
    fingerprint = manifest.fingerprint
    if fingerprint is not None:
        code = fingerprint.code
        requirements.append(
            ExternalRequirement(
                ExternalInput.STRATEGY_CODE,
                f"{code.package} {code.version or '(unversioned)'} at {code.entry_point}, "
                + (
                    f"source digest {code.source_digest}"
                    if code.source_digest is not None
                    else "source not hashed"
                ),
                recreatable=True,
            )
        )
        requirements.append(
            ExternalRequirement(
                ExternalInput.DEPENDENCIES,
                f"{fingerprint.dependencies.completeness.name} with "
                f"{len(fingerprint.dependencies.pins)} pinned distribution(s)",
                recreatable=True,
            )
        )
    requirements.append(
        ExternalRequirement(ExternalInput.ENGINE, str(manifest.engine), recreatable=True)
    )
    if manifest.kind is ResultKind.STUDY:
        requirements.extend(
            ExternalRequirement(
                ExternalInput.AUXILIARY_DATA,
                f"{role}: {identity}, supplied exactly as the study named it",
                recreatable=True,
            )
            for role, identity in _study_inputs(manifest.configuration)
        )
    if manifest.kind is ResultKind.RUN:
        requirements.extend(
            (
                ExternalRequirement(
                    ExternalInput.LIVE_OBJECTS,
                    "supplied back by type -- "
                    + ", ".join(_live_objects(manifest))
                    + " -- with the parameters they ran with, which a run records only by type",
                    recreatable=True,
                ),
                ExternalRequirement(
                    ExternalInput.NORMALIZATION_POLICY,
                    "the normalization policy that turned the dataset into market records",
                    recreatable=True,
                ),
                ExternalRequirement(
                    ExternalInput.CONTEXT_FACTORY,
                    "the strategy context factory the run was driven with",
                    recreatable=True,
                ),
            )
        )
        if manifest.mode is ExecutionMode.LIVE:
            requirements.append(
                ExternalRequirement(
                    ExternalInput.VENUE_EXECUTIONS,
                    "the fills a venue reported; a venue's decisions are not in the run's "
                    "record and no rerun can make them again",
                    recreatable=False,
                )
            )
    return tuple(requirements)


def _study_inputs(configuration: str) -> tuple[tuple[str, str], ...]:
    """The ``inputs`` section of a study's canonical key, as ``(role, identity)``.

    The section is appended last, after ``parameters``, and only when present
    (:func:`~alphalab.research.study.canonical_study_key`), so it is read
    backwards from the end: every line after the last section header, if that
    header is ``inputs``. Neither header can be a parameter or input line, both
    of which contain ``=``.
    """

    collected: list[str] = []
    for line in reversed(configuration.split("\n")):
        if line == "inputs":
            return tuple(
                (role, identity)
                for role, _, identity in (entry.partition("=") for entry in reversed(collected))
            )
        if line == "parameters":
            return ()
        collected.append(line)
    return ()


# --------------------------------------------------------------------------- #
# Assessment
# --------------------------------------------------------------------------- #


class RerunOutcome(Enum):
    """What a rerun established. Four values, because "not the same" is two things."""

    #: No rerun was supplied. Nothing is established about reproduction.
    NOT_ATTEMPTED = auto()

    #: The same declared inputs produced the same result: identical manifests.
    REPRODUCED = auto()

    #: The same declared inputs produced a different result. Reproduction failed.
    DIVERGED = auto()

    #: The rerun was not of the same declared inputs -- another engine, another
    #: seed, another configuration -- so it establishes nothing either way.
    INPUTS_DIFFER = auto()


@dataclass(frozen=True, slots=True)
class ReproducibilityAssessment:
    """Four separate answers about one manifest.

    Attributes:
        manifest_id: The manifest assessed.
        identity_verified: Whether the manifest recomputes from its content.
        gaps: What it rests on that is approximate; see :func:`manifest_gaps`.
        rerun: What a rerun established.
        rerun_detail: Why, in sentences: the differing inputs, or the two
            result identities.
        external_requirements: What a rerun needs from outside AlphaLab.
        findings: Facts worth a reader's attention that change none of the
            above -- an engine that differs from the one the strategy was
            fingerprinted under, a study that records no seed, a live run.
    """

    manifest_id: str
    identity_verified: bool
    gaps: tuple[str, ...]
    rerun: RerunOutcome
    rerun_detail: tuple[str, ...]
    external_requirements: tuple[ExternalRequirement, ...]
    findings: tuple[str, ...]

    @property
    def metadata_complete(self) -> bool:
        """Whether nothing the manifest rests on is approximate."""

        return not self.gaps

    @property
    def recreatable(self) -> bool:
        """Whether every external input could, in principle, be supplied again."""

        return all(requirement.recreatable for requirement in self.external_requirements)


def _input_differences(
    original: ReproducibilityManifest, rerun: ReproducibilityManifest
) -> list[str]:
    differences: list[str] = []
    for label, before, after in (
        ("kind", original.kind.name, rerun.kind.name),
        ("dataset", original.dataset_version, rerun.dataset_version),
        ("dataset content", original.dataset_content_hash, rerun.dataset_content_hash),
        ("configuration", original.configuration_id, rerun.configuration_id),
        ("seed", repr(original.seed), repr(rerun.seed)),
        ("seed role", original.seed_role.name, rerun.seed_role.name),
        ("engine", str(original.engine), str(rerun.engine)),
        (
            "strategy",
            repr(None if original.fingerprint is None else original.fingerprint.fingerprint),
            repr(None if rerun.fingerprint is None else rerun.fingerprint.fingerprint),
        ),
    ):
        if before != after:
            differences.append(f"{label}: {before} -> {after}")
    return differences


def assess_reproducibility(
    manifest: ReproducibilityManifest,
    rerun: ReproducibilityManifest | None = None,
) -> ReproducibilityAssessment:
    """Assess one manifest, and a rerun of it if one was produced.

    A rerun is supplied as *its own* manifest: the caller produces the result
    again from the declared inputs and builds a manifest for the new result
    with :func:`manifest_for_run` or :func:`manifest_for_study`. The two are
    compared input by input before their results are, so a rerun of different
    inputs is reported as :attr:`RerunOutcome.INPUTS_DIFFER` rather than as a
    failure -- or a success -- of reproduction.

    Pure and deterministic; nothing is executed here.

    Raises:
        LifecycleInputError: If a rerun is supplied and either manifest does
            not verify. A record that was altered after it was derived is not
            evidence of reproduction, whichever side of the comparison it is
            on. Without a rerun, an unverifiable manifest is assessed and
            reported as :attr:`ReproducibilityAssessment.identity_verified`
            ``False``.
    """

    findings: list[str] = []
    fingerprint = manifest.fingerprint
    if fingerprint is not None and fingerprint.engine != manifest.engine:
        findings.append(
            f"the result was produced by {manifest.engine} and the strategy was fingerprinted "
            f"under {fingerprint.engine}."
        )
    if manifest.seed_role is SeedRole.ABSENT:
        findings.append(
            "the study records no seed; its result reproduces only if it ran no stochastic "
            "step, which is the study's declaration rather than something AlphaLab observed."
        )
    if manifest.kind is ResultKind.RUN and manifest.mode is ExecutionMode.LIVE:
        findings.append(
            "a live run: its fills came from a venue, so the manifest identifies the result "
            "and cannot reproduce it."
        )

    identity_verified = verify_manifest(manifest)
    outcome = RerunOutcome.NOT_ATTEMPTED
    detail: list[str] = []
    if rerun is not None:
        if not identity_verified:
            raise LifecycleInputError(
                f"Manifest {manifest.manifest_id} does not verify, so there is no record to "
                "compare a rerun against; a rerun matching an altered record is not evidence "
                "of reproduction."
            )
        if not verify_manifest(rerun):
            raise LifecycleInputError(
                f"The rerun's manifest {rerun.manifest_id} does not verify; a rerun whose "
                "record was altered is not evidence of reproduction."
            )
        differences = _input_differences(manifest, rerun)
        if differences:
            outcome = RerunOutcome.INPUTS_DIFFER
            detail.extend(differences)
            detail.append(
                "the result "
                + ("nevertheless matched" if rerun.result_id == manifest.result_id else "differed")
                + ", which establishes nothing about reproduction from the declared inputs."
            )
        elif rerun.result_id == manifest.result_id:
            outcome = RerunOutcome.REPRODUCED
            detail.append(f"the rerun produced result {rerun.result_id} again.")
        else:
            outcome = RerunOutcome.DIVERGED
            detail.append(
                f"the same declared inputs produced {rerun.result_id} where the manifest "
                f"records {manifest.result_id}."
            )

    return ReproducibilityAssessment(
        manifest_id=manifest.manifest_id,
        identity_verified=identity_verified,
        gaps=manifest_gaps(manifest),
        rerun=outcome,
        rerun_detail=tuple(detail),
        external_requirements=external_requirements(manifest),
        findings=tuple(findings),
    )


# --------------------------------------------------------------------------- #
# Adaptive replays (v3.7)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AdaptiveReplayAssessment:
    """What a rerun of an adaptive replay established.

    Attributes:
        replay_id: The replay assessed.
        record_verified: Whether its record holds together -- transitions that
            chain, decisions that belong to them, a stream identity that
            recomputes.
        rerun: What the rerun established; :class:`RerunOutcome`, the same four
            answers a run or a study gets.
        detail: Why, in sentences.
        first_divergence: For ``DIVERGED``, the position of the first
            observation whose resulting state or decision differs; otherwise
            ``None``.
    """

    replay_id: str
    record_verified: bool
    rerun: RerunOutcome
    detail: tuple[str, ...]
    first_divergence: int | None


def assess_adaptive_replay(
    original: AdaptiveReplay, rerun: AdaptiveReplay | None = None
) -> AdaptiveReplayAssessment:
    """Assess an adaptive replay, and a rerun of it if one was produced.

    The adaptive counterpart of :func:`assess_reproducibility`: a rerun of the
    same configuration, mode, starting state and observation stream either
    produced the same decisions and the same final state (``REPRODUCED``) or did
    not (``DIVERGED``, with the first observation at which it did not); a rerun
    of different inputs establishes nothing (``INPUTS_DIFFER``). A divergence
    with identical inputs is how a rule that read a clock, drew a random number
    or kept state of its own shows itself.

    Pure; nothing is re-run here.

    Raises:
        LifecycleInputError: If a rerun is supplied and either record does not
            hold together. An altered record is not evidence of reproduction.
    """

    verified = original.verify()
    if rerun is None:
        return AdaptiveReplayAssessment(
            original.replay_id, verified, RerunOutcome.NOT_ATTEMPTED, (), None
        )
    if not verified or not rerun.verify():
        which = "original" if not verified else "rerun"
        raise LifecycleInputError(
            f"The {which} adaptive replay's record does not hold together, so it is not "
            "evidence of reproduction either way."
        )
    differences = [
        f"{label}: {before} -> {after}"
        for label, before, after in (
            (
                "configuration",
                original.configuration.configuration_id,
                rerun.configuration.configuration_id,
            ),
            ("mode", original.mode.name, rerun.mode.name),
            ("initial state", original.initial.state_id, rerun.initial.state_id),
            ("stream", original.stream_id, rerun.stream_id),
        )
        if before != after
    ]
    if differences:
        return AdaptiveReplayAssessment(
            original.replay_id,
            verified,
            RerunOutcome.INPUTS_DIFFER,
            (*differences, "a rerun of other inputs establishes nothing about reproduction."),
            None,
        )
    if rerun.replay_id == original.replay_id:
        return AdaptiveReplayAssessment(
            original.replay_id,
            verified,
            RerunOutcome.REPRODUCED,
            (f"the rerun reached state {rerun.final.state_id} again.",),
            None,
        )
    position = next(
        index
        for index, (left, right) in enumerate(
            zip(
                zip(original.transitions, original.decisions, strict=True),
                zip(rerun.transitions, rerun.decisions, strict=True),
                strict=True,
            )
        )
        if left != right
    )
    return AdaptiveReplayAssessment(
        original.replay_id,
        verified,
        RerunOutcome.DIVERGED,
        (
            f"the same inputs diverged at observation {position}: the rule is not "
            "deterministic in something it read.",
        ),
        position,
    )
