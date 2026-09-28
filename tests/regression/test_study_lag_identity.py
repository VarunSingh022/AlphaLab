"""The declared implementation lag enters a study's identity unambiguously (DAT-003).

``implementation_lag`` is also a legal *input role*, so rendering the declared
lag as ``implementation_lag=0`` would let a study that names an input of that
role and declares no lag render -- and hash -- exactly like one that declares a
lag of 0. The lag is rendered with ``:`` instead, which no input or parameter
line holds, and the manifest's reader of a study's inputs skips it.
"""

from alphalab.factor_library.definition import FeatureDefinition, FeatureField, FeatureKind
from alphalab.lifecycle.reproducibility import _study_inputs
from alphalab.research import ResearchStudy
from alphalab.research.study import canonical_study_key

FEATURE = FeatureDefinition("m", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=2)


def _study(**changes: object) -> ResearchStudy:
    fields: dict[str, object] = {
        "study_name": "lagged",
        "dataset_version": "panel@1",
        "universe": ("AAA",),
        "features": (FEATURE,),
        "horizons": (1,),
    }
    fields.update(changes)
    return ResearchStudy(**fields)  # type: ignore[arg-type]


def test_an_input_named_like_the_lag_is_not_a_declared_lag() -> None:
    declared = _study(inputs={"a": "b"}, implementation_lag=0)
    impostor = _study(inputs={"a": "b", "implementation_lag": "0"})
    assert canonical_study_key(declared) != canonical_study_key(impostor)
    assert declared.study_id != impostor.study_id


def test_the_manifest_reads_a_studys_inputs_past_its_declared_lag() -> None:
    study = _study(inputs={"events": "evt@1", "regime": "reg@2"}, implementation_lag=3)
    assert _study_inputs(canonical_study_key(study)) == (
        ("events", "evt@1"),
        ("regime", "reg@2"),
    )
    assert _study_inputs(canonical_study_key(_study(implementation_lag=1))) == ()


def test_an_undeclared_lag_leaves_the_pre_v311_key_unchanged() -> None:
    key = canonical_study_key(_study())
    assert "implementation_lag" not in key
    assert key.endswith("parameters")
