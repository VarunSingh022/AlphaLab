"""An extractor states every choice, its identity names them, and a record is held to its series.

ADR-0055 sections 2 and 3. An extractor has no defaults (AC-12) and refuses a
choice that is not one of its kind's; its canonical key is pinned here, because
``extractor_id`` is what two captures are compared under; a
:class:`~alphalab.backtesting.capture.CaptureRecord` refuses a series that is
not one, or a ``series_id`` that is not its own.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import MISSING, fields, replace
from typing import Any

import pytest

from alphalab.backtesting.capture import (
    CAPTURE_EXTRACTOR_SCHEME,
    CAPTURE_SERIES_SCHEME,
    CAPTURE_TRACE_SCHEME,
    EVERY_INSTANT,
    CaptureError,
    CaptureRecord,
    CaptureSpec,
    CashFlows,
    Costs,
    FixedInterval,
    Insolvency,
    ReturnExtractor,
    TraceCounts,
    canonical_captured_series_key,
    canonical_extractor_key,
)

NET_USD = ReturnExtractor(EVERY_INSTANT, Costs.NET, CashFlows.INCLUDED, "USD", Insolvency.REFUSE)
DAILY_GROSS = ReturnExtractor(
    FixedInterval(86400, 0), Costs.GROSS, CashFlows.EXCLUDED, "EUR", Insolvency.TRUNCATE
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_the_schemes_are_the_records() -> None:
    assert CAPTURE_EXTRACTOR_SCHEME == "alphalab.capture.extractor.v1"
    assert CAPTURE_SERIES_SCHEME == "alphalab.capture.series.v1"
    assert CAPTURE_TRACE_SCHEME == "alphalab.capture.trace.v1"


# --- the extractor ------------------------------------------------------------------


def test_an_extractor_has_no_defaults() -> None:
    for item in (*fields(ReturnExtractor), *fields(CaptureSpec), *fields(FixedInterval)):
        assert item.default is MISSING and item.default_factory is MISSING, item.name


def test_the_canonical_key_is_pinned() -> None:
    assert canonical_extractor_key(NET_USD) == (
        '{"cash_flows":"INCLUDED","costs":"NET","currency":"USD","grid":{"kind":"EVERY_INSTANT"},'
        '"insolvency":"REFUSE","scheme":"alphalab.capture.extractor.v1"}'
    )
    assert canonical_extractor_key(DAILY_GROSS) == (
        '{"cash_flows":"EXCLUDED","costs":"GROSS","currency":"EUR","grid":{"kind":"FIXED_INTERVAL",'
        '"origin":0.0,"seconds":86400.0},"insolvency":"TRUNCATE",'
        '"scheme":"alphalab.capture.extractor.v1"}'
    )
    assert NET_USD.extractor_id == (
        "33201d905f299b819fe4070bb8187eba0dcf9fc5e736aabac87b274c79099ee8"
    )
    assert DAILY_GROSS.extractor_id == _sha(canonical_extractor_key(DAILY_GROSS))


@pytest.mark.parametrize(
    "changed",
    [
        {"grid": FixedInterval(1.0, 0.0)},
        {"costs": Costs.GROSS},
        {"cash_flows": CashFlows.EXCLUDED},
        {"currency": "EUR"},
        {"insolvency": Insolvency.TRUNCATE},
    ],
)
def test_every_choice_moves_the_extractor_id(changed: dict[str, Any]) -> None:
    assert replace(NET_USD, **changed).extractor_id != NET_USD.extractor_id


def test_a_fixed_interval_is_named_by_its_interval_and_its_origin() -> None:
    ids = {
        ReturnExtractor(grid, Costs.NET, CashFlows.INCLUDED, "USD", Insolvency.REFUSE).extractor_id
        for grid in (FixedInterval(60, 0), FixedInterval(60, 30), FixedInterval(30, 0))
    }
    assert len(ids) == 3
    assert FixedInterval(60, 0) == FixedInterval(60.0, 0.0)


@pytest.mark.parametrize(
    "changed",
    [
        {"grid": "EVERY_INSTANT"},
        {"costs": "NET"},
        {"cash_flows": CashFlows.INCLUDED.name},
        {"insolvency": None},
        {"currency": ""},
        {"currency": "   "},
        {"currency": 840},
    ],
)
def test_a_choice_that_is_not_one_of_its_kinds_is_refused(changed: dict[str, Any]) -> None:
    with pytest.raises(CaptureError, match="ReturnExtractor"):
        replace(NET_USD, **changed)


@pytest.mark.parametrize(
    ("seconds", "origin"),
    [(0, 0), (-1, 0), (math.inf, 0), (math.nan, 0), (1, math.inf), (True, 0), ("60", 0)],
)
def test_a_fixed_interval_is_positive_and_finite(seconds: Any, origin: Any) -> None:
    with pytest.raises(CaptureError, match="FixedInterval"):
        FixedInterval(seconds, origin)


@pytest.mark.parametrize(
    ("extractor", "trace"), [(NET_USD, 1), (NET_USD, None), ("NET", True), (None, False)]
)
def test_a_spec_is_an_extractor_and_a_boolean(extractor: Any, trace: Any) -> None:
    with pytest.raises(CaptureError, match="CaptureSpec"):
        CaptureSpec(extractor, trace)


# --- the record ---------------------------------------------------------------------

INSTANTS = (1.0, 2.0, 3.5)
RETURNS = (0.01, -0.025)
SERIES_ID = "ce8de49e974c9d5902deb4952c5778ba5fd5f6652f12525d5d6188f8986ac451"
DIGEST = "a" * 64


def _record(**changed: Any) -> CaptureRecord:
    base: dict[str, Any] = {
        "extractor_id": NET_USD.extractor_id,
        "instants": INSTANTS,
        "returns": RETURNS,
        "series_id": SERIES_ID,
        "insolvent_at": None,
        "steps": 4,
        "configuration_id": DIGEST,
        "trace_id": DIGEST,
        "traced": TraceCounts(1, 2, 3, 4),
    }
    base.update(changed)
    return CaptureRecord(**base)


def test_the_series_key_is_pinned() -> None:
    assert canonical_captured_series_key(INSTANTS, RETURNS, None) == (
        '{"insolvent_at":null,"instants":[1.0,2.0,3.5],"returns":[0.01,-0.025],'
        '"scheme":"alphalab.capture.series.v1"}'
    )
    assert _sha(canonical_captured_series_key(INSTANTS, RETURNS, None)) == SERIES_ID


def test_a_record_of_a_series_is_accepted_and_names_its_periods() -> None:
    record = _record()

    assert record.period_ends == (2.0, 3.5)
    assert _record(configuration_id=None, trace_id=None, traced=None).trace_id is None


def test_an_empty_series_is_one() -> None:
    record = _record(
        instants=(), returns=(), series_id=_sha(canonical_captured_series_key((), (), None))
    )
    assert record.period_ends == ()


@pytest.mark.parametrize(
    ("changed", "match"),
    [
        ({"returns": (0.01,)}, "periods"),
        ({"returns": (0.01, math.inf)}, "finite"),
        ({"instants": (1.0, 1.0, 3.5)}, "ascending"),
        ({"insolvent_at": 2.0}, "last instant"),
        ({"series_id": "0" * 64}, "identity"),
        ({"trace_id": None}, "whole"),
        ({"configuration_id": "not-a-digest"}, "SHA-256"),
    ],
)
def test_a_record_is_held_to_its_series(changed: dict[str, Any], match: str) -> None:
    with pytest.raises(CaptureError, match=match):
        _record(**changed)


def test_an_insolvency_is_the_last_instant_and_part_of_the_identity() -> None:
    truncated = _sha(canonical_captured_series_key(INSTANTS, RETURNS, 3.5))
    assert truncated != SERIES_ID
    assert _record(insolvent_at=3.5, series_id=truncated).insolvent_at == 3.5
