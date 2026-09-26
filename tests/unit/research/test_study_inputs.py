"""A study names every versioned input it reads, and old studies keep their identity.

v3.7 lets a study read point-in-time events, alternative data and fundamentals
beside its dataset. Each is named by its derived identity in
``ResearchStudy.inputs``, so the study's identity -- and a reproducibility
manifest over its result -- commits to exactly those versions. A study that
names none renders its key exactly as it did before v3.7.
"""

from __future__ import annotations

import hashlib

import pytest

from alphalab.factor_library.definition import FeatureDefinition, FeatureField, FeatureKind
from alphalab.research import (
    STUDY_KEY_SCHEME,
    ResearchStudy,
    ResearchValidationError,
    canonical_study_key,
)

MOMENTUM = FeatureDefinition(
    feature_id="momentum_5",
    kind=FeatureKind.MOMENTUM,
    source_field=FeatureField.CLOSE,
    window=5,
)


def _study(**changes: object) -> ResearchStudy:
    arguments: dict[str, object] = {
        "study_name": "example",
        "dataset_version": "panel@abc123",
        "universe": ("AAA", "BBB"),
        "features": (MOMENTUM,),
        "horizons": (1, 5),
        "splits": "scheme",
        "parameters": {"cost": 0.001},
        "seed": 7,
        "notes": "note",
    }
    arguments.update(changes)
    return ResearchStudy(**arguments)  # type: ignore[arg-type]


def test_a_study_with_no_inputs_keeps_its_pre_v37_identity() -> None:
    """The v3.6 rendering, recomputed by hand: no inputs section, same digest."""

    rendered = "\n".join(
        [
            STUDY_KEY_SCHEME,
            "name=example",
            "dataset=panel@abc123",
            "splits=scheme",
            "seed=7",
            "notes=note",
            "universe",
            "AAA",
            "BBB",
            "features",
            MOMENTUM.feature_version,
            "horizons",
            "1",
            "5",
            "parameters",
            "cost=0.001",
        ]
    )

    assert canonical_study_key(_study()) == rendered
    assert _study().study_id == "example@" + hashlib.sha256(rendered.encode()).hexdigest()


def test_inputs_render_sorted_after_everything_else() -> None:
    study = _study(inputs={"fundamentals": "fund@1", "events": "earnings@2"})

    assert canonical_study_key(study).splitlines()[-3:] == [
        "inputs",
        "events=earnings@2",
        "fundamentals=fund@1",
    ]


def test_an_input_version_changes_the_study_identity_and_order_does_not() -> None:
    base = _study(inputs={"events": "earnings@2", "regime": "vol@9"})

    assert base.study_id == _study(inputs={"regime": "vol@9", "events": "earnings@2"}).study_id
    assert base.study_id != _study(inputs={"events": "earnings@3", "regime": "vol@9"}).study_id
    assert base.study_id != _study().study_id


def test_the_inputs_cannot_be_changed_after_the_study_is_built() -> None:
    supplied = {"events": "earnings@2"}
    study = _study(inputs=supplied)
    supplied["events"] = "earnings@999"

    assert study.inputs["events"] == "earnings@2"
    with pytest.raises(TypeError):
        study.inputs["events"] = "x"  # type: ignore[index]


@pytest.mark.parametrize(
    ("inputs", "match"),
    [
        ({"Events": "e@1"}, "dotted lowercase identifier"),
        ({"events=x": "e@1"}, "dotted lowercase identifier"),
        ({"events": ""}, "one line"),
        ({"events": " e@1"}, "one line"),
        ({"events": "e@1\nextra"}, "one line"),
    ],
)
def test_a_malformed_input_is_refused(inputs: dict[str, str], match: str) -> None:
    with pytest.raises(ResearchValidationError, match=match):
        _study(inputs=inputs)
