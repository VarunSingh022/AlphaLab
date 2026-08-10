"""Tests for the generic point-in-time correctness utility, previously untested
directly since it was only exercised indirectly through alphalab.macro."""

from dataclasses import dataclass

from alphalab.common.point_in_time import PointInTimeRecord, known_as_of


@dataclass(frozen=True, slots=True)
class _Record:
    """A minimal record satisfying PointInTimeRecord structurally, deliberately
    unrelated to any real domain type, to prove the utility is genuinely generic."""

    label: str
    reference_period: float
    release_date: float


def test_known_as_of_returns_none_for_empty_records() -> None:
    assert known_as_of((), as_of=1000.0) is None


def test_known_as_of_returns_none_before_any_release() -> None:
    record = _Record("A", reference_period=0.0, release_date=100.0)
    assert known_as_of((record,), as_of=50.0) is None


def test_known_as_of_returns_the_record_once_released() -> None:
    record = _Record("A", reference_period=0.0, release_date=100.0)
    assert known_as_of((record,), as_of=100.0) is record


def test_known_as_of_prefers_newer_reference_period() -> None:
    older = _Record("older", reference_period=0.0, release_date=100.0)
    newer = _Record("newer", reference_period=200.0, release_date=250.0)
    result = known_as_of((older, newer), as_of=1000.0)
    assert result is newer


def test_known_as_of_prefers_latest_release_within_same_reference_period() -> None:
    original = _Record("original", reference_period=0.0, release_date=100.0)
    revision = _Record("revision", reference_period=0.0, release_date=200.0)
    result = known_as_of((original, revision), as_of=1000.0)
    assert result is revision


def test_known_as_of_hides_unreleased_revision() -> None:
    """The core look-ahead-bias-prevention behavior: a revision not yet released
    at as_of must not be visible, even though it exists in the input tuple."""
    original = _Record("original", reference_period=0.0, release_date=100.0)
    revision = _Record("revision", reference_period=0.0, release_date=200.0)
    result = known_as_of((original, revision), as_of=150.0)
    assert result is original


def test_known_as_of_is_generic_across_unrelated_types() -> None:
    """Confirms the Protocol-based genericity actually works at runtime for a type
    that has nothing to do with alphalab.macro, the package this was extracted from."""
    record = _Record("generic", reference_period=0.0, release_date=100.0)
    result: _Record | None = known_as_of((record,), as_of=200.0)
    assert result is not None
    assert result.label == "generic"


def test_point_in_time_record_protocol_is_importable() -> None:
    assert PointInTimeRecord is not None
