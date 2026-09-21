"""A deployment specification: what it requires, what it refuses, and its identity.

The identity is the half worth most of the attention. It is a content digest, so
the properties that matter are that equal content digests equally, that different
content digests differently, and that a mapping's iteration order is not part of
"content".
"""

from decimal import Decimal

import pytest

from alphalab.core.enums import AssetType, OrderType, TimeInForce
from alphalab.data.exceptions import DataValidationError
from alphalab.lifecycle import (
    DEPLOYMENT_SPECIFICATION_SCHEME,
    BrokerCapabilities,
    BrokerRequirements,
    CapitalPolicy,
    DatasetAssumption,
    DeploymentSpecification,
    LifecycleInputError,
    MarketAvailability,
    MarketRequirements,
    RuntimeRequirements,
    StrategyVersionRef,
    Tolerance,
    build_specification,
    dataset_assumption_from,
    specification_for_version,
    specification_id_for,
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

REF = StrategyVersionRef("momentum", 2)
PARAMETERS = {"fast": 10.0, "slow": 30.0}
DATASETS = (DatasetAssumption("prices", "alphalab.dataset.v1:abc123"),)


def limits(
    *,
    order_quantity: str = "100",
    order_notional: str = "10000",
    position_quantity: str = "1000",
    position_notional: str = "100000",
    gross: str = "200000",
    net: str = "150000",
    leverage: str = "2",
) -> RiskLimits:
    return RiskLimits(
        order_size=OrderSizeLimit(Decimal(order_quantity), Decimal(order_notional)),
        position=PositionLimit(Decimal(position_quantity), Decimal(position_notional)),
        exposure=ExposureLimit(Decimal(gross), Decimal(net)),
        leverage=LeverageLimit(Decimal(leverage)),
        margin=MarginLimit(Decimal("0.5")),
        daily_loss=DailyLossLimit(Decimal("5000")),
        drawdown=DrawdownLimit(Decimal("0.2")),
    )


CAPITAL = CapitalPolicy("acct-1", "USD", Decimal("1000000"), ("USD",))
BROKER = BrokerRequirements(
    order_types=frozenset({OrderType.MARKET, OrderType.LIMIT}),
    time_in_force=frozenset({TimeInForce.DAY}),
    asset_classes=frozenset({AssetType.EQUITY}),
    short_selling=True,
    fractional_quantities=False,
)
MARKET = MarketRequirements(
    instruments=("asset-a", "asset-b"),
    venues=("XNYS",),
    calendar_ids=("XNYS",),
    quote_currencies=("USD",),
)
RUNTIME = RuntimeRequirements(
    max_data_staleness_seconds=60.0,
    max_heartbeat_silence_seconds=30.0,
    max_execution_latency_seconds=2.0,
    position_tolerance=Tolerance(absolute=Decimal("0")),
)


def spec(**overrides: object) -> DeploymentSpecification:
    arguments: dict[str, object] = {
        "strategy": REF,
        "parameters": PARAMETERS,
        "datasets": DATASETS,
        "risk": limits(),
        "capital": CAPITAL,
        "broker": BROKER,
        "market": MARKET,
        "runtime": RUNTIME,
    }
    arguments.update(overrides)
    return build_specification(**arguments)  # type: ignore[arg-type]


class TestRequiredFields:
    def test_a_specification_carries_every_field_the_roadmap_names(self) -> None:
        built = spec()
        assert built.strategy == REF
        assert built.parameters == PARAMETERS
        assert built.datasets == DATASETS
        assert built.risk == limits()
        assert built.capital == CAPITAL
        assert built.broker == BROKER
        assert built.market == MARKET
        assert built.runtime == RUNTIME

    def test_a_specification_with_no_dataset_assumption_is_refused(self) -> None:
        with pytest.raises(LifecycleInputError, match="names no dataset"):
            spec(datasets=())

    def test_two_assumptions_claiming_one_role_are_refused(self) -> None:
        with pytest.raises(LifecycleInputError, match="lists a value twice"):
            spec(
                datasets=(
                    DatasetAssumption("prices", "v1"),
                    DatasetAssumption("prices", "v2"),
                )
            )

    def test_a_blank_dataset_version_is_refused(self) -> None:
        with pytest.raises(LifecycleInputError, match="dataset_version"):
            DatasetAssumption("prices", "  ")


class TestCapitalPolicy:
    def test_the_base_currency_has_no_default(self) -> None:
        import inspect

        parameters = inspect.signature(CapitalPolicy).parameters
        assert parameters["base_currency"].default is inspect.Parameter.empty
        assert parameters["allocated_capital"].default is inspect.Parameter.empty

    def test_unfunded_capital_is_refused(self) -> None:
        with pytest.raises(LifecycleInputError, match="funded with nothing"):
            CapitalPolicy("acct", "USD", Decimal("0"), ("USD",))

    def test_no_settlement_currency_is_refused(self) -> None:
        with pytest.raises(LifecycleInputError, match="is empty"):
            CapitalPolicy("acct", "USD", Decimal("1"), ())

    def test_a_repeated_settlement_currency_is_refused(self) -> None:
        with pytest.raises(LifecycleInputError, match="lists a value twice"):
            CapitalPolicy("acct", "USD", Decimal("1"), ("USD", "USD"))


class TestMarketAndRuntimeRequirements:
    def test_an_empty_market_requirement_is_refused(self) -> None:
        for field in ("instruments", "venues", "calendar_ids", "quote_currencies"):
            arguments = {
                "instruments": ("a",),
                "venues": ("v",),
                "calendar_ids": ("c",),
                "quote_currencies": ("USD",),
                field: (),
            }
            with pytest.raises(LifecycleInputError, match="is empty"):
                MarketRequirements(**arguments)

    def test_a_negative_runtime_budget_is_refused(self) -> None:
        with pytest.raises(LifecycleInputError, match="negative budget"):
            RuntimeRequirements(
                max_data_staleness_seconds=-1.0,
                max_heartbeat_silence_seconds=1.0,
                max_execution_latency_seconds=1.0,
                position_tolerance=Tolerance(absolute=Decimal("0")),
            )

    def test_a_zero_budget_is_permitted_because_it_is_a_real_policy(self) -> None:
        requirements = RuntimeRequirements(0.0, 0.0, 0.0, Tolerance(absolute=Decimal("0")))
        assert requirements.max_data_staleness_seconds == 0.0

    def test_every_duration_field_names_its_unit(self) -> None:
        import dataclasses

        durations = [
            field.name
            for field in dataclasses.fields(RuntimeRequirements)
            if field.type in ("float", float)
        ]
        assert durations
        assert all(name.endswith("_seconds") for name in durations)

    def test_an_empty_broker_requirement_set_is_refused(self) -> None:
        with pytest.raises(LifecycleInputError, match="does not trade"):
            BrokerRequirements(
                order_types=frozenset(),
                time_in_force=frozenset({TimeInForce.DAY}),
                asset_classes=frozenset({AssetType.EQUITY}),
                short_selling=False,
                fractional_quantities=False,
            )


class TestIdentity:
    def test_the_same_content_identifies_itself_identically(self) -> None:
        assert spec().specification_id == spec().specification_id

    def test_mapping_order_is_not_part_of_the_content(self) -> None:
        reversed_parameters = dict(reversed(list(PARAMETERS.items())))
        assert spec().specification_id == spec(parameters=reversed_parameters).specification_id

    def test_dataset_order_is_not_part_of_the_content(self) -> None:
        pair = (
            DatasetAssumption("prices", "v-a"),
            DatasetAssumption("signals", "v-b"),
        )
        assert (
            spec(datasets=pair).specification_id
            == spec(datasets=tuple(reversed(pair))).specification_id
        )

    def test_changing_any_field_changes_the_identity(self) -> None:
        baseline = spec().specification_id
        assert spec(parameters={"fast": 11.0, "slow": 30.0}).specification_id != baseline
        assert spec(datasets=(DatasetAssumption("prices", "other"),)).specification_id != baseline
        assert spec(risk=limits(leverage="3")).specification_id != baseline
        assert (
            spec(
                capital=CapitalPolicy("acct-1", "EUR", Decimal("1000000"), ("EUR",))
            ).specification_id
            != baseline
        )
        assert (
            spec(
                broker=BrokerRequirements(
                    order_types=frozenset({OrderType.MARKET}),
                    time_in_force=frozenset({TimeInForce.DAY}),
                    asset_classes=frozenset({AssetType.EQUITY}),
                    short_selling=True,
                    fractional_quantities=False,
                )
            ).specification_id
            != baseline
        )
        assert (
            spec(
                market=MarketRequirements(("asset-a",), ("XNYS",), ("XNYS",), ("USD",))
            ).specification_id
            != baseline
        )
        assert (
            spec(
                runtime=RuntimeRequirements(61.0, 30.0, 2.0, RUNTIME.position_tolerance)
            ).specification_id
            != baseline
        )
        assert spec(strategy=StrategyVersionRef("momentum", 3)).specification_id != baseline

    def test_the_scheme_tag_is_part_of_the_rendering(self) -> None:
        assert DEPLOYMENT_SPECIFICATION_SCHEME == "alphalab.deployment_specification.v1"

    def test_a_handbuilt_identity_does_not_verify(self) -> None:
        built = spec()
        forged = DeploymentSpecification(
            specification_id="not-a-digest",
            strategy=built.strategy,
            parameters=built.parameters,
            datasets=built.datasets,
            risk=built.risk,
            capital=built.capital,
            broker=built.broker,
            market=built.market,
            runtime=built.runtime,
        )
        assert verify_specification_id(built)
        assert not verify_specification_id(forged)

    def test_the_id_function_and_the_builder_agree(self) -> None:
        assert spec().specification_id == specification_id_for(
            REF, PARAMETERS, DATASETS, limits(), CAPITAL, BROKER, MARKET, RUNTIME
        )

    def test_dataset_versions_read_in_role_order(self) -> None:
        built = spec(
            datasets=(
                DatasetAssumption("signals", "v-b"),
                DatasetAssumption("prices", "v-a"),
            )
        )
        assert built.dataset_versions == ("v-a", "v-b")
        assert built.dataset_for("prices") == DatasetAssumption("prices", "v-a")
        assert built.dataset_for("nothing") is None


class TestDatasetLineage:
    def test_an_assumption_read_from_a_dataset_carries_its_derived_version(self) -> None:
        from alphalab.api import ingest_rows
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

        rows = [
            {
                "timestamp": f"2024-01-{day:02d} 00:00:00",
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 10,
            }
            for day in range(1, 8)
        ]
        request = IngestionRequest(
            name="SPEC-PRICES",
            source=raw_source_from_bytes(
                SourceKind.IN_MEMORY, "spec-prices", b"", 0.0, "text/csv", "utf-8"
            ),
            frequency=TimeFrequency.DAILY,
            asset_class=DataAssetClass.EQUITY,
            cleaning_policy=CleaningPolicy(
                duplicates=DuplicatePolicy.KEEP_FIRST,
                ordering=OrderingPolicy.SORT,
                invalid_records=InvalidRecordPolicy.DROP,
                missing_values=MissingValuePolicy.DROP_ROW,
            ),
            price_basis=PriceBasis.RAW,
            timezone_name="UTC",
            symbol="AAPL",
        )
        result = ingest_rows(rows, request)

        assumption = dataset_assumption_from(result.dataset, "prices")

        assert result.dataset.is_versioned
        assert assumption.dataset_version == result.dataset.dataset_version
        assert assumption.role == "prices"

    def test_a_dataset_with_no_provenance_is_refused_rather_than_given_one(self) -> None:
        from alphalab.data.feed import Bar
        from alphalab.data.loader import create_dataset
        from alphalab.data.metadata import DatasetMetadata
        from alphalab.data.symbols import DataAssetClass
        from alphalab.data.time import TimeFrequency

        bars = tuple(
            Bar(
                symbol="X",
                timestamp=float(day * 86_400),
                open=1.0,
                high=2.0,
                low=0.5,
                close=1.5,
                volume=10.0,
            )
            for day in range(1, 6)
        )
        unversioned = create_dataset(
            DatasetMetadata(
                dataset_id="hand-made",
                source_name="memory",
                asset_class=DataAssetClass.EQUITY,
                frequency=TimeFrequency.DAILY,
                start_timestamp=bars[0].timestamp,
                end_timestamp=bars[-1].timestamp,
            ),
            bars,
        )

        assert not unversioned.is_versioned
        with pytest.raises(DataValidationError, match="carries no provenance"):
            dataset_assumption_from(unversioned, "prices")


class TestFromARegisteredVersion:
    def test_parameters_are_derived_from_the_version_rather_than_supplied(self) -> None:
        version = StrategyVersion(
            name="momentum",
            version=2,
            definition=StrategyDefinition(
                "momentum", "Momentum", "1", "author", "desc", {"fast": 10.0, "slow": 30.0}
            ),
            stage=ModelStage.STAGING,
        )
        built = specification_for_version(
            version, DATASETS, limits(), CAPITAL, BROKER, MARKET, RUNTIME
        )
        assert built.strategy == version.ref
        assert built.parameters == {"fast": 10.0, "slow": 30.0}
        assert built.specification_id == spec().specification_id


class TestBrokerAndMarketGaps:
    def test_a_capable_broker_leaves_no_gap(self) -> None:
        assert (
            unmet_broker_requirements(
                BROKER,
                BrokerCapabilities(
                    order_types=frozenset(OrderType),
                    time_in_force=frozenset(TimeInForce),
                    asset_classes=frozenset(AssetType),
                    short_selling=True,
                    fractional_quantities=True,
                ),
            )
            == ()
        )

    def test_every_gap_is_reported_rather_than_only_the_first(self) -> None:
        gaps = unmet_broker_requirements(
            BROKER,
            BrokerCapabilities(
                order_types=frozenset({OrderType.MARKET}),
                time_in_force=frozenset(),
                asset_classes=frozenset(),
                short_selling=False,
                fractional_quantities=False,
            ),
        )
        assert len(gaps) == 4
        assert any("order types" in gap for gap in gaps)
        assert any("short positions" in gap for gap in gaps)

    def test_a_broker_offering_more_than_is_required_is_not_a_gap(self) -> None:
        assert (
            unmet_broker_requirements(
                BROKER,
                BrokerCapabilities(
                    order_types=frozenset(OrderType),
                    time_in_force=frozenset(TimeInForce),
                    asset_classes=frozenset(AssetType),
                    short_selling=True,
                    fractional_quantities=True,
                ),
            )
            == ()
        )

    def test_a_missing_instrument_or_venue_is_reported(self) -> None:
        gaps = unmet_market_requirements(
            MARKET,
            MarketAvailability(
                instruments=frozenset({"asset-a"}),
                venues=frozenset(),
                calendar_ids=frozenset({"XNYS"}),
                quote_currencies=frozenset({"USD"}),
            ),
        )
        assert len(gaps) == 2
        assert any("asset-b" in gap for gap in gaps)
        assert any("venues" in gap for gap in gaps)


class TestValidation:
    def test_a_coherent_specification_reports_nothing(self) -> None:
        assert validate_specification(spec()) == ()

    def test_an_order_larger_than_a_position_is_reported(self) -> None:
        findings = validate_specification(
            spec(risk=limits(order_quantity="5000", order_notional="500000"))
        )
        assert len(findings) >= 2
        assert any("units while a whole position" in finding for finding in findings)
        assert any("notional while a whole position" in finding for finding in findings)

    def test_net_exposure_above_gross_is_reported(self) -> None:
        findings = validate_specification(spec(risk=limits(gross="100", net="200")))
        assert any("net can never exceed gross" in finding for finding in findings)

    def test_an_exposure_cap_the_capital_cannot_fund_is_reported(self) -> None:
        findings = validate_specification(
            spec(capital=CapitalPolicy("acct-1", "USD", Decimal("10"), ("USD",)))
        )
        assert any("leverage limit binds first" in finding for finding in findings)

    def test_a_currency_the_book_cannot_settle_is_reported(self) -> None:
        findings = validate_specification(
            spec(
                market=MarketRequirements(("asset-a",), ("XTKS",), ("XTKS",), ("JPY",)),
            )
        )
        assert any("needs an FX conversion" in finding for finding in findings)

    def test_an_altered_specification_is_reported_before_anything_else(self) -> None:
        built = spec()
        forged = DeploymentSpecification(
            specification_id=built.specification_id,
            strategy=built.strategy,
            parameters={"fast": 999.0},
            datasets=built.datasets,
            risk=built.risk,
            capital=built.capital,
            broker=built.broker,
            market=built.market,
            runtime=built.runtime,
        )
        findings = validate_specification(forged)
        assert findings[0].startswith("specification ")
        assert "altered after it was built" in findings[0]

    def test_validation_reports_and_repairs_nothing(self) -> None:
        built = spec(risk=limits(gross="100", net="200"))
        before = built
        validate_specification(built)
        assert built == before
