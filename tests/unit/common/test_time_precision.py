"""The stated precision of an instant (ledger DAT-008, v3.12).

Instants are float Unix seconds, and that is kept: these tests pin the envelope
the documentation states, so a statement about it cannot drift from the numbers.
"""

from datetime import UTC, datetime

from alphalab.common.time import instant_resolution


def _instant(year: int) -> float:
    return datetime(year, 1, 1, tzinfo=UTC).timestamp()


def test_an_instant_resolves_to_a_quarter_microsecond_at_current_epochs() -> None:
    for year in (2005, 2026, 2037):
        assert instant_resolution(_instant(year)) == 2.0**-22
    # The band is 2**30 to 2**31 seconds: 10 January 2004 to 19 January 2038.
    assert instant_resolution(2.0**30) == 2.0**-22
    assert instant_resolution(2.0**31 - 1.0) == 2.0**-22


def test_two_moments_closer_than_the_resolution_are_one_instant() -> None:
    now = _instant(2026)
    assert now + 1e-9 == now  # a nanosecond is below the resolution
    assert now + instant_resolution(now) != now


def test_the_resolution_coarsens_with_the_epoch() -> None:
    assert instant_resolution(_instant(2040)) == 2.0**-21
    assert instant_resolution(_instant(1990)) == 2.0**-23
