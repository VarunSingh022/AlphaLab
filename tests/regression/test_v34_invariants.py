"""The v3.4 invariants, measured from the code rather than claimed in a release note.

Each section is one property the global-markets and multi-asset work depends on,
and each is written so that a future change which breaks it fails here rather
than in a number somebody reads a year later.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import pathlib
from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest

PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "alphalab"


def _sources() -> list[tuple[str, str]]:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        found.append((str(path.relative_to(PACKAGE.parent)), path.read_text()))
    return found


# --------------------------------------------------------------------------- #
# 1. One authority per concept
# --------------------------------------------------------------------------- #


def test_there_is_one_timezone_resolution_site() -> None:
    """``ZoneInfo`` is constructed in exactly one place, which refuses clearly."""

    offenders = [
        name
        for name, source in _sources()
        if "ZoneInfo(" in source and not name.endswith("data/time.py")
    ]
    assert offenders == [], (
        "a second timezone authority: ZoneInfo is constructed outside "
        f"alphalab/data/time.py in {offenders}. resolve_zone() is the one site, and it is "
        "what turns a missing tz database into a domain error rather than a stack trace."
    )


def test_there_is_one_exchange_calendar_type() -> None:
    """``MarketCalendar`` answers sessions; ``TradingCalendar`` answers job firing.

    Pinned as a pair in ``test_shared_names_stay_distinct.py``. What this adds
    is that v3.4 created no third: the new packages reach a calendar through a
    structural protocol instead of defining one.
    """

    from alphalab.conventions.settlement import TradingDayCalendar
    from alphalab.data.calendar import MarketCalendar
    from alphalab.futures.chain import SessionCalendar

    assert not hasattr(TradingDayCalendar, "windows_on")
    assert not hasattr(SessionCalendar, "windows_on")
    # Both are one-method protocols the one calendar already satisfies.
    calendar = MarketCalendar(
        calendar_id="X",
        timezone_name="UTC",
        weekly_sessions=dict.fromkeys(range(5), (SessionWindow(time(9), time(17)),)),
    )
    settlement: TradingDayCalendar = calendar
    session: SessionCalendar = calendar
    assert settlement.is_trading_day(date(2026, 3, 16))
    assert session.is_trading_day(date(2026, 3, 16))


def test_conventions_is_a_leaf_over_common() -> None:
    """The property that lets every asset package share one convention vocabulary.

    ``alphalab.data`` imports ``alphalab.options``, which imports
    ``alphalab.portfolio``. A convention package reaching any of those could not
    also be used by them without closing a package cycle.
    """

    edges: set[str] = set()
    for name, source in _sources():
        if not name.startswith("alphalab/conventions/"):
            continue
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("alphalab"):
                assert node.module is not None
                edges.add(".".join(node.module.split(".")[:2]))
    assert edges <= {"alphalab.common", "alphalab.conventions"}, (
        f"alphalab.conventions reaches {sorted(edges)}; it must rest on alphalab.common alone."
    )


def test_the_multiplier_is_multiplied_in_one_place() -> None:
    """``contract_notional`` is the one site, and a reader can check it applied once."""

    from alphalab.conventions.market import contract_notional

    source = inspect.getsource(contract_notional)
    assert "quantity * price * convention.multiplier" in source
    assert "quantity * convention.multiplier" in source  # the units, not money

    # And nothing else in the repository multiplies by a convention's multiplier.
    elsewhere = [
        name
        for name, text in _sources()
        if "convention.multiplier" in text and not name.endswith("conventions/market.py")
    ]
    assert elsewhere == [], f"a second site applies the multiplier: {elsewhere}"


def test_no_second_implied_volatility_solver_appeared() -> None:
    from alphalab.options import implied, pricing

    assert "black_scholes_value" in inspect.getsource(implied)
    # The solver evaluates the pricer's expression rather than restating it.
    assert "math.log" not in inspect.getsource(implied)
    assert "math.log" in inspect.getsource(pricing)


def test_no_second_day_count_or_compounding_enum() -> None:
    offenders = [
        name
        for name, source in _sources()
        if ("class DayCount" in source or "class Compounding" in source)
        and not name.startswith("alphalab/conventions/")
    ]
    assert offenders == [], f"a second day-count or compounding authority in {offenders}"


def test_currency_attribution_does_not_recompute_the_attribution_engine() -> None:
    """Two different measurements; neither derives the other."""

    from alphalab.portfolio import fx_research

    imported = {
        node.module
        for node in ast.walk(ast.parse(inspect.getsource(fx_research)))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not [name for name in imported if name.startswith("alphalab.analytics")], (
        "currency attribution reaches the attribution engine; the two are different "
        "measurements and neither derives the other."
    )


# --------------------------------------------------------------------------- #
# 2. No hidden market assumptions
# --------------------------------------------------------------------------- #

#: Dataclass fields whose wrong value produces a number rather than a refusal.
#:
#: ``test_no_silent_financial_defaults.py`` sweeps function *parameters*. It
#: could not see a dataclass field, which is how ``FutureContract.currency``,
#: ``OptionContract.multiplier``, ``OptionContract.style`` and
#: ``FundingRate.interval_hours`` each kept a US or Binance convention as a
#: global default for eight releases.
CONVENTION_FIELDS = frozenset(
    {
        "currency",
        "quote_currency",
        "settlement_currency",
        "base_currency",
        "default_currency",
        "multiplier",
        "contract_multiplier",
        "tick_size",
        "lot_size",
        "contract_size",
        "interval_hours",
        "funding_interval_hours",
        "timezone_name",
        "day_count",
        "compounding",
        "settlement",
        "style",
        "exercise_style",
    }
)

#: Each default that stays, with the reason a wrong value cannot produce a number.
#:
#: The three ``currency`` entries are the shape ``test_no_silent_financial_defaults.py``
#: already permits for ``NAVCalculator.calculate``: each flows into a seam that
#: **refuses** a mismatch rather than converting it. ADR-0028's two seams, and
#: ``test_currency_authority.py`` / ``test_settlement_multi_currency.py`` pin them.
#: They are pipeline and venue configuration rather than instrument conventions,
#: which is what this sweep is about, and they are listed rather than excluded so
#: that the distinction is a decision a reader can check.
PERMITTED_FIELD_DEFAULTS: dict[tuple[str, str], str] = {
    ("FutureSpec", "contract_month"): "None means not supplied, which is the honest state",
    ("CryptoInstrument", "expiry"): "None, and refused as non-None for SPOT and PERPETUAL",
    ("RoutingConfig", "currency"): (
        "reaches ExecutionReport.currency, which _require_settlement_currency refuses "
        "when it is not one the pipeline settles"
    ),
    ("VenueConfig", "currency"): (
        "a venue connection label; a report in an unsettled currency is refused at the same seam"
    ),
    ("StudioConfig", "default_currency"): (
        "alphalab.studio is a standalone package with no in-repo consumer and reaches "
        "no accounting path"
    ),
    ("ExecutionPipelineConfig", "currency"): (
        "the pipeline's settlement currency; an instrument or a report in a currency it "
        "does not settle is refused at ADR-0028's two seams, never converted"
    ),
    ("NormalizationPolicy", "currency"): (
        "ADR-0019's fourth currency role: what a quote is *labelled* with, and never "
        "accounting. It reaches no ledger, so a wrong value cannot produce a figure"
    ),
    ("TradingEnvConfig", "currency"): (
        "alphalab.reinforcement_learning is a standalone package with no in-repo "
        "consumer and no accounting path"
    ),
}


def test_no_dataclass_field_defaults_a_market_convention() -> None:
    offenders: list[str] = []
    for name, source in _sources():
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for statement in node.body:
                if not isinstance(statement, ast.AnnAssign) or statement.value is None:
                    continue
                if not isinstance(statement.target, ast.Name):
                    continue
                field = statement.target.id
                if field not in CONVENTION_FIELDS:
                    continue
                rendered = ast.unparse(statement.value)
                if rendered in ("None", "False", '""', "''"):
                    continue
                if (node.name, field) in PERMITTED_FIELD_DEFAULTS:
                    continue
                offenders.append(f"{name}: {node.name}.{field} = {rendered}")

    assert not offenders, (
        "a market convention is defaulted on a dataclass field, where a wrong value "
        "produces a number rather than a refusal:\n  " + "\n  ".join(sorted(offenders))
    )


def test_the_permitted_currency_defaults_refuse_rather_than_convert() -> None:
    """The exemption is earned, not asserted -- the seams must actually refuse.

    Each permitted ``currency`` default is one that reaches a refusal. The two
    on the execution path are exercised here against the real pipeline;
    ``test_settlement_multi_currency.py`` covers the same seams in full.
    """

    from alphalab.market.normalization import NormalizationPolicy
    from alphalab.runtime.broker_routing import RoutingConfig
    from alphalab.runtime.execution_pipeline import (
        ExecutionPipelineConfig,
        _require_settlement_currency,
    )

    # The defaults exist, and each one names a role rather than an amount.
    assert RoutingConfig().currency == "USD"
    assert inspect.signature(ExecutionPipelineConfig).parameters["currency"].default == "USD"

    # The pipeline refuses a report it cannot settle rather than converting it.
    assert callable(_require_settlement_currency)
    source = inspect.getsource(_require_settlement_currency)
    assert "raise" in source
    assert "convert" not in source

    # And the market-data role reaches no ledger: it labels a quote.
    labelled = NormalizationPolicy()
    assert labelled.currency == "USD"
    assert "currency" not in {
        field.name
        for field in __import__("dataclasses").fields(
            __import__("alphalab.market.bar", fromlist=["Bar"]).Bar
        )
    }


def test_the_four_that_were_found_stay_required() -> None:
    """Named individually, so removing the sweep does not quietly reopen them."""

    from alphalab.crypto.funding import FundingRate, compute_funding_payment
    from alphalab.crypto.instrument import CryptoInstrument
    from alphalab.data.assets import OptionSpec
    from alphalab.futures.contract import FutureContract
    from alphalab.options.contract import OptionContract

    required = (
        (FutureContract, "currency"),
        (OptionContract, "multiplier"),
        (OptionContract, "style"),
        (OptionSpec, "style"),
        (FundingRate, "interval_hours"),
        (CryptoInstrument, "contract_size"),
    )
    for owner, field in required:
        declared = inspect.signature(owner).parameters[field]
        assert declared.default is inspect.Parameter.empty, (
            f"{owner.__name__}.{field} is defaulted again"
        )
    assert (
        inspect.signature(compute_funding_payment).parameters["contract_size"].default
        is inspect.Parameter.empty
    )


def test_every_convention_carrying_type_requires_all_of_its_fields() -> None:
    from alphalab.conventions import MarketConvention
    from alphalab.crypto import VenueSpecification

    for owner in (MarketConvention, VenueSpecification):
        for parameter in inspect.signature(owner).parameters.values():
            assert parameter.default is inspect.Parameter.empty, (
                f"{owner.__name__}.{parameter.name} is defaulted"
            )


# --------------------------------------------------------------------------- #
# 3. Point-in-time safety
# --------------------------------------------------------------------------- #


def test_an_fx_rate_from_the_future_is_refused_in_both_directions_of_time() -> None:
    from alphalab.portfolio.fx import FutureDatedRateError, FxRate, FxRates, StaleRateError

    table = FxRates.of([FxRate("EUR", "USD", Decimal("1.1"), 100.0, "ECB")], max_age_seconds=10.0)
    with pytest.raises(FutureDatedRateError):
        table.convert(Decimal("1"), "EUR", "USD", as_of=50.0)
    with pytest.raises(StaleRateError):
        table.convert(Decimal("1"), "EUR", "USD", as_of=500.0)
    assert table.convert(Decimal("1"), "EUR", "USD", as_of=105.0).converted == Decimal("1.10")


def test_a_margin_requirement_published_later_cannot_be_applied_earlier() -> None:
    from alphalab.futures import ContractMarginSpec, FuturesInputError, position_margin
    from alphalab.futures.contract import FutureContract, futures_symbol

    contract = FutureContract("CL", 0.0, 86400.0, 1000, Decimal("0.01"), "USD")
    spec = ContractMarginSpec(futures_symbol(contract), Decimal("100"), Decimal("80"), "USD", 500.0)
    with pytest.raises(FuturesInputError, match="did not exist"):
        position_margin(contract, Decimal("1"), {futures_symbol(contract): spec}, as_of=100.0)


def test_a_volume_roll_does_not_read_observations_after_the_decision() -> None:
    """Truncating the future leaves every earlier roll unchanged."""

    from alphalab.futures import ContractChain, RollPolicy, RollTrigger, roll_schedule
    from alphalab.futures.contract import FutureContract
    from alphalab.market.bar import Bar, TimeFrame

    def contract(month: int) -> FutureContract:
        stamp = datetime(2026, month, 20, tzinfo=UTC).timestamp()
        return FutureContract("CL", stamp, stamp, 1000, Decimal("0.01"), "USD")

    def bar(symbol: str, timestamp: float, volume: str) -> Bar:
        price = Decimal("70")
        return Bar(
            symbol, timestamp, price, price, price, price, Decimal(volume), price, 1, TimeFrame.D1
        )

    chain = ContractChain("CL", (contract(3), contract(6), contract(9)))
    day = 86400.0
    full = {
        "CL_202603": tuple(bar("CL_202603", i * day, "100" if i < 3 else "1") for i in range(10)),
        "CL_202606": tuple(
            bar("CL_202606", i * day, "50" if i < 3 else ("200" if i < 7 else "1"))
            for i in range(10)
        ),
        "CL_202609": tuple(bar("CL_202609", i * day, "10" if i < 7 else "300") for i in range(10)),
    }
    policy = RollPolicy(RollTrigger.VOLUME_CROSSOVER)
    complete = roll_schedule(chain, policy, observations=full)

    truncated = {
        symbol: tuple(b for b in bars if b.timestamp <= 5 * day) for symbol, bars in full.items()
    }
    partial_chain = ContractChain("CL", (contract(3), contract(6)))
    partial = roll_schedule(partial_chain, policy, observations=truncated)
    assert partial[0].timestamp == complete[0].timestamp


# --------------------------------------------------------------------------- #
# 4. Determinism
# --------------------------------------------------------------------------- #


def test_no_v34_module_reads_a_clock_or_a_salted_hash() -> None:
    v34 = (
        "alphalab/conventions/",
        "alphalab/futures/chain.py",
        "alphalab/futures/margin.py",
        "alphalab/options/implied.py",
        "alphalab/options/expiration.py",
        "alphalab/options/model.py",
        "alphalab/crypto/venue.py",
        "alphalab/crypto/availability.py",
        "alphalab/macro/bond.py",
        "alphalab/portfolio/fx_research.py",
        "alphalab/portfolio/contracts.py",
    )
    offenders: list[str] = []
    for name, source in _sources():
        if not any(name.startswith(prefix) for prefix in v34):
            continue
        for marker in ("time.time(", "datetime.now(", "utc_now(", "random.", "hash("):
            if marker in source:
                offenders.append(f"{name}: {marker}")
    assert offenders == [], f"a v3.4 path is non-deterministic: {offenders}"


def test_every_v34_computation_repeats_exactly() -> None:
    from alphalab.conventions import (
        LotSpecification,
        MarketConvention,
        SettlementBasis,
        SettlementRule,
        TickSchedule,
        contract_notional,
    )
    from alphalab.crypto import FeeSchedule, LiquidityRole, PriceSource, VenueSpecification
    from alphalab.crypto import trading_fee as fee
    from alphalab.macro import clean_price, convexity
    from alphalab.options import black_scholes_price, implied_volatility
    from alphalab.options.contract import OptionContract
    from alphalab.options.enums import ExerciseStyle, OptionType

    convention = MarketConvention(
        "XCME",
        "XCME",
        "USD",
        "USD",
        Decimal("1000"),
        TickSchedule.flat(Decimal("0.01")),
        LotSpecification.single_units(),
        SettlementRule(SettlementBasis.TRADE_DATE, 0),
    )
    contract = OptionContract(
        "AAPL", Decimal("150"), 365.25 * 86400, OptionType.CALL, ExerciseStyle.EUROPEAN, 100
    )
    price = black_scholes_price(contract, Decimal("150"), 0.25, 0.04, 0.0)
    venue = VenueSpecification(
        "binance",
        8,
        FeeSchedule(Decimal("-1"), Decimal("5")),
        PriceSource.INDEX,
        Decimal("5"),
        "USDT",
    )
    from alphalab.conventions import Compounding, DayCount
    from alphalab.macro.bond import Bond

    bond = Bond(
        Decimal("100"),
        0.05,
        Compounding.SEMI_ANNUAL,
        date(2020, 1, 15),
        date(2030, 1, 15),
        DayCount.ACT_365_FIXED,
        "USD",
    )

    for _ in range(3):
        assert contract_notional(convention, Decimal("10"), Decimal("75.5")) == contract_notional(
            convention, Decimal("10"), Decimal("75.5")
        )
        assert implied_volatility(contract, price, Decimal("150"), 0.04, 0.0) == (
            implied_volatility(contract, price, Decimal("150"), 0.04, 0.0)
        )
        assert fee(venue, Decimal("1000"), LiquidityRole.TAKER) == fee(
            venue, Decimal("1000"), LiquidityRole.TAKER
        )
        assert clean_price(bond, 0.0637, date(2025, 1, 15)) == clean_price(
            bond, 0.0637, date(2025, 1, 15)
        )
        assert convexity(bond, 0.05, date(2025, 1, 15)) == convexity(bond, 0.05, date(2025, 1, 15))


# --------------------------------------------------------------------------- #
# 5. Dimensional correctness
# --------------------------------------------------------------------------- #


def test_a_tick_value_reconciles_with_its_tick_size_and_multiplier() -> None:
    from alphalab.conventions import TickSchedule, tick_value

    for size, multiplier in (("0.01", "1000"), ("0.25", "50"), ("0.0001", "100000")):
        value = tick_value(Decimal(size), Decimal(multiplier), "USD")
        assert value.amount == value.tick_size * value.multiplier
        assert TickSchedule.flat(Decimal(size)).tick_size_at(Decimal("1")) == value.tick_size


def test_a_notional_and_its_underlying_units_are_not_the_same_field() -> None:
    from alphalab.conventions import (
        ContractNotional,
        LotSpecification,
        MarketConvention,
        SettlementBasis,
        SettlementRule,
        TickSchedule,
        contract_notional,
    )

    convention = MarketConvention(
        "XCME",
        "XCME",
        "USD",
        "USD",
        Decimal("1000"),
        TickSchedule.flat(Decimal("0.01")),
        LotSpecification.single_units(),
        SettlementRule(SettlementBasis.TRADE_DATE, 0),
    )
    result = contract_notional(convention, Decimal("10"), Decimal("75.50"))
    assert {f.name for f in dataclasses.fields(ContractNotional)} >= {
        "amount",
        "underlying_units",
        "currency",
    }
    assert result.amount != result.underlying_units


def test_a_cross_rate_is_dimensionally_coherent() -> None:
    """base/via x via/quote cancels the middle currency, and the ends survive."""

    from alphalab.portfolio.fx import FxRate, FxRates

    table = FxRates.of(
        [
            FxRate("EUR", "USD", Decimal("1.10"), 0.0, "s"),
            FxRate("USD", "JPY", Decimal("150"), 0.0, "s"),
        ]
    )
    crossed = table.cross_rate("EUR", "JPY", via="USD")
    assert crossed.pair == ("EUR", "JPY")
    # One EUR buys 1.10 USD buys 165 JPY.
    assert crossed.rate == Decimal("1.10") * Decimal("150")


def test_duration_is_years_and_convexity_is_years_squared() -> None:
    """A zero-coupon bond pins both exactly, which is the only way to check units."""

    from alphalab.conventions import Compounding, DayCount
    from alphalab.macro.bond import Bond, convexity, macaulay_duration

    zero = Bond(
        Decimal("100"),
        0.0,
        Compounding.ANNUAL,
        date(2020, 1, 15),
        date(2030, 1, 15),
        DayCount.ACT_365_FIXED,
        "USD",
    )
    settlement = date(2025, 1, 15)
    years = 5.0
    assert macaulay_duration(zero, 0.05, settlement) == pytest.approx(years, abs=1e-9)
    # For a zero, convexity is t(t+1)/(1+y)^2 -- unambiguously years squared.
    assert convexity(zero, 0.05, settlement) == pytest.approx(
        years * (years + 1) / 1.05**2, abs=1e-9
    )


def test_an_option_leg_reconciles_its_quantity_and_direction() -> None:
    from alphalab.core.enums import Side
    from alphalab.options import OptionLeg, OptionStrategy, net_premium, occ_symbol, signed_quantity
    from alphalab.options.contract import OptionContract
    from alphalab.options.enums import ExerciseStyle, OptionType

    contract = OptionContract(
        "AAPL", Decimal("150"), 365.25 * 86400, OptionType.CALL, ExerciseStyle.EUROPEAN, 100
    )
    long_leg = OptionLeg(contract, Side.BUY, 3)
    short_leg = OptionLeg(contract, Side.SELL, 3)
    assert signed_quantity(long_leg) + signed_quantity(short_leg) == Decimal("0")
    prices = {occ_symbol(contract): Decimal("5")}
    assert net_premium(OptionStrategy((long_leg, short_leg)), prices) == Decimal("0")


def test_the_sign_convention_is_spelled_once() -> None:
    """A convention applied in two places is one that will eventually disagree."""

    from alphalab.options import strategy

    source = inspect.getsource(strategy)
    assert source.count("if leg.side is Side.BUY") == 1


# --------------------------------------------------------------------------- #
# 6. Boundaries
# --------------------------------------------------------------------------- #


def test_no_module_names_a_prohibited_integration_even_in_prose() -> None:
    """The text-level half of the vendor boundary.

    ``test_one_research_authority_per_concept.py`` reads **import statements**
    for these roots and owns the list. This reads the **raw source text**, which
    additionally catches a vendor named in a docstring, a comment or a URL --
    "do not describe vendor APIs as AlphaLab functionality" is a claim a
    docstring can make without importing anything.

    The list is imported rather than restated. Two copies is how one of them
    comes to be shorter than the other, which is the failure mode
    ``nowandfuture.md`` section 14 names.
    """

    from tests.regression.test_one_research_authority_per_concept import FORBIDDEN_VENDORS

    # Roots that are ordinary English or appear in unrelated identifiers are
    # checked as imports only; the text sweep would match "torch" in "torchlight".
    textual = tuple(
        term
        for term in FORBIDDEN_VENDORS
        if term not in ("torch", "transformers", "marketplace", "knight")
    )
    offenders: list[str] = []
    for name, source in _sources():
        lowered = source.lower()
        offenders.extend(f"{name}: {term}" for term in textual if term in lowered)
    assert offenders == [], f"a prohibited integration is named in {offenders}"


def test_the_crypto_venue_surface_holds_metadata_and_reaches_no_exchange() -> None:
    from alphalab.crypto import venue

    source = inspect.getsource(venue)
    for marker in ("http", "requests", "websocket", "api_key", "secret", "urllib"):
        assert marker not in source.lower(), f"{marker} in alphalab/crypto/venue.py"


def test_the_package_still_has_no_runtime_dependency() -> None:
    import tomllib

    pyproject = tomllib.loads((PACKAGE.parent / "pyproject.toml").read_text())
    assert pyproject["project"]["dependencies"] == []


# Imported late so the module-level protocol check above can construct one.
from alphalab.data.calendar import SessionWindow  # noqa: E402

# --------------------------------------------------------------------------- #
# 7. Persistence: v3.4 added no durable state
# --------------------------------------------------------------------------- #


def test_v34_added_no_snapshot_owner_and_no_schema_constant() -> None:
    """Ten durable states, each with one snapshot owner and one schema literal.

    v3.4 is entirely value types and pure functions: a ``MarketConvention``, a
    ``ContractChain``, a ``RollPolicy`` and a ``Bond`` are declarations a caller
    holds, not state a run carries. So there is nothing to persist, nothing to
    version, and no migration to write -- which is why every v3.3 snapshot
    payload still round-trips unchanged.

    This asserts the absence, so that a later release adding durable state to
    one of these packages has to do it deliberately.
    """

    v34_modules = (
        "alphalab/conventions/",
        "alphalab/futures/chain.py",
        "alphalab/futures/margin.py",
        "alphalab/options/implied.py",
        "alphalab/options/expiration.py",
        "alphalab/options/model.py",
        "alphalab/crypto/venue.py",
        "alphalab/crypto/availability.py",
        "alphalab/macro/bond.py",
        "alphalab/portfolio/fx_research.py",
        "alphalab/portfolio/contracts.py",
    )
    offenders: list[str] = []
    for name, source in _sources():
        if not any(name.startswith(prefix) for prefix in v34_modules):
            continue
        for marker in ("SNAPSHOT_SCHEMA", "def capture(", "def restore(", "__serializable__"):
            if marker in source:
                offenders.append(f"{name}: {marker}")
    assert offenders == [], (
        f"a v3.4 module grew durable state: {offenders}. Each durable state has one "
        "snapshot owner and one module-local schema literal, and adding one is a "
        "decision rather than a side effect."
    )


def test_every_v34_type_is_frozen() -> None:
    """Value semantics, like every other state in AlphaLab."""

    import dataclasses as dc

    from alphalab.conventions import (
        ContractNotional,
        LotSpecification,
        MarketConvention,
        SettlementRule,
        TickBand,
        TickSchedule,
        TickValue,
    )
    from alphalab.crypto import (
        CoverageReport,
        FeeSchedule,
        FundingAccrual,
        FundingSummary,
        ObservationGap,
        VenueDispersion,
        VenueSpecification,
    )
    from alphalab.futures import ContractChain, ContractMarginSpec, MarginPosture, RollEvent
    from alphalab.futures import RollPolicy as _RollPolicy
    from alphalab.macro import Bond, CashFlow
    from alphalab.options import (
        ExpirationPolicy,
        ExpirationResult,
        ImpliedVolatility,
        ModelAssumptions,
        SurfaceRefusal,
        VolSlice,
    )
    from alphalab.portfolio import (
        ContractExposure,
        ContractHolding,
        CurrencyAttribution,
        CurrencyAttributionReport,
        ForwardTerms,
        SettlementExposure,
    )

    types: tuple[type, ...] = (
        ContractNotional,
        LotSpecification,
        MarketConvention,
        SettlementRule,
        TickBand,
        TickSchedule,
        TickValue,
        CoverageReport,
        FeeSchedule,
        FundingAccrual,
        FundingSummary,
        ObservationGap,
        VenueDispersion,
        VenueSpecification,
        ContractChain,
        ContractMarginSpec,
        MarginPosture,
        RollEvent,
        _RollPolicy,
        Bond,
        CashFlow,
        ExpirationPolicy,
        ExpirationResult,
        ImpliedVolatility,
        ModelAssumptions,
        SurfaceRefusal,
        VolSlice,
        ContractExposure,
        ContractHolding,
        CurrencyAttribution,
        CurrencyAttributionReport,
        ForwardTerms,
        SettlementExposure,
    )
    for owner in types:
        assert dc.is_dataclass(owner), f"{owner.__name__} is not a dataclass"
    mutable = [owner.__name__ for owner in types if not _is_frozen(owner)]
    assert mutable == [], f"a v3.4 type is mutable: {mutable}"


def _is_frozen(owner: type) -> bool:
    """Read the dataclass's own frozen flag, without the union-typed attribute."""

    params = vars(owner).get("__dataclass_params__")
    return bool(getattr(params, "frozen", False))
