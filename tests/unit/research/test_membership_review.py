"""A membership review lists the trades made outside membership as it was known (ADR-0059).

:func:`~alphalab.research.bias.membership_review` scores nothing (RES-001). It
reports each fill in an asset that was not a member when it filled -- as
``BEFORE_ANNOUNCEMENT`` when the asset's next addition was announced only after
the fill, which is the mark of a list that knew it would join, and as
``NOT_A_MEMBER`` otherwise -- and nothing for a fill in a member.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest

from alphalab.core.enums import Side
from alphalab.core.fill import Fill
from alphalab.core.ids import AssetId, FillId, OrderId
from alphalab.instrument.membership import HistoricalUniverse, MembershipChange, MembershipKind
from alphalab.research.bias import (
    MembershipFinding,
    MembershipFindingKind,
    MembershipReview,
    membership_review,
)
from alphalab.research.exceptions import ResearchValidationError

A, B, C, D = (str(uuid.UUID(int=n)) for n in range(1, 5))
ADD, REMOVE = MembershipKind.ADDITION, MembershipKind.REMOVAL

#: A and B from the start; B removed at 30 (announced 25); C added at 20
#: (announced 12); D never a member.
HISTORY = HistoricalUniverse(
    "IDX",
    (
        MembershipChange("IDX", A, ADD, 0.0, 0.0),
        MembershipChange("IDX", B, ADD, 0.0, 0.0),
        MembershipChange("IDX", C, ADD, 20.0, 12.0),
        MembershipChange("IDX", B, REMOVE, 30.0, 25.0),
    ),
)


def _fill(asset: str, at: float, side: Side = Side.BUY) -> Fill:
    return Fill(
        fill_id=FillId(str(uuid.uuid4())),
        order_id=OrderId(str(uuid.uuid4())),
        asset_id=AssetId(asset),
        side=side,
        quantity=Decimal(1),
        price=Decimal(100),
        filled_at=at,
    )


def test_fills_in_members_are_not_reported() -> None:
    review = membership_review([_fill(A, 1.0), _fill(B, 29.0), _fill(C, 20.0)], HISTORY)

    assert review == MembershipReview("IDX", 3, ())
    assert review.clean


@pytest.mark.parametrize(
    ("fill", "kind", "announced"),
    [
        # C traded before its addition was announced: a list that knew it would join.
        (_fill(C, 5.0), MembershipFindingKind.BEFORE_ANNOUNCEMENT, 12.0),
        # Between announcement and effect: known, and not yet a member.
        (_fill(C, 15.0), MembershipFindingKind.NOT_A_MEMBER, None),
        # After its removal: how a point-in-time strategy would leave, if late.
        (_fill(B, 30.0, Side.SELL), MembershipFindingKind.NOT_A_MEMBER, None),
        # Never a member.
        (_fill(D, 10.0), MembershipFindingKind.NOT_A_MEMBER, None),
    ],
)
def test_each_fill_outside_membership_is_reported_with_its_instant(
    fill: Fill, kind: MembershipFindingKind, announced: float | None
) -> None:
    (finding,) = membership_review([fill], HISTORY).findings

    assert finding == MembershipFinding(
        asset_id=fill.asset_id,
        at=fill.filled_at,
        side=fill.side.value,
        quantity=fill.quantity,
        kind=kind,
        announced_at=announced,
    )


def test_findings_keep_the_order_the_fills_were_given_in() -> None:
    review = membership_review([_fill(D, 3.0), _fill(A, 4.0), _fill(C, 1.0)], HISTORY)

    assert review.trades == 3
    assert [finding.asset_id for finding in review.findings] == [D, C]


@pytest.mark.parametrize(("fills", "universe"), [([object()], HISTORY), ([], "IDX")])
def test_the_review_reads_fills_against_a_historical_universe(fills: Any, universe: Any) -> None:
    with pytest.raises(ResearchValidationError):
        membership_review(fills, universe)
