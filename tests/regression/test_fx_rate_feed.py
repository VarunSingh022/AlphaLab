"""The FX feed boundary: three rules, one refusal, and no invented data.

ADR-0033's consequences section left three things open, and named this one
twice: "**No rate feed.** AlphaLab ships no FX data, exactly as it ships no
classification data."

ADR-0035 closes it **without** changing that sentence. Every rate here is still
supplied by a caller, still carries a source and an ``as_of``, and there is still
no default, no fallback of ``1.0``, no triangulation and nothing AlphaLab
computes for itself. What was missing was the *boundary*: without one, every
caller folded quotes into an :class:`~alphalab.portfolio.fx.FxRates` itself and
had to decide, alone and usually implicitly, what to do about

* a quote that arrives **out of order**,
* a quote **redelivered** after a reconnect, and
* **two sources** disagreeing about one pair at one instant.

Those have one right answer each, and this file is where each is pinned.

The third is the sharpest. ``FxRates.of`` already refuses two quotes for one
pair -- "which one is right is not a question this table will answer by picking"
-- and a feed that let the later packet win would be picking, while looking
authoritative doing it.
"""

import inspect
from decimal import Decimal

import pytest

from alphalab.persistence import deserialize, serialize
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.portfolio.exceptions import PortfolioError
from alphalab.portfolio.fx import FxRate, MissingRateError, StaleRateError
from alphalab.portfolio.fx_feed import (
    FX_FEED_SNAPSHOT_SCHEMA,
    ConflictingQuoteError,
    FxFeed,
    FxFeedOutcome,
    FxFeedState,
    FxQuote,
    FxRateSource,
    SequenceFxSource,
    capture,
    from_primitives,
    restore,
)


def _rate(
    base: str = "EUR",
    quote: str = "USD",
    rate: str = "1.08",
    as_of: float = 100.0,
    source: str = "ECB",
) -> FxRate:
    return FxRate(base, quote, Decimal(rate), as_of, source)


def _feed(max_age_seconds: float | None = None) -> FxFeedState:
    return FxFeed.initialize("FEED-1", max_age_seconds)


# --------------------------------------------------------------------------- #
# 1. The source boundary
# --------------------------------------------------------------------------- #


def test_the_deterministic_source_yields_the_rates_it_was_given_in_order() -> None:
    """Not invented data: the caller supplies every rate. What the source adds
    is repeatability, which a test needs and a vendor cannot give.
    """

    rates = (_rate("EUR", "USD"), _rate("GBP", "USD", "1.27"))
    source = SequenceFxSource.of("ECB", rates)

    quotes = list(source.quotes())

    assert [q.rate for q in quotes] == list(rates)
    assert [q.sequence for q in quotes] == [0, 1]
    assert [q.rate for q in source.quotes()] == list(rates), "re-readable"


def test_the_sequence_source_satisfies_the_protocol() -> None:
    assert isinstance(SequenceFxSource("ECB"), FxRateSource)


def test_a_source_must_name_itself() -> None:
    with pytest.raises(PortfolioError, match="source_id cannot be empty"):
        SequenceFxSource("   ")


def test_a_feed_must_name_itself() -> None:
    with pytest.raises(PortfolioError, match="feed_id cannot be empty"):
        FxFeed.initialize("")


def test_the_module_models_no_provider_api() -> None:
    """The boundary ``MarketDataSource`` draws, drawn again: no transport here.

    Read from the imports rather than from the text, because the module's own
    docstring *names* the things it does not model and a substring search would
    flag its explanation as the offence.
    """

    import ast

    from alphalab.portfolio import fx_feed

    tree = ast.parse(inspect.getsource(fx_feed))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    transport = {"requests", "urllib", "urllib3", "socket", "ssl", "http", "httpx", "asyncio"}
    assert not (imported & transport), f"the feed models a transport: {imported & transport}"

    # And it reaches no higher layer: a rate table is below the execution path.
    alphalab_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("alphalab")
    }
    assert all(
        module.startswith(("alphalab.common", "alphalab.persistence", "alphalab.portfolio"))
        for module in alphalab_modules
    ), alphalab_modules


# --------------------------------------------------------------------------- #
# 2. The three rules
# --------------------------------------------------------------------------- #


def test_a_first_quote_is_applied() -> None:
    state, decision = FxFeed.apply(_feed(), FxQuote(_rate()))

    assert decision.outcome is FxFeedOutcome.APPLIED
    assert decision.previous is None
    assert state.rates.rate_for("EUR", "USD") is not None
    assert state.applied_count == 1 and state.observed == 1


def test_a_newer_quote_replaces_and_says_what_it_replaced() -> None:
    state, _ = FxFeed.apply(_feed(), FxQuote(_rate(rate="1.08", as_of=100.0)))
    state, decision = FxFeed.apply(state, FxQuote(_rate(rate="1.09", as_of=200.0)))

    assert decision.outcome is FxFeedOutcome.APPLIED
    assert decision.previous is not None and decision.previous.rate == Decimal("1.08")
    assert state.rates.rate_for("EUR", "USD").rate == Decimal("1.09")  # type: ignore[union-attr]


def test_an_older_quote_does_not_move_the_table_backwards() -> None:
    """A reordered packet must not un-do a newer observation.

    ``OrderingGuarantee`` exists on ``MarketDataSource`` because a live venue can
    reorder; this rule is what makes reordering *safe* rather than merely
    declared.
    """

    state, _ = FxFeed.apply(_feed(), FxQuote(_rate(rate="1.09", as_of=200.0)))
    state, decision = FxFeed.apply(state, FxQuote(_rate(rate="1.05", as_of=150.0)))

    assert decision.outcome is FxFeedOutcome.SUPERSEDED
    assert state.rates.rate_for("EUR", "USD").rate == Decimal("1.09")  # type: ignore[union-attr]
    assert state.applied_count == 1, "observed, not applied"
    assert state.observed == 2


def test_a_redelivered_quote_is_a_duplicate_rather_than_a_second_observation() -> None:
    """The distinction ``BrokerState`` draws for a redelivered execution."""

    quote = FxQuote(_rate())
    state, _ = FxFeed.apply(_feed(), quote)
    state, decision = FxFeed.apply(state, quote)

    assert decision.outcome is FxFeedOutcome.DUPLICATE
    assert state.observed == 2 and state.applied_count == 1


@pytest.mark.parametrize(
    ("second", "differs_in"),
    [(_rate(rate="1.11"), "the rate"), (_rate(source="Reuters"), "the source")],
    ids=["different rate", "different source"],
)
def test_two_quotes_claiming_one_instant_are_refused_rather_than_ranked(
    second: FxRate, differs_in: str
) -> None:
    """``FxRates.of`` refuses this shape at construction; so does the feed."""

    state, _ = FxFeed.apply(_feed(), FxQuote(_rate()))

    with pytest.raises(ConflictingQuoteError, match="will answer by picking"):
        FxFeed.apply(state, FxQuote(second))


def test_a_refused_quote_leaves_the_state_it_was_offered_to_unchanged() -> None:
    before, _ = FxFeed.apply(_feed(), FxQuote(_rate()))

    with pytest.raises(ConflictingQuoteError):
        FxFeed.apply(before, FxQuote(_rate(rate="1.11")))

    assert before.observed == 1 and before.applied_count == 1
    assert before.rates.rate_for("EUR", "USD").rate == Decimal("1.08")  # type: ignore[union-attr]


def test_every_decision_is_recorded_in_order() -> None:
    state, _ = FxFeed.advance(
        _feed(),
        [
            FxQuote(_rate(as_of=100.0)),
            FxQuote(_rate(rate="1.09", as_of=200.0)),
            FxQuote(_rate(rate="1.05", as_of=150.0)),
            FxQuote(_rate(rate="1.09", as_of=200.0)),
        ],
    )

    assert [d.outcome for d in state.decisions] == [
        FxFeedOutcome.APPLIED,
        FxFeedOutcome.APPLIED,
        FxFeedOutcome.SUPERSEDED,
        FxFeedOutcome.DUPLICATE,
    ]
    assert "EUR/USD" in state.decisions[0].summary


# --------------------------------------------------------------------------- #
# 3. Provenance, staleness and liveness stay where they belong
# --------------------------------------------------------------------------- #


def test_a_rate_cannot_reach_a_feed_without_provenance() -> None:
    """Refused by ``FxRate`` itself, so a source cannot emit one by accident."""

    with pytest.raises(PortfolioError, match="names no source"):
        FxRate("EUR", "USD", Decimal("1.08"), 100.0, "")


def test_the_feed_does_not_decide_whether_a_rate_is_too_old_to_use() -> None:
    """That is ``max_age_seconds``, checked at conversion, and unchanged."""

    state, decision = FxFeed.apply(_feed(max_age_seconds=10.0), FxQuote(_rate(as_of=100.0)))

    assert decision.outcome is FxFeedOutcome.APPLIED, "applied; usability is a later question"
    with pytest.raises(StaleRateError):
        state.rates.convert(Decimal("100"), "EUR", "USD", 1_000.0)


def test_liveness_is_a_different_question_from_staleness() -> None:
    """A feed quoting EUR/USD while never quoting USD/JPY is alive and missing a
    pair; a silent feed is neither, and only ``silent_for`` tells them apart.
    """

    assert _feed().silent_for(500.0) is None, "never heard anything"

    state, _ = FxFeed.apply(_feed(), FxQuote(_rate(as_of=100.0)))
    assert state.silent_for(500.0) == 400.0

    # A superseded quote is still evidence the connection is alive.
    state, decision = FxFeed.apply(state, FxQuote(_rate(rate="1.05", as_of=50.0)))
    assert decision.outcome is FxFeedOutcome.SUPERSEDED
    assert state.silent_for(500.0) == 400.0, "the newest offered, not the newest applied"


def test_a_missing_pair_is_still_a_refusal_and_not_a_derivation() -> None:
    state, _ = FxFeed.apply(_feed(), FxQuote(_rate("EUR", "USD")))

    with pytest.raises(MissingRateError, match="No rate for USD/EUR"):
        state.rates.convert(Decimal("100"), "USD", "EUR", 200.0)


def test_the_feed_derives_no_rate_of_its_own() -> None:
    """No triangulation, no implicit inversion -- ADR-0020's non-goals, kept."""

    state, _ = FxFeed.advance(
        _feed(), [FxQuote(_rate("EUR", "USD")), FxQuote(_rate("EUR", "JPY", "160"))]
    )

    assert state.pairs == (("EUR", "JPY"), ("EUR", "USD"))
    assert state.rates.rate_for("USD", "JPY") is None, "no triangulation"
    assert state.rates.rate_for("USD", "EUR") is None, "no implicit inversion"
    assert all(not rate.derived for rate in state.rates.rates.values())


# --------------------------------------------------------------------------- #
# 4. It produces the canonical type, and reaches valuation and settlement
# --------------------------------------------------------------------------- #


def test_the_feed_produces_the_canonical_table_rather_than_a_parallel_one() -> None:
    from alphalab.portfolio.fx import FxRates

    state, _ = FxFeed.drain(_feed(), SequenceFxSource.of("ECB", [_rate()]))

    assert isinstance(state.rates, FxRates)
    assert state.rates.convert(Decimal("100.00"), "EUR", "USD", 200.0).converted == Decimal(
        "108.00"
    )


def test_the_tolerance_the_feed_was_built_with_reaches_the_table_it_builds() -> None:
    state, _ = FxFeed.drain(_feed(max_age_seconds=30.0), SequenceFxSource.of("ECB", [_rate()]))

    assert state.rates.max_age_seconds == 30.0


def test_draining_a_source_applies_everything_it_holds() -> None:
    source = SequenceFxSource.of(
        "ECB", [_rate("EUR", "USD"), _rate("GBP", "USD", "1.27"), _rate("USD", "JPY", "150")]
    )

    state, decisions = FxFeed.drain(_feed(), source)

    assert len(decisions) == 3
    assert all(d.applied for d in decisions)
    assert len(state.rates) == 3


# --------------------------------------------------------------------------- #
# 5. Durability and replay
# --------------------------------------------------------------------------- #


def test_a_feed_round_trips_through_its_snapshot() -> None:
    state, _ = FxFeed.advance(
        _feed(max_age_seconds=60.0),
        [
            FxQuote(_rate(as_of=100.0), sequence=0),
            FxQuote(_rate(rate="1.09", as_of=200.0), sequence=1),
            FxQuote(_rate(rate="1.05", as_of=150.0), sequence=2),
            FxQuote(_rate("GBP", "USD", "1.27", 210.0), sequence=3),
        ],
    )

    restored = restore(from_primitives(deserialize(serialize(capture(state)))))

    assert restored == state
    assert restored.rates.max_age_seconds == 60.0
    assert [d.outcome for d in restored.decisions] == [d.outcome for d in state.decisions]


def test_the_snapshot_declares_its_own_version() -> None:
    payload = deserialize(serialize(capture(_feed())))

    assert payload["schema_version"] == FX_FEED_SNAPSHOT_SCHEMA == 1


def test_an_unreadable_version_is_refused() -> None:
    payload = dict(deserialize(serialize(capture(_feed()))))
    payload["schema_version"] = 2

    with pytest.raises(StateDecodeError, match="declares schema version 2"):
        from_primitives(payload)


def test_a_restore_reconnects_nothing() -> None:
    """What is captured is what AlphaLab believed, never a connection."""

    from alphalab.portfolio import fx_feed

    snapshot_fields = set(fx_feed.FxFeedSnapshot.__dataclass_fields__)

    assert "source" not in snapshot_fields
    assert "connection" not in snapshot_fields
    assert inspect.signature(restore).parameters.keys() == {"snapshot"}


def test_replaying_the_same_quotes_produces_the_same_state() -> None:
    """Determinism: a replayed run values its book with the rates the original
    used rather than with today's.
    """

    quotes = [
        FxQuote(_rate(as_of=100.0)),
        FxQuote(_rate(rate="1.09", as_of=200.0)),
        FxQuote(_rate("GBP", "USD", "1.27", 150.0)),
    ]

    first, _ = FxFeed.advance(_feed(), quotes)
    second, _ = FxFeed.advance(_feed(), quotes)

    assert first == second


def test_the_order_quotes_arrive_in_does_not_change_where_the_table_ends_up() -> None:
    """The superseding rule's whole purpose, stated as a property."""

    forward = [FxQuote(_rate(as_of=100.0)), FxQuote(_rate(rate="1.09", as_of=200.0))]
    reversed_order = list(reversed(forward))

    ahead, _ = FxFeed.advance(_feed(), forward)
    behind, _ = FxFeed.advance(_feed(), reversed_order)

    assert ahead.rates.rate_for("EUR", "USD") == behind.rates.rate_for("EUR", "USD")


def test_a_negative_sequence_is_refused() -> None:
    with pytest.raises(PortfolioError, match="sequence cannot be negative"):
        FxQuote(_rate(), sequence=-1)
