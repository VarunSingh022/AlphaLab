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

Where the stream has got to
---------------------------
A seed says where a stream *starts*. Until v2.9 nothing said where it had
**reached**, and that omission was a correctness defect rather than a missing
convenience: a run that stopped and continued had to re-enter
:func:`id_scope`, which builds a fresh source positioned at zero, so the
continued run re-minted identifiers it had already used. Measured on v2.8.0, a
workload producing 41 identifiers uninterrupted produced up to 4 duplicates when
split and resumed, against zero for the uninterrupted control.

:class:`IdStreamPosition` is the missing fact, and it is two integers: the seed,
and the number of identifiers drawn from it. :func:`current_id_position` reads it
and :func:`id_source_for` rebuilds a source that has reached it. Deliberately
*not* the generator's internal state: two integers can be read by a person and
checked against the state they accompany, while a dump of generator words can be
neither, and would pin the recorded form to one PRNG implementation for good.

Continuation is guaranteed relative to the identifier algorithm this build mints
with. Changing that algorithm changes the identifiers a position replays, and is
therefore a compatibility event to be versioned and decided rather than a free
one. See ADR-0022.
"""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from random import Random
from typing import NewType
from uuid import UUID, uuid4

from alphalab.common.exceptions import AlphaLabValidationError

Identifier = NewType("Identifier", str)

#: Source of raw identifier strings for the current context; ``None`` means uuid4.
_ID_SOURCE: ContextVar[Callable[[], str] | None] = ContextVar("alphalab_id_source", default=None)


@dataclass(frozen=True, slots=True)
class IdStreamPosition:
    """How far a run's identifier stream has advanced.

    Two integers, and a complete description of the cursor: there is one stream,
    so there is one position, and no per-engine or per-category counter exists to
    record. ``draws`` counts *identifiers minted*, never events -- an event mints
    between zero and a couple of dozen depending on what it did, so a count
    derived from event totals would be a guess.

    ``seed is None`` means there is no deterministic stream: identifiers came
    from ``uuid4``, ``draws`` is ``0`` because no cursor exists to advance, and
    replaying the position promises nothing about future identifiers. Those
    identifiers cannot collide, so nothing is lost by the absence of a promise.

    Attributes:
        seed: The seed the stream was built from, or ``None`` when unseeded.
        draws: Identifiers minted from that seed so far.
    """

    seed: int | None = None
    draws: int = 0


class DeterministicIdSource:
    """Reproducible stream of UUID-shaped identifiers from an explicit seed.

    Backed by :class:`random.Random`, whose Mersenne Twister stream is
    guaranteed reproducible across Python versions for a given seed, so a run
    recorded today replays identically later. It is *not* a source of
    cryptographic randomness and is not meant to be one.

    The source counts what it has minted. That count is the only thing a stopped
    run needs in order to continue where it left off, and keeping it here rather
    than at the call sites is what leaves every ``new_id()`` caller untouched.
    """

    __slots__ = ("_draws", "_random", "_seed")

    def __init__(self, seed: int) -> None:
        self._seed = seed
        self._random = Random(seed)
        self._draws = 0

    @property
    def seed(self) -> int:
        """The seed this source was built from."""

        return self._seed

    @property
    def draws(self) -> int:
        """How many identifiers this source has minted."""

        return self._draws

    @property
    def position(self) -> IdStreamPosition:
        """Where this source's stream has reached."""

        return IdStreamPosition(self._seed, self._draws)

    def __call__(self) -> str:
        self._draws += 1
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


def id_source(seed: int | None) -> Callable[[], str] | None:
    """The identifier source a seed selects; ``None`` means ``uuid4``."""

    return None if seed is None else DeterministicIdSource(seed)


def current_id_position() -> IdStreamPosition:
    """Where the current context's identifier stream has reached.

    Answers for a :class:`DeterministicIdSource` and reports an unseeded position
    for anything else -- no source installed, so identifiers come from ``uuid4``,
    or a caller's own callable, whose cursor this module cannot know and will not
    invent. In both cases the honest answer is that there is no deterministic
    position, which is what :class:`IdStreamPosition` with ``seed=None`` says.

    This is an ambient read, and the only one: a state that stores its result
    afterwards can be captured as a pure function of itself, which is the point.
    """

    source = _ID_SOURCE.get()
    if isinstance(source, DeterministicIdSource):
        return source.position
    return IdStreamPosition()


def id_source_for(position: IdStreamPosition) -> Callable[[], str] | None:
    """A source whose stream has already reached ``position``.

    Built from the seed and advanced by ``draws``, so the next identifier it
    mints is the one the original source would have minted next. Advancing by
    replaying draws is deliberate: it uses the generator's own sequence rather
    than a jump-ahead of our own, so there is nothing to get subtly wrong, and it
    costs about 79 ns per draw -- roughly half a second for a run that minted a
    million identifiers, paid once on resume.

    Returns ``None`` for an unseeded position, matching :func:`id_source`, so the
    result can be handed to :func:`use_id_source` either way.
    """

    if position.seed is None:
        return None
    source = DeterministicIdSource(position.seed)
    for _ in range(position.draws):
        source()
    return source


def id_scope(seed: int | None) -> AbstractContextManager[None]:
    """Scope in which a run's identifiers are minted.

    A seeded run mints reproducible ids; an unseeded one keeps ``uuid4``.
    Entering the scope is what makes "the same run twice" mean the same orders
    and fills, not merely the same P&L.
    """

    if seed is None:
        return nullcontext()
    return use_id_source(id_source(seed))


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
