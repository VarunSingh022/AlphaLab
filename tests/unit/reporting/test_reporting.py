"""Comprehensive tests validating strict reporting synthesis and immutable formats."""

from decimal import Decimal

import pytest

from alphalab.reporting import (
    Dashboard,
    DashboardCard,
    DashboardSection,
    DashboardTable,
    Report,
    ReportingAdapter,
    ReportingEngine,
    ReportingState,
    ReportingValidationError,
    ReportSection,
    ReportSectionType,
    ReportType,
    dashboard_summary,
    export_csv,
    export_json,
    export_markdown,
    export_statistics,
    get_export,
    latest_report,
    report_count,
)


@pytest.fixture
def base_state() -> ReportingState:
    return ReportingEngine.initialize("REP-01")


@pytest.fixture
def sample_metrics_section() -> ReportSection:
    return ReportSection(
        name="Summary Metrics",
        section_type=ReportSectionType.METRICS,
        content={"Win Rate": 0.55, "Profit Factor": 1.5, "Total PnL": Decimal("1000.50")},
        description="Core algorithm metrics",
    )


@pytest.fixture
def sample_table_section() -> ReportSection:
    return ReportSection(
        name="Trade Log",
        section_type=ReportSectionType.TABLE,
        content=[
            {"Trade ID": "T1", "Symbol": "AAPL", "PnL": 500.0},
            {"Trade ID": "T2", "Symbol": "MSFT", "PnL": -100.0},
        ],
    )


@pytest.fixture
def sample_report(
    sample_metrics_section: ReportSection, sample_table_section: ReportSection
) -> Report:
    return Report(
        report_id="R-100",
        title="Weekly Performance",
        timestamp=1000.0,
        report_type=ReportType.PERFORMANCE,
        sections=(sample_metrics_section, sample_table_section),
        summary="Positive week overall.",
    )


# --- ENGINE & VALIDATION TESTS ---


def test_initialization() -> None:
    state = ReportingEngine.initialize("E1")
    assert state.engine_id == "E1"
    assert report_count(state) == 0

    with pytest.raises(ValueError):
        ReportingEngine.initialize("")


def test_register_report_success(base_state: ReportingState, sample_report: Report) -> None:
    s1 = ReportingEngine.register_report(base_state, sample_report)

    assert report_count(s1) == 1
    assert latest_report(s1) == sample_report
    assert any(type(e).__name__ == "ReportGenerated" for e in s1.events)


def test_register_duplicate_report(base_state: ReportingState, sample_report: Report) -> None:
    s1 = ReportingEngine.register_report(base_state, sample_report)
    with pytest.raises(ReportingValidationError, match="Duplicate"):
        ReportingEngine.register_report(s1, sample_report)


def test_validate_empty_sections() -> None:
    bad_report = Report("R1", "Title", 1000.0, ReportType.RISK, sections=())
    state = ReportingEngine.initialize("E1")
    with pytest.raises(ReportingValidationError, match="least one section"):
        ReportingEngine.register_report(state, bad_report)


def test_validate_duplicate_sections(sample_metrics_section: ReportSection) -> None:
    bad_report = Report(
        "R1",
        "Title",
        1000.0,
        ReportType.RISK,
        sections=(sample_metrics_section, sample_metrics_section),
    )
    state = ReportingEngine.initialize("E1")
    with pytest.raises(ReportingValidationError, match="Duplicate section name"):
        ReportingEngine.register_report(state, bad_report)


def test_register_dashboard_success(base_state: ReportingState) -> None:
    card = DashboardCard("Uptime", "99.9%")
    table = DashboardTable("Top Assets", ("Symbol", "Vol"), "data.assets")
    section = DashboardSection("Overview", cards=(card,), tables=(table,))
    db = Dashboard("D1", "Main Dashboard", 1000.0, (section,))

    s1 = ReportingEngine.register_dashboard(base_state, db)

    assert len(dashboard_summary(s1)) == 1
    assert dashboard_summary(s1)[0] == db
    assert any(type(e).__name__ == "DashboardGenerated" for e in s1.events)


def test_register_dashboard_validation(base_state: ReportingState) -> None:
    db = Dashboard("", "Main", 1000.0, ())
    with pytest.raises(ReportingValidationError, match="empty"):
        ReportingEngine.register_dashboard(base_state, db)


# --- EXPORT TESTS ---


def test_export_json(sample_report: Report) -> None:
    output = export_json(sample_report)
    assert "R-100" in output
    assert "Weekly Performance" in output
    assert "PERFORMANCE" in output
    assert "1000.5" in output  # Decimal serialized safely


def test_export_csv(sample_report: Report) -> None:
    output = export_csv(sample_report)
    assert "--- Trade Log ---" in output
    assert "Trade ID,Symbol,PnL" in output
    assert "T1,AAPL,500.0" in output
    assert "T2,MSFT,-100.0" in output


def test_export_csv_ignores_non_tables(sample_report: Report) -> None:
    output = export_csv(sample_report)
    # The Metrics section should not appear as CSV data
    assert "Win Rate" not in output


def test_export_markdown(sample_report: Report) -> None:
    output = export_markdown(sample_report)
    assert "# Weekly Performance" in output
    assert "**Report ID:** R-100" in output
    assert "## Summary Metrics" in output
    assert "- **Win Rate:** 0.55" in output
    assert "## Trade Log" in output
    assert "| Trade ID | Symbol | PnL |" in output


def test_engine_export_routing(base_state: ReportingState, sample_report: Report) -> None:
    s1 = ReportingEngine.register_report(base_state, sample_report)

    s2 = ReportingEngine.export_report(s1, "R-100", "JSON", 1001.0)
    assert export_statistics(s2).total_exports_completed == 1
    assert get_export(s2, "R-100", "JSON") is not None

    s3 = ReportingEngine.export_report(s2, "R-100", "CSV", 1002.0)
    assert export_statistics(s3).total_exports_completed == 2
    assert get_export(s3, "R-100", "CSV") is not None


def test_engine_export_unsupported(base_state: ReportingState, sample_report: Report) -> None:
    s1 = ReportingEngine.register_report(base_state, sample_report)
    s2 = ReportingEngine.export_report(s1, "R-100", "PDF", 1001.0)

    # Should catch error and log ExportFailed event without crashing the engine
    assert export_statistics(s2).total_exports_failed == 1
    assert any(type(e).__name__ == "ExportFailed" for e in s2.events)


# --- ADAPTER TESTS ---


def test_adapter_flat_metrics() -> None:
    class DummyStruct:
        __slots__ = ["a", "b"]

        def __init__(self, a: int, b: int) -> None:
            self.a = a
            self.b = b

    res = ReportingAdapter.extract_flat_metrics(DummyStruct(1, 2))
    assert res == {"a": 1, "b": 2}


def test_adapter_generic_performance() -> None:
    metrics = {"sharpe": 2.0, "drawdown": 0.1}
    report = ReportingAdapter.generic_performance_report("R1", 1000.0, "Test", metrics)

    assert report.report_id == "R1"
    assert report.report_type == ReportType.PERFORMANCE
    assert len(report.sections) == 1
    assert report.sections[0].section_type == ReportSectionType.METRICS
    assert report.sections[0].content["sharpe"] == 2.0


# --- VIEWS & IMMUTABILITY TESTS ---


def test_views(base_state: ReportingState, sample_report: Report) -> None:
    s1 = ReportingEngine.register_report(base_state, sample_report)
    s2 = ReportingEngine.export_report(s1, "R-100", "JSON", 1001.0)

    assert report_count(s2) == 1
    assert latest_report(s2) == sample_report
    assert export_statistics(s2).total_reports_generated == 1


def test_immutability(base_state: ReportingState, sample_report: Report) -> None:
    s1 = ReportingEngine.register_report(base_state, sample_report)

    assert base_state is not s1
    assert len(base_state.reports) == 0
    assert len(s1.reports) == 1
