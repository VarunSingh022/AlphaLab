"""Re-executing a run from its manifest, and saying where it diverged.

A :class:`~alphalab.lifecycle.reproducibility.ReproducibilityManifest` states
what a rerun needs, and :func:`~alphalab.lifecycle.reproducibility.assess_reproducibility`
compares a rerun's manifest with the original's. Until v3.13 the rerun itself
was the caller's to build and assess by hand, so reproducibility was asserted
by whoever bothered and demonstrated by nobody (ledger REP-003, OFE-020).

:func:`rerun_from_manifest` does the whole of it:

1. **Inputs first.** The dataset, the strategy fingerprint, the engine and its
   build are checked against the manifest *before* anything runs. A rerun of
   other inputs establishes nothing, so it is reported as
   :attr:`~alphalab.lifecycle.reproducibility.RerunOutcome.INPUTS_DIFFER`, with
   what differs, and not executed.
2. **The run.** ``run`` is called -- a zero-argument callable that drives the
   run through the canonical path (``BacktestEngine.run``) with the objects the
   manifest records by type only: the strategy instances, the sizing model, the
   simulator, the fill policy. AlphaLab holds none of them (ADR-0023), so they
   are the caller's to supply; the manifest says which.
3. **The verdict.** The rerun's own manifest is built from its result by
   :func:`~alphalab.lifecycle.reproducibility.manifest_for_run` and assessed
   against the original: ``REPRODUCED``, ``DIVERGED`` or ``INPUTS_DIFFER``.
4. **Where.** Given the original result as well, a divergence is located: the
   two runs' canonical records are compared field by field, and the first
   :data:`RERUN_DIFFERENCE_LIMIT` differing paths are reported --
   ``pipeline.fills[3].price: 100.02 -> 100.07`` -- so a reader sees which fill,
   order or equity point moved rather than only that a digest did.

Nothing here reads a clock, a file or an environment, and a rerun of a live run
is refused: its fills were a venue's, and no rerun recreates them.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from alphalab.backtesting.state import BacktestResult
from alphalab.lifecycle.exceptions import LifecycleInputError
from alphalab.lifecycle.fingerprint import EngineBuild, EngineIdentity, StrategyFingerprint
from alphalab.lifecycle.reproducibility import (
    ReproducibilityAssessment,
    ReproducibilityManifest,
    RerunOutcome,
    ResultKind,
    VersionedDataset,
    assess_reproducibility,
    digest_run,
    manifest_for_run,
    verify_manifest,
)
from alphalab.persistence.serializer import serialize
from alphalab.runtime.run import ExecutionMode
from alphalab.runtime.run_snapshot import capture as capture_run

__all__ = ["RERUN_DIFFERENCE_LIMIT", "RerunReport", "rerun_from_manifest"]

#: How many differing paths a diverged rerun reports. The first ones are what
#: locate a divergence; the rest are usually its consequences.
RERUN_DIFFERENCE_LIMIT: Final = 10


@dataclass(frozen=True, slots=True)
class RerunReport:
    """What re-executing one run from its manifest established.

    Attributes:
        manifest_id: The manifest the rerun was of.
        outcome: ``REPRODUCED``, ``DIVERGED`` or ``INPUTS_DIFFER``.
        detail: Why, in sentences: the inputs that differ, or the two results.
        assessment: The full assessment of the original against the rerun, or
            ``None`` when the inputs were refused before anything ran.
        rerun_manifest: The rerun's own manifest, or ``None`` when nothing ran.
        differences: For a divergence with the original result supplied, the
            first :data:`RERUN_DIFFERENCE_LIMIT` paths at which the two runs'
            records differ, in their canonical order -- keys sorted, sequences
            by index; otherwise empty.
    """

    manifest_id: str
    outcome: RerunOutcome
    detail: tuple[str, ...]
    assessment: ReproducibilityAssessment | None
    rerun_manifest: ReproducibilityManifest | None
    differences: tuple[str, ...]


def _input_differences(
    manifest: ReproducibilityManifest,
    dataset: VersionedDataset,
    fingerprint: StrategyFingerprint,
    engine: EngineIdentity,
    build: EngineBuild | None,
) -> list[str]:
    provenance = dataset.require_provenance()
    found: list[str] = []
    if provenance.dataset_version != manifest.dataset_version:
        found.append(f"dataset: {manifest.dataset_version} -> {provenance.dataset_version}")
    if provenance.content_hash != manifest.dataset_content_hash:
        found.append(
            f"dataset content: {manifest.dataset_content_hash} -> {provenance.content_hash}"
        )
    recorded = None if manifest.fingerprint is None else manifest.fingerprint.fingerprint
    if fingerprint.fingerprint != recorded:
        found.append(f"strategy: {recorded} -> {fingerprint.fingerprint}")
    if engine != manifest.engine:
        found.append(f"engine: {manifest.engine} -> {engine}")
    if manifest.build is not None:
        if build is None:
            found.append("engine build: recorded by the manifest, not supplied for the rerun")
        else:
            if build.source_digest != manifest.build.source_digest:
                found.append(
                    f"engine source: {manifest.build.source_digest} -> {build.source_digest}"
                )
            if build.tz_database != manifest.build.tz_database:
                found.append(
                    f"time-zone database: {manifest.build.tz_database} -> {build.tz_database}"
                )
    return found


def _render(value: Any) -> str:
    text = json.dumps(value, sort_keys=True)
    return text if len(text) <= 80 else text[:77] + "..."


def _differences(before: Any, after: Any, path: str, found: list[str]) -> None:
    """Depth-first, keys sorted: the first differing leaves, up to the limit."""

    if len(found) >= RERUN_DIFFERENCE_LIMIT:
        return
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            child = f"{path}.{key}" if path else str(key)
            if key not in before:
                found.append(f"{child}: absent -> {_render(after[key])}")
            elif key not in after:
                found.append(f"{child}: {_render(before[key])} -> absent")
            else:
                _differences(before[key], after[key], child, found)
            if len(found) >= RERUN_DIFFERENCE_LIMIT:
                return
        return
    if isinstance(before, list) and isinstance(after, list):
        for index in range(min(len(before), len(after))):
            _differences(before[index], after[index], f"{path}[{index}]", found)
            if len(found) >= RERUN_DIFFERENCE_LIMIT:
                return
        if len(before) != len(after):
            found.append(f"{path}: {len(before)} entries -> {len(after)}")
        return
    if before != after:
        found.append(f"{path}: {_render(before)} -> {_render(after)}")


def _record(result: BacktestResult) -> Any:
    return json.loads(serialize(capture_run(result.run)))


def rerun_from_manifest(
    manifest: ReproducibilityManifest,
    run: Callable[[], BacktestResult],
    *,
    dataset: VersionedDataset,
    fingerprint: StrategyFingerprint,
    engine: EngineIdentity,
    build: EngineBuild | None,
    original: BacktestResult | None = None,
) -> RerunReport:
    """Re-execute the run ``manifest`` identifies and say whether it reproduced.

    Args:
        manifest: The original run's manifest. It must verify.
        run: Drives the rerun through the canonical path with the supplied
            objects and returns its result. Called at most once, and not at all
            when the inputs below differ from the manifest's.
        dataset: The dataset to rerun on -- checked against the manifest's
            version and content digest.
        fingerprint: The strategy -- checked against the manifest's.
        engine: The engine running the rerun, typically
            :func:`~alphalab.lifecycle.fingerprint.running_engine`.
        build: Its build, typically
            :func:`~alphalab.lifecycle.fingerprint.running_build`; required
            when the manifest recorded one.
        original: The original result, to locate a divergence. Checked: it
            must be the result the manifest identifies.

    Raises:
        LifecycleInputError: If the manifest does not verify, is not of a run,
            is of a live run, or ``original`` is not the result it identifies.
    """

    if not verify_manifest(manifest):
        raise LifecycleInputError(
            f"Manifest {manifest.manifest_id} does not verify; a rerun matching an altered "
            "record would be evidence of nothing."
        )
    if manifest.kind is not ResultKind.RUN:
        raise LifecycleInputError(
            f"Manifest {manifest.manifest_id} is of a {manifest.kind.name}; this reruns runs."
        )
    if manifest.mode is ExecutionMode.LIVE:
        raise LifecycleInputError(
            "The manifest is of a live run: its fills were decided by a venue, and no rerun "
            "recreates them."
        )
    if original is not None and digest_run(original).result_id != manifest.result_id:
        raise LifecycleInputError(
            "The original result supplied is not the one the manifest identifies; a "
            "divergence located against it would be located against another run."
        )

    differing = _input_differences(manifest, dataset, fingerprint, engine, build)
    if differing:
        return RerunReport(
            manifest_id=manifest.manifest_id,
            outcome=RerunOutcome.INPUTS_DIFFER,
            detail=(
                *differing,
                "nothing was run: a rerun of other inputs establishes nothing about reproduction.",
            ),
            assessment=None,
            rerun_manifest=None,
            differences=(),
        )

    result = run()
    try:
        rerun_manifest = manifest_for_run(result, dataset, fingerprint, engine, build=build)
    except LifecycleInputError as error:
        return RerunReport(
            manifest_id=manifest.manifest_id,
            outcome=RerunOutcome.INPUTS_DIFFER,
            detail=(f"the rerun is not of the declared inputs: {error}",),
            assessment=None,
            rerun_manifest=None,
            differences=(),
        )
    assessment = assess_reproducibility(manifest, rerun_manifest)
    found: list[str] = []
    if assessment.rerun is RerunOutcome.DIVERGED and original is not None:
        _differences(_record(original), _record(result), "", found)
    return RerunReport(
        manifest_id=manifest.manifest_id,
        outcome=assessment.rerun,
        detail=assessment.rerun_detail,
        assessment=assessment,
        rerun_manifest=rerun_manifest,
        differences=tuple(found),
    )
