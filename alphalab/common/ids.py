"""Shared identifier helpers.

Every identifier AlphaLab mints -- event ids, execution ids, order ids,
transaction ids -- comes from :func:`new_id`. By default that is ``uuid4``, so
two runs of the same workload produce states that agree on every quantity and
disagree on every identifier. For a backtest or a replay that is not good
enough: "the same run twice" has to mean the same orders and the same fills,
not merely the same P&L.

:func:`use_id_source` makes the source explicit and scoped. Inside the block,
identifiers come from the supplied source -- typically a
:class:`DeterministicIdSource` built from a recorded seed -- and outside it,
from ``uuid4`` exactly as before. The source lives in a :class:`~contextvars.ContextVar`,
so it is bound to the running context rather than to the process, nests
correctly, and is restored on exit even if the block raises.

This is the one deliberate ambient value on the execution path, and it exists
because the alternative -- threading an id source parameter through every
engine method -- would put a plumbing argument on APIs that have nothing to do
with reproducibility. The seed is recorded on the run that installs it (see
:class:`alphalab.backtesting.BacktestConfig`), so a reproducible run always
says what made it reproducible.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from random import Random
from typing import NewType
from uuid import UUID, uuid4

from alphalab.common.exceptions import AlphaLabValidationError

Identifier = NewType("Identifier", str)

#: Source of raw identifier strings for the current context; ``None`` means uuid4.
_ID_SOURCE: ContextVar[Callable[[], str] | None] = ContextVar("alphalab_id_source", default=None)


class DeterministicIdSource:
    """Reproducible stream of UUID-shaped identifiers from an explicit seed.

    Backed by :class:`random.Random`, whose Mersenne Twister stream is
    guaranteed reproducible across Python versions for a given seed, so a run
    recorded today replays identically later. It is *not* a source of
    cryptographic randomness and is not meant to be one.
    """

    __slots__ = ("_random", "_seed")

    def __init__(self, seed: int) -> None:
        self._seed = seed
        self._random = Random(seed)

    @property
    def seed(self) -> int:
        """The seed this source was built from."""

        return self._seed

    def __call__(self) -> str:
        return str(UUID(int=self._random.getrandbits(128), version=4))


def new_id() -> Identifier:
    """Return a new identifier from the current source (``uuid4`` by default)."""

    source = _ID_SOURCE.get()
    if source is None:
        return Identifier(str(uuid4()))
    return Identifier(source())


@contextmanager
def use_id_source(source: Callable[[], str] | None) -> Iterator[None]:
    """Mint identifiers from ``source`` for the duration of the block.

    Passing ``None`` restores ``uuid4`` inside the block, which is how a run
    opts one section back out of determinism.
    """

    token = _ID_SOURCE.set(source)
    try:
        yield
    finally:
        _ID_SOURCE.reset(token)


def is_uuid(value: str) -> bool:
    """Return whether a string is a valid UUID."""

    try:
        UUID(value)
    except ValueError:
        return False
    return True


def require_uuid(value: str, field_name: str) -> Identifier:
    """Validate and return a UUID-backed identifier."""

    if not value or not is_uuid(value):
        raise AlphaLabValidationError(f"{field_name} must be a valid UUID")
    return Identifier(value)
