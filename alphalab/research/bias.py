"""The facts a bias review of a finished run starts from.

Look-ahead and survivorship cannot be read from a return series after the fact.
Until v3.12 this module tried: it called a win rate above 95% on short trades
"look-ahead", a volatility under 5% "survivorship", and turned both into risks
and a score by constants nobody chose (ledger RES-001). AlphaLab prevents those
biases where they arise instead -- point-in-time datasets read ``as_of`` an
instant, delisting returns kept, purged and embargoed splits -- and this report
carries what the run itself shows, for a reviewer to read.
"""

from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, unique

from alphalab.core.fill import Fill
from alphalab.instrument.membership import HistoricalUniverse, MembershipChange, MembershipKind
from alphalab.research.exceptions import ResearchValidationError
from alphalab.research.metrics import calculate_volatility
from alphalab.research.protocol import ResearchPayload


@dataclass(frozen=True, slots=True)
class BiasReport:
    """What the run shows that bears on bias -- measured, not scored.

    Attributes:
        win_rate: The fraction of trades with a positive profit; ``None``
            without trades.
        mean_trade_duration_seconds: The mean holding time; ``None`` without
            trades.
        annualized_volatility: Of the returns, annualized with the payload's
            periods; ``None`` with fewer than two returns.
        parameter_count: How many parameters the configuration has.
        trade_count: How many trades the run made.
        observations: How many returns the run produced.
        trades_per_parameter: ``trade_count / parameter_count``; ``None``
            without parameters.
    """

    win_rate: float | None
    mean_trade_duration_seconds: float | None
    annualized_volatility: float | None
    parameter_count: int
    trade_count: int
    observations: int
    trades_per_parameter: float | None


def detect_bias(payload: ResearchPayload) -> BiasReport:
    """Measure the run's facts a bias review reads; see the module docstring."""

    trades = payload.trades
    count = len(trades)
    parameters = len(payload.parameters)
    return BiasReport(
        win_rate=sum(1 for t in trades if t.pnl > 0) / count if count else None,
        mean_trade_duration_seconds=(
            sum(t.duration_seconds for t in trades) / count if count else None
        ),
        annualized_volatility=(
            calculate_volatility(payload.returns, payload.periods_per_year)
            if len(payload.returns) >= 2
            else None
        ),
        parameter_count=parameters,
        trade_count=count,
        observations=len(payload.returns),
        trades_per_parameter=count / parameters if parameters else None,
    )


# --- membership as it was known (v4.3, ADR-0059) ------------------------------------


@unique
class MembershipFindingKind(Enum):
    """How a trade stands against a universe's membership as it was known."""

    #: The asset was not a member when it traded, and nothing announced by then
    #: said it would be.
    NOT_A_MEMBER = "NOT_A_MEMBER"
    #: The asset was not yet a member when it traded, and the addition that made
    #: it one was announced only later: a list of today's members, or one of
    #: effective dates alone, would have traded it.
    BEFORE_ANNOUNCEMENT = "BEFORE_ANNOUNCEMENT"


@dataclass(frozen=True, slots=True)
class MembershipFinding:
    """One trade made outside a universe's membership as it was known.

    Attributes:
        asset_id: What traded.
        at: When it filled.
        side: ``"buy"`` or ``"sell"``.
        quantity: How much.
        kind: :class:`MembershipFindingKind`.
        announced_at: For :attr:`~MembershipFindingKind.BEFORE_ANNOUNCEMENT`,
            when the asset's next addition was announced; otherwise ``None``.
    """

    asset_id: str
    at: float
    side: str
    quantity: Decimal
    kind: MembershipFindingKind
    announced_at: float | None


@dataclass(frozen=True, slots=True)
class MembershipReview:
    """A run's trades against a universe's membership as known at each (ADR-0059).

    It scores nothing (RES-001): it lists what happened, for a reviewer to read.
    A sale after a removal is how a point-in-time strategy leaves; the finding
    says when, and the side says which.

    Attributes:
        universe: The universe reviewed against.
        trades: How many fills were reviewed.
        findings: Every fill made outside the membership as known then, in the
            order given.
    """

    universe: str
    trades: int
    findings: tuple[MembershipFinding, ...]

    @property
    def clean(self) -> bool:
        """Whether every trade was in an asset that was a member, as known, when it traded."""

        return not self.findings


def membership_review(fills: Iterable[Fill], universe: HistoricalUniverse) -> MembershipReview:
    """Each of ``fills`` against ``universe``'s membership as it was known when it filled.

    A change takes effect no earlier than it is announced, so membership at an
    instant was always known by then; what the review adds is when a later
    addition was known. A fill in an asset that was a member is not reported. One
    in an asset that was not is reported as
    :attr:`~MembershipFindingKind.BEFORE_ANNOUNCEMENT` when the asset's next
    addition was announced after the fill -- the mark of a survivorship-biased
    list, which knew it would join -- and as
    :attr:`~MembershipFindingKind.NOT_A_MEMBER` otherwise.

    Raises:
        ResearchValidationError: If ``universe`` is not a
            :class:`~alphalab.instrument.membership.HistoricalUniverse`, or a
            fill is not a :class:`~alphalab.core.fill.Fill`.
    """

    if not isinstance(universe, HistoricalUniverse):
        raise ResearchValidationError(
            f"A membership review reads a HistoricalUniverse, got {universe!r}."
        )
    by_asset: dict[str, list[MembershipChange]] = {}
    for change in universe.changes:
        by_asset.setdefault(change.asset_id, []).append(change)
    effective = {
        asset: [change.effective_at for change in changes] for asset, changes in by_asset.items()
    }
    findings: list[MembershipFinding] = []
    count = 0
    for fill in fills:
        if not isinstance(fill, Fill):
            raise ResearchValidationError(f"A membership review reads fills, got {fill!r}.")
        count += 1
        changes = by_asset.get(fill.asset_id, [])
        taken = bisect_right(effective.get(fill.asset_id, []), fill.filled_at)
        if taken and changes[taken - 1].kind is MembershipKind.ADDITION:
            continue
        later = next(
            (change for change in changes[taken:] if change.kind is MembershipKind.ADDITION), None
        )
        unannounced = later is not None and later.announced_at > fill.filled_at
        findings.append(
            MembershipFinding(
                asset_id=fill.asset_id,
                at=fill.filled_at,
                side=fill.side.value,
                quantity=fill.quantity,
                kind=(
                    MembershipFindingKind.BEFORE_ANNOUNCEMENT
                    if unannounced
                    else MembershipFindingKind.NOT_A_MEMBER
                ),
                announced_at=later.announced_at if unannounced and later is not None else None,
            )
        )
    return MembershipReview(universe=universe.name, trades=count, findings=tuple(findings))
