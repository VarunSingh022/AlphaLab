"""How much of its derived history a run keeps (v3.12, ledger PRF-004).

Every state a run threads keeps its histories whole: every market event it
published, every decision risk made, every OMS and execution event, every fill,
trade and equity point, every per-record step. A backtest wants that -- its
analytics are computed from it -- and a months-long live session cannot afford
it: memory grows with the session, and so does every checkpoint of it.

A :class:`RetentionPolicy` declares, in the pipeline's configuration, how many
entries of each kind of history a run keeps. It is data: it travels in the run's
snapshot and enters nothing a fill, a position or a cash balance is computed
from.

What is bounded, and what is not
--------------------------------
Only **derived** histories -- logs a later step reads no further back than its
newest entry, or than the entries appended during the same step. Positions, cash,
reservations, working orders, risk's running figures and every other piece of
state a step computes from are never touched, so a run with a policy holds
exactly the positions, cash and orders the same run without one holds. That is
what the policy may change, and all it may change:

=================== =============================================================
``market_history``  the market events published (``MarketState.history`` and
                    ``.events``) -- and the window a strategy's
                    :class:`~alphalab.runtime.context_views.HistoryView` reads
``steps``           the per-record results (``RunState.steps`` and ``.skipped``)
``audit_events``    each subsystem's audit trail: allocation, risk, OMS and
                    execution histories and events, and the portfolio's events
``results``         what the run produced: fills, trades, trade records, the
                    equity curve and the transaction ledger -- the inputs of
                    compiled analytics, which then describe the entries kept
=================== =============================================================

The OMS order book, the execution reports by order and, in a live session, the
routed and settled orders are kept for every order the run has placed: they are
state a later step looks orders up in, not history, and they grow with orders
rather than with events.

When, and how much
------------------
Entries are dropped **between records** -- by
:meth:`~alphalab.runtime.run.RunEngine.advance`, after a record's step -- never
during one: a step reads the entries appended within it by position, and a log
that shortened mid-step would move them. A bounded log keeps at least its bound
and at most an eighth more (:func:`slack`): it is trimmed back to its bound once
it has grown that far past it, so that dropping costs nothing per record. Each
log counts what it has dropped
(:attr:`~alphalab.common.append_log.AppendOnlyLog.dropped`), and the count
travels in the snapshot with the entries.

A strategy's history window is exact. :class:`~alphalab.runtime.context_views.HistoryView`
reads only the newest ``market_history`` events, however many more the log
happens to hold, and refuses -- rather than shortens -- an answer that needs an
event older than that window, so a strategy sees the same history whenever the
log was last trimmed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from alphalab.common.append_log import AppendOnlyLog
from alphalab.runtime.exceptions import RuntimeValidationError

__all__ = [
    "RetentionPolicy",
    "slack",
    "trimmed",
]

#: How far past its bound a log grows before it is trimmed back, as a divisor
#: of the bound: an eighth.
_SLACK_DIVISOR: Final = 8


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """How many entries each kind of derived history keeps; ``None`` keeps all.

    The default keeps everything, which is what every run before v3.12 did. See
    the module docstring for exactly which logs each bound covers.

    Attributes:
        market_history: Market events kept, and a strategy's history window.
        steps: Per-record results kept.
        audit_events: Entries kept in each subsystem audit log.
        results: Fills, trades, trade records, equity points and ledger
            transactions kept, each.

    Raises:
        RuntimeValidationError: If a bound is not a positive integer or ``None``.
    """

    market_history: int | None = None
    steps: int | None = None
    audit_events: int | None = None
    results: int | None = None

    def __post_init__(self) -> None:
        for name in ("market_history", "steps", "audit_events", "results"):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise RuntimeValidationError(
                    f"RetentionPolicy.{name} is {value!r}; it must be a positive whole number "
                    "of entries, or None to keep every one."
                )

    @property
    def keeps_everything(self) -> bool:
        """Whether no history is bounded -- the default, and every pre-v3.12 run."""

        return (
            self.market_history is None
            and self.steps is None
            and self.audit_events is None
            and self.results is None
        )


def slack(bound: int) -> int:
    """How far past ``bound`` a log grows before it is trimmed back: an eighth, at least one."""

    return max(1, bound // _SLACK_DIVISOR)


def trimmed[T](log: AppendOnlyLog[T], bound: int | None) -> AppendOnlyLog[T]:
    """``log`` trimmed back to ``bound`` once it has grown :func:`slack` past it; else ``log``."""

    if bound is None or len(log) <= bound + slack(bound):
        return log
    return log.retain_last(bound)
