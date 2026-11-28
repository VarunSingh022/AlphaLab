"""What a record reads from a reference pack: a venue's settlement cycle (v4.3, ADR-0059).

A pack's format is data's (:mod:`alphalab.data.packs`); what its entries mean
is each domain's. A record's settlement rule is the instrument package's, so a
pack's settlement cycle is read here, as a universe's membership is read by
:func:`~alphalab.instrument.membership.universe_from_pack`. The rule is
declared on the record that trades there, and its fills settle by it over the
venue's calendar -- a calendar a pack states, read by
:func:`~alphalab.data.packs.calendar_from_pack`.
"""

from __future__ import annotations

from alphalab.conventions.exceptions import ConventionInputError
from alphalab.conventions.settlement import SettlementBasis, SettlementRule
from alphalab.data.exceptions import DataValidationError
from alphalab.data.packs import SETTLEMENT_CYCLE, ReferencePack

__all__ = ["settlement_from_pack"]


def settlement_from_pack(pack: ReferencePack, venue: str) -> SettlementRule:
    """The settlement cycle ``pack`` states for ``venue``, as a record declares it.

    The pack states one cycle for the venue, in force across its whole range.
    Cite ``pack.citation`` beside the run's record.

    Raises:
        DataValidationError: If the pack states no cycle for ``venue``, states a
            cycle that changes within its range, or states one that does not
            read: a basis that is not a :class:`SettlementBasis`, or a count of
            days that is not a non-negative integer the basis admits.
    """

    cycles = [entry for entry in pack.of(SETTLEMENT_CYCLE) if entry["venue"] == venue]
    if not cycles:
        held = sorted({entry["venue"] for entry in pack.of(SETTLEMENT_CYCLE)})
        raise DataValidationError(
            f"Pack {pack.pack_id} {pack.version} states no settlement cycle for {venue!r} (it "
            f"states {', '.join(held) or 'none'}); a missing cycle is refused, never assumed."
        )
    if len(cycles) != 1 or cycles[0]["effective_at"] != pack.valid_from:
        raise DataValidationError(
            f"Pack {pack.pack_id} states a settlement cycle for {venue} that changes within its "
            "range; a record declares one rule, and a change is refused rather than flattened."
        )
    (cycle,) = cycles
    basis, days = cycle["basis"], cycle["days"]
    if basis not in SettlementBasis.__members__ or not days.isdigit():
        raise DataValidationError(
            f"Pack {pack.pack_id} states {venue}'s cycle as {basis!r} {days!r}: a basis is one "
            f"of {', '.join(SettlementBasis.__members__)}, and its days a non-negative integer."
        )
    try:
        return SettlementRule(SettlementBasis[basis], int(days))
    except ConventionInputError as exc:
        raise DataValidationError(f"Pack {pack.pack_id}'s cycle for {venue}: {exc}") from exc
