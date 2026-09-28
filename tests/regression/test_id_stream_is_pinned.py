"""The seeded identifier stream is pinned, and its seed means one stream (ledger DET-003).

A recorded run replays into the same identifiers only if the stream a seed mints
never changes. Python's documentation guarantees ``random()``'s sequence across
versions and says nothing so explicit about ``getrandbits``, which is what the
stream draws from -- so the stream is held here, value by value. A Python that
minted different identifiers fails this test instead of silently replaying a
recorded run into different ones.

``random.Random`` seeds an integer by its absolute value, so until v3.10 seeds
``-7`` and ``7`` minted the same stream. A negative seed is now refused.
"""

import pytest

from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.common.ids import DeterministicIdSource, id_scope, new_id
from tests.integration.harness import backtest_config

#: Recorded with CPython 3.12. Changing any of these is a change to every seeded
#: run's identities, and needs the same care as a snapshot schema.
PINNED = {
    0: [
        "e3e70682-c209-4cac-a29f-6fbed82c07cd",
        "f728b4fa-4248-4e3a-8a5d-2f346baa9455",
        "eb1167b3-67a9-4378-bc65-c1e582e2e662",
    ],
    20220905: [
        "79281aba-a8d6-4411-89b1-da6bc3fd945b",
        "f15eb1b2-25d3-4a5b-aa2e-43afc45d9340",
        "42881680-89ca-4b90-8bb5-9979b140e0a1",
    ],
}


@pytest.mark.parametrize("seed", sorted(PINNED))
def test_a_seed_mints_the_pinned_stream(seed: int) -> None:
    source = DeterministicIdSource(seed)

    assert [source() for _ in PINNED[seed]] == PINNED[seed]


def test_the_thousandth_identifier_is_pinned_too() -> None:
    source = DeterministicIdSource(7)
    for _ in range(999):
        source()

    assert source() == "dd986619-08cc-463c-8a4e-ecb2e277e9db"
    assert source.draws == 1000


def test_the_scope_mints_from_the_same_stream() -> None:
    with id_scope(20220905):
        assert [str(new_id()) for _ in range(3)] == PINNED[20220905]


@pytest.mark.parametrize("seed", [-1, -20220905, True, 1.0, "7"])
def test_a_seed_that_is_not_a_non_negative_integer_is_refused(seed: object) -> None:
    with pytest.raises(AlphaLabValidationError, match="non-negative integer"):
        DeterministicIdSource(seed)  # type: ignore[arg-type]


def test_a_run_refuses_a_negative_seed_before_it_starts() -> None:
    with pytest.raises(AlphaLabValidationError, match=r"RunConfig\.seed"):
        backtest_config("STRAT", seed=-7)
