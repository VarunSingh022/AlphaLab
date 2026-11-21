"""Point-in-time panels: what was known at each event, beside the label it earned (v4.3, plan I.3).

A :class:`PointInTimePanel` joins feature panels -- computed by the factor
library over prices or over knowledge frames, so each value is what was
knowable at its instant (GAP-13) -- to a :class:`~alphalab.ml.labels.LabelSet`.
Each row is one labelled event: the features at the event's ``start``, the
label, and the ``end`` at which the label became known. An event a feature has
no value for at its start is counted in :attr:`PointInTimePanel.missing`, never
filled.

Three ways to take rows, each point-in-time:

* :meth:`PointInTimePanel.known_by` keeps the rows whose label was known by an
  instant: a model fitted at that instant could have been fitted on exactly
  these.
* :meth:`PointInTimePanel.at` keeps the rows starting at given instants, as a
  split from :mod:`alphalab.research` names them.
* :meth:`PointInTimePanel.purge_policy` gives research's purging the latest
  label end at each instant, so ``cross_validation_splits`` and
  ``combinatorial_purged_splits`` purge a panel as they purge any series:
  splits have one authority.

:meth:`PointInTimePanel.training_set` weighs the rows equally or by their
average uniqueness among themselves.

``alphalab.ml.dataset.build_dataset_from_feature_store`` is kept, and is not
point-in-time: it reads each feature's latest value.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from alphalab.factor_library.panel import FeaturePanel
from alphalab.ml.exceptions import MLInputError
from alphalab.ml.labels import LabelSet, average_uniqueness
from alphalab.ml.protocol import FeatureMatrix, TrainingSet
from alphalab.research.purging import PurgePolicy

__all__ = [
    "PANEL_SCHEME",
    "PointInTimePanel",
    "SampleWeights",
    "point_in_time_panel",
]

#: The scheme tag a panel's identity is derived under: its first line.
PANEL_SCHEME: Final = "alphalab.point_in_time_panel.v1"


class SampleWeights(StrEnum):
    """How a panel's rows are weighed in a training set."""

    #: Every row counts once.
    EQUAL = "equal"
    #: Each row by its average uniqueness among the rows taken (Lopez de Prado, ch. 4).
    UNIQUENESS = "uniqueness"


@dataclass(frozen=True, slots=True)
class PointInTimePanel:
    """Labelled events with the features known at their start.

    Attributes:
        features: One row per event, the features in the order stated.
        subjects: Each row's subject.
        starts: Each row's event instant.
        ends: Each row's label end: the instant the label became known.
        labels: Each row's label.
        outcomes: Each row's return from start to end.
        clocks: Each subject's observation instants, which uniqueness counts
            in.
        lineage: How each feature and the labels were made, one line each:
            definitions, methods and sources, never the identity of a label set
            whose later labels a panel taken from it does not hold.
        dropped: Labelled events left out because a feature had no value at
            their start, as ``(subject, start, end)``; a panel taken from this
            one keeps those its own rule keeps.
    """

    features: FeatureMatrix
    subjects: tuple[str, ...]
    starts: tuple[float, ...]
    ends: tuple[float, ...]
    labels: tuple[float, ...]
    outcomes: tuple[float, ...]
    clocks: Mapping[str, tuple[float, ...]]
    lineage: tuple[str, ...]
    dropped: tuple[tuple[str, float, float], ...]

    def __post_init__(self) -> None:
        count = len(self.features.rows)
        for name in ("subjects", "starts", "ends", "labels", "outcomes"):
            if len(getattr(self, name)) != count:
                raise MLInputError(
                    f"A panel of {count} rows holds {len(getattr(self, name))} {name}."
                )

    def __len__(self) -> int:
        return len(self.features.rows)

    @property
    def missing(self) -> int:
        """How many labelled events have no row: a feature was unknown at their start."""

        return len(self.dropped)

    @property
    def names(self) -> tuple[str, ...]:
        """The features, in column order."""

        return self.features.names

    @property
    def instants(self) -> tuple[float, ...]:
        """Every instant an event starts at, ascending: what research's splits partition."""

        return tuple(sorted(set(self.starts)))

    @property
    def panel_id(self) -> str:
        """The derived identity: the lineage and every row, in order."""

        lines = [PANEL_SCHEME, *self.lineage, "|".join(self.names)]
        for index, row in enumerate(self.features.rows):
            lines.append(
                f"{self.subjects[index]}|{self.starts[index]!r}|{self.ends[index]!r}|"
                f"{self.labels[index]!r}|{self.outcomes[index]!r}|"
                + ",".join(repr(value) for value in row)
            )
        return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()

    def _rows(
        self, keep: Iterable[int], dropped: Iterable[tuple[str, float, float]]
    ) -> PointInTimePanel:
        kept = list(keep)
        return PointInTimePanel(
            features=FeatureMatrix(self.names, tuple(self.features.rows[i] for i in kept)),
            subjects=tuple(self.subjects[i] for i in kept),
            starts=tuple(self.starts[i] for i in kept),
            ends=tuple(self.ends[i] for i in kept),
            labels=tuple(self.labels[i] for i in kept),
            outcomes=tuple(self.outcomes[i] for i in kept),
            clocks=self.clocks,
            lineage=self.lineage,
            dropped=tuple(dropped),
        )

    def known_by(self, instant: float) -> PointInTimePanel:
        """The rows whose label was known by ``instant``: ``end <= instant``."""

        return self._rows(
            (i for i, end in enumerate(self.ends) if end <= instant),
            (event for event in self.dropped if event[2] <= instant),
        )

    def at(self, instants: Iterable[float]) -> PointInTimePanel:
        """The rows starting at ``instants``, such as a split's ``train`` or ``validation``."""

        wanted = set(instants)
        return self._rows(
            (i for i, start in enumerate(self.starts) if start in wanted),
            (event for event in self.dropped if event[1] in wanted),
        )

    def purge_policy(self, *, embargo_seconds: float) -> PurgePolicy:
        """Research's purge policy for this panel: each instant's latest label end.

        An instant is a training instant only if none of its labels reaches
        into a validation span, so the instant's end is the latest of its rows'.

        Raises:
            MLInputError: If the panel holds no row.
        """

        if not self.starts:
            raise MLInputError("An empty panel has no label ends to purge by.")
        ends: dict[float, float] = defaultdict(lambda: float("-inf"))
        for start, end in zip(self.starts, self.ends, strict=True):
            ends[start] = max(ends[start], end)
        return PurgePolicy(label_ends=MappingProxyType(dict(ends)), embargo_seconds=embargo_seconds)

    def uniqueness(self) -> tuple[float, ...]:
        """Each row's average uniqueness among this panel's rows."""

        return average_uniqueness(
            list(zip(self.subjects, self.starts, self.ends, strict=True)), self.clocks
        )

    def training_set(self, *, weights: SampleWeights) -> TrainingSet:
        """The rows as a :class:`~alphalab.ml.protocol.TrainingSet`, weighed as stated.

        Raises:
            MLInputError: If the panel holds no row, or ``weights`` is not a
                SampleWeights.
        """

        if not isinstance(weights, SampleWeights):
            raise MLInputError(f"weights is a SampleWeights, got {weights!r}.")
        return TrainingSet(
            self.features,
            self.labels,
            self.uniqueness() if weights is SampleWeights.UNIQUENESS else None,
        )


def _lineage(name: str, panel: FeaturePanel) -> str:
    definition = panel.definition
    steps = ", ".join(str(step) for step in panel.transforms) or "none"
    return (
        f"feature {name}: {definition.feature_id} ({definition.kind.name}, window "
        f"{definition.window}) on {panel.dataset_version}, transforms {steps}"
    )


def point_in_time_panel(features: Mapping[str, FeaturePanel], labels: LabelSet) -> PointInTimePanel:
    """Join ``features`` to ``labels``: each event's features at its start, beside its label.

    ``features`` maps the names the panel's columns take to feature panels, in
    the order the columns take. Rows are ordered by start, then subject.

    Raises:
        MLInputError: If ``features`` is empty, a value is not a FeaturePanel,
            ``labels`` is not a LabelSet, or a panel reports its instants in
            another zone than the labels.
    """

    if not features:
        raise MLInputError("A point-in-time panel needs at least one feature.")
    if not isinstance(labels, LabelSet):
        raise MLInputError(f"labels is a LabelSet, got {labels!r}.")
    names = tuple(features)
    for name, panel in features.items():
        if not isinstance(panel, FeaturePanel):
            raise MLInputError(f"Feature {name!r} is not a FeaturePanel: {panel!r}.")
        if panel.timezone_name != labels.timezone_name:
            raise MLInputError(
                f"Feature {name!r} reports its instants in {panel.timezone_name!r} and the "
                f"labels in {labels.timezone_name!r}; join them in one zone."
            )
    rows: list[tuple[float, ...]] = []
    kept = []
    dropped: list[tuple[str, float, float]] = []
    for label in labels.labels:
        values = []
        for name in names:
            value = features[name].rows.get(label.start, {}).get(label.subject)
            if value is None:
                break
            values.append(value)
        if len(values) < len(names):
            dropped.append((label.subject, label.start, label.end))
            continue
        rows.append(tuple(values))
        kept.append(label)
    lineage = (
        *(_lineage(name, features[name]) for name in names),
        f"labels: {labels.method}, from {labels.source} on {labels.dataset_version} "
        f"in {labels.timezone_name}",
    )
    return PointInTimePanel(
        features=FeatureMatrix(names, tuple(rows)),
        subjects=tuple(label.subject for label in kept),
        starts=tuple(label.start for label in kept),
        ends=tuple(label.end for label in kept),
        labels=tuple(label.value for label in kept),
        outcomes=tuple(label.outcome for label in kept),
        clocks=labels.clocks,
        lineage=lineage,
        dropped=tuple(dropped),
    )
