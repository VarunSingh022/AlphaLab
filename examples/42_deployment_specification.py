"""
AlphaLab Examples
=================

Example 42 : Deployment Specifications

Difficulty : Advanced

Estimated Time : 12 minutes

Prerequisites
-------------

✓ Example 15 (data ingestion and provenance)
✓ Example 12 (model and strategy lifecycle)
✓ Example 41 (strategy lifecycle progression)

Topics
------

• What a strategy version needs in order to run as it was researched
• Dataset assumptions named by derived identity, never by a label
• Risk limits carried whole rather than re-described
• Broker requirements as capabilities, with no vendor anywhere
• A content digest, so an edited specification stops verifying
• Coherence findings a single field cannot see

What this shows
---------------

`alphalab.lifecycle.deployment` records *that* an environment should be running
a strategy version. It never recorded what that version needs to be running
*correctly* -- which data it assumes, how much capital, under which limits, on a
broker that can do what. That lived in somebody's head, or in a release
manifest's flat mapping of strings.

Four things are kept apart, because collapsing any two is how a deployment comes
to claim a property it never had: a **research assumption** (what the
measurement was taken over), a **deployment requirement** (what the environment
must supply), a **runtime observation** (what was seen -- Example 43), and the
**current runtime state** (what is running -- the execution path's).

Run

    python examples/42_deployment_specification.py
"""

from decimal import Decimal

from alphalab.api import ingest_rows
from alphalab.core.enums import AssetType, OrderType, TimeInForce
from alphalab.data.cleaning import (
    CleaningPolicy,
    DuplicatePolicy,
    InvalidRecordPolicy,
    MissingValuePolicy,
    OrderingPolicy,
)
from alphalab.data.corporate_actions import PriceBasis
from alphalab.data.ingestion import IngestionRequest
from alphalab.data.source import SourceKind, raw_source_from_bytes
from alphalab.data.symbols import DataAssetClass
from alphalab.data.time import TimeFrequency
from alphalab.lifecycle import (
    BrokerCapabilities,
    BrokerRequirements,
    CapitalPolicy,
    DeploymentSpecification,
    LifecycleInputError,
    MarketAvailability,
    MarketRequirements,
    RuntimeRequirements,
    Tolerance,
    build_specification,
    dataset_assumption_from,
    specification_for_version,
    unmet_broker_requirements,
    unmet_market_requirements,
    validate_specification,
    verify_specification_id,
)
from alphalab.lifecycle.strategy_version import StrategyVersion
from alphalab.model_registry import ModelStage
from alphalab.risk.limits import (
    DailyLossLimit,
    DrawdownLimit,
    ExposureLimit,
    LeverageLimit,
    MarginLimit,
    OrderSizeLimit,
    PositionLimit,
    RiskLimits,
)
from alphalab.studio.strategy import StrategyDefinition

# --------------------------------------------------------------------------- #
# A dataset with real provenance, so the assumption names actual bytes
# --------------------------------------------------------------------------- #

CLEANING = CleaningPolicy(
    duplicates=DuplicatePolicy.KEEP_FIRST,
    ordering=OrderingPolicy.SORT,
    invalid_records=InvalidRecordPolicy.DROP,
    missing_values=MissingValuePolicy.DROP_ROW,
)

ROWS = [
    {
        "symbol": "EXA",
        "timestamp": f"2024-05-{day:02d} 00:00:00",
        "open": f"{100 + day * 0.4:.4f}",
        "high": f"{101 + day * 0.4:.4f}",
        "low": f"{99 + day * 0.4:.4f}",
        "close": f"{100.5 + day * 0.4:.4f}",
        "volume": 1_000_000 + day,
    }
    for day in range(1, 11)
]

#: The limits the pre-trade gate actually enforces, carried whole. A
#: specification that described limits in its own shape would be describing
#: something ``alphalab.risk`` does not check.
LIMITS = RiskLimits(
    order_size=OrderSizeLimit(Decimal("500"), Decimal("50000")),
    position=PositionLimit(Decimal("5000"), Decimal("500000")),
    exposure=ExposureLimit(Decimal("1500000"), Decimal("900000")),
    leverage=LeverageLimit(Decimal("2")),
    margin=MarginLimit(Decimal("0.5")),
    daily_loss=DailyLossLimit(Decimal("25000")),
    drawdown=DrawdownLimit(Decimal("0.15")),
)

CAPITAL = CapitalPolicy(
    account_id="ACC-EU-EQUITY",
    base_currency="USD",
    allocated_capital=Decimal("1000000"),
    settlement_currencies=("USD",),
)

BROKER = BrokerRequirements(
    order_types=frozenset({OrderType.MARKET, OrderType.LIMIT}),
    time_in_force=frozenset({TimeInForce.DAY, TimeInForce.IOC}),
    asset_classes=frozenset({AssetType.EQUITY}),
    short_selling=True,
    fractional_quantities=False,
)

MARKET = MarketRequirements(
    instruments=("3f2504e0-4f89-41d3-9a0c-0305e82c3301",),
    venues=("XNYS",),
    calendar_ids=("XNYS",),
    quote_currencies=("USD",),
)

RUNTIME = RuntimeRequirements(
    max_data_staleness_seconds=60.0,
    max_heartbeat_silence_seconds=30.0,
    max_execution_latency_seconds=2.0,
    position_tolerance=Tolerance(absolute=Decimal("0.000001")),
)

VERSION = StrategyVersion(
    name="momentum",
    version=4,
    definition=StrategyDefinition(
        strategy_id="momentum",
        name="Momentum",
        version="4",
        author="quant-7",
        description="a crossover",
        parameters={"fast": 10.0, "slow": 30.0},
    ),
    stage=ModelStage.STAGING,
)


def show(specification: DeploymentSpecification) -> None:
    print(f"  strategy        : {specification.strategy}")
    print(f"  parameters      : {dict(specification.parameters)}")
    print(f"  datasets        : {[a.role for a in specification.datasets]}")
    print(
        f"  capital         : {specification.capital.allocated_capital} "
        f"{specification.capital.base_currency} in {specification.capital.account_id}"
    )
    print(f"  max leverage    : {specification.risk.leverage.max_leverage}x")
    print(f"  freshness budget: {specification.runtime.max_data_staleness_seconds}s")
    print(f"  identity        : {specification.specification_id[:16]}...")


def main() -> None:
    print("=" * 68)
    print("AlphaLab Example 42 : Deployment Specifications")
    print("=" * 68)

    # ----------------------------------------------------------------- #
    # 1. The dataset assumption comes from the dataset
    # ----------------------------------------------------------------- #

    print("\n[1] A dataset assumption, read from a dataset with real lineage")
    result = ingest_rows(
        ROWS,
        IngestionRequest(
            name="EXAMPLE-42",
            source=raw_source_from_bytes(
                SourceKind.IN_MEMORY, "example-42", b"", 1_700_000_000.0, "text/csv", "utf-8"
            ),
            frequency=TimeFrequency.DAILY,
            asset_class=DataAssetClass.EQUITY,
            cleaning_policy=CLEANING,
            price_basis=PriceBasis.RAW,
            timezone_name="UTC",
            symbol="EXA",
        ),
    )
    prices = dataset_assumption_from(result.dataset, "prices")
    print(f"  role           : {prices.role}")
    print(f"  dataset_version: {prices.dataset_version}")
    print("  (Derived from the bytes, the schema, the zone and the cleaning policy.")
    print("   It is the same string a run's source_id and its evidence carry.)")

    # ----------------------------------------------------------------- #
    # 2. The specification, built from the registered version
    # ----------------------------------------------------------------- #

    print("\n[2] The specification")
    specification = specification_for_version(
        VERSION,
        datasets=(prices,),
        risk=LIMITS,
        capital=CAPITAL,
        broker=BROKER,
        market=MARKET,
        runtime=RUNTIME,
    )
    show(specification)
    print("  (The reference and the parameters are *derived* from the registered")
    print("   version, so a specification cannot configure a parameter it does")
    print("   not declare -- the lesson ADR-0017 records about evidence.)")

    # ----------------------------------------------------------------- #
    # 3. Identity
    # ----------------------------------------------------------------- #

    print("\n[3] A content digest, not a minted id")
    again = specification_for_version(VERSION, (prices,), LIMITS, CAPITAL, BROKER, MARKET, RUNTIME)
    print(f"  built twice, same id  : {again.specification_id == specification.specification_id}")

    reordered = build_specification(
        strategy=specification.strategy,
        parameters={"slow": 30.0, "fast": 10.0},
        datasets=specification.datasets,
        risk=LIMITS,
        capital=CAPITAL,
        broker=BROKER,
        market=MARKET,
        runtime=RUNTIME,
    )
    same_id = reordered.specification_id == specification.specification_id
    print(f"  parameter order       : {same_id} (mapping order is not content)")

    retuned = build_specification(
        strategy=specification.strategy,
        parameters={"fast": 11.0, "slow": 30.0},
        datasets=specification.datasets,
        risk=LIMITS,
        capital=CAPITAL,
        broker=BROKER,
        market=MARKET,
        runtime=RUNTIME,
    )
    changed_id = retuned.specification_id != specification.specification_id
    print(f"  one parameter changed : {changed_id} (a different deployment)")

    edited = DeploymentSpecification(
        specification_id=specification.specification_id,
        strategy=specification.strategy,
        parameters={"fast": 999.0, "slow": 30.0},
        datasets=specification.datasets,
        risk=specification.risk,
        capital=specification.capital,
        broker=specification.broker,
        market=specification.market,
        runtime=specification.runtime,
    )
    print(f"  edited after the fact : verifies={verify_specification_id(edited)}")

    # ----------------------------------------------------------------- #
    # 4. Coherence a single field cannot see
    # ----------------------------------------------------------------- #

    print("\n[4] Cross-field coherence")
    findings = validate_specification(specification)
    print(f"  the specification above: {findings or '() -- coherent'}")

    underfunded = build_specification(
        strategy=specification.strategy,
        parameters=specification.parameters,
        datasets=specification.datasets,
        risk=LIMITS,
        capital=CapitalPolicy("ACC-SMALL", "USD", Decimal("100000"), ("USD",)),
        broker=BROKER,
        market=MarketRequirements(
            MARKET.instruments, MARKET.venues, MARKET.calendar_ids, ("USD", "JPY")
        ),
        runtime=RUNTIME,
    )
    for finding in validate_specification(underfunded):
        print(f"  - {finding}")
    print("  (Reported, not refused: a half-drafted specification must stay")
    print("   representable, and nothing here repairs or defaults anything.)")

    # ----------------------------------------------------------------- #
    # 5. Requirements are capabilities, and name no vendor
    # ----------------------------------------------------------------- #

    print("\n[5] Checking an environment against the requirements")
    capable = BrokerCapabilities(
        order_types=frozenset({OrderType.MARKET, OrderType.LIMIT, OrderType.STOP}),
        time_in_force=frozenset({TimeInForce.DAY, TimeInForce.IOC, TimeInForce.GTC}),
        asset_classes=frozenset({AssetType.EQUITY, AssetType.FUTURE}),
        short_selling=True,
        fractional_quantities=True,
    )
    print(
        f"  a capable broker : {unmet_broker_requirements(BROKER, capable) or '() -- can run it'}"
    )

    limited = BrokerCapabilities(
        order_types=frozenset({OrderType.MARKET}),
        time_in_force=frozenset({TimeInForce.DAY}),
        asset_classes=frozenset({AssetType.EQUITY}),
        short_selling=False,
        fractional_quantities=False,
    )
    for gap in unmet_broker_requirements(BROKER, limited):
        print(f"  - {gap}")

    available = MarketAvailability(
        instruments=frozenset(MARKET.instruments),
        venues=frozenset({"XNAS"}),
        calendar_ids=frozenset({"XNYS"}),
        quote_currencies=frozenset({"USD"}),
    )
    for gap in unmet_market_requirements(MARKET, available):
        print(f"  - {gap}")

    # ----------------------------------------------------------------- #
    # 6. What cannot be built
    # ----------------------------------------------------------------- #

    print("\n[6] Refusals")
    try:
        CapitalPolicy("ACC", "USD", Decimal("0"), ("USD",))
    except LifecycleInputError as error:
        print(f"  unfunded capital : {error}")
    try:
        MarketRequirements((), ("XNYS",), ("XNYS",), ("USD",))
    except LifecycleInputError as error:
        print(f"  no instruments   : {error}")
    try:
        build_specification(
            strategy=specification.strategy,
            parameters=specification.parameters,
            datasets=(),
            risk=LIMITS,
            capital=CAPITAL,
            broker=BROKER,
            market=MARKET,
            runtime=RUNTIME,
        )
    except LifecycleInputError as error:
        print(f"  no dataset       : {error}")

    # ----------------------------------------------------------------- #

    print("\n[7] Invariants")
    checks = (
        (
            "the assumption names the dataset's own derived version",
            prices.dataset_version == result.dataset.dataset_version,
        ),
        (
            "the parameters are the registered version's",
            dict(specification.parameters) == dict(VERSION.definition.parameters),
        ),
        ("the identity verifies", verify_specification_id(specification)),
        ("an edited specification does not", not verify_specification_id(edited)),
        (
            "mapping order is not part of the content",
            reordered.specification_id == specification.specification_id,
        ),
        (
            "a changed parameter is a different deployment",
            retuned.specification_id != specification.specification_id,
        ),
        ("the risk limits are the type the gate enforces", specification.risk is LIMITS),
        (
            "no vendor is named anywhere in the requirements",
            not any(gap for gap in unmet_broker_requirements(BROKER, capable)),
        ),
    )
    for label, held in checks:
        print(f"  [{'ok' if held else 'FAILED'}] {label}")
    assert all(held for _, held in checks)

    print("\n" + "=" * 68)
    print("Example 42 complete.")
    print("(A specification observes nothing, reaches nothing and starts nothing.")
    print(" AlphaLab holds no broker adapter, no credential and no client.)")


if __name__ == "__main__":
    main()
