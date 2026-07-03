"""High-performance benchmarking suite for the functional Reporting Engine."""

import time

from alphalab.reporting import (
    Report,
    ReportingEngine,
    ReportSection,
    ReportSectionType,
    ReportType,
)


def run_benchmark() -> None:
    state = ReportingEngine.initialize("REP-BENCH")

    N = 1_000
    print(f"Starting Reporting Benchmark: Registering and Exporting {N} Reports...")

    # 1. Pre-generate dummy objects to isolate engine overhead
    section = ReportSection(
        name="Data",
        section_type=ReportSectionType.TABLE,
        content=[{"Col1": i, "Col2": i * 2} for i in range(10)],
    )

    reports = tuple(
        Report(
            report_id=f"R-{i}",
            title=f"Benchmark Report {i}",
            timestamp=float(1000 + i),
            report_type=ReportType.PERFORMANCE,
            sections=(section,),
        )
        for i in range(N)
    )

    start = time.perf_counter()

    # 2. Process Registration and Exports
    for i, report in enumerate(reports):
        state = ReportingEngine.register_report(state, report)
        state = ReportingEngine.export_report(state, report.report_id, "JSON", float(1000 + i))
        state = ReportingEngine.export_report(state, report.report_id, "CSV", float(1000 + i))

    duration = time.perf_counter() - start

    ops_sec = N / duration
    print(f"Reporting Synthesis & Export Time: {duration:.4f}s")
    print(f"Total Reports Registered: {state.statistics.total_reports_generated}")
    print(f"Total Exports Completed: {state.statistics.total_exports_completed}")
    print(f"Throughput: {ops_sec:.2f} reports/sec (including 2 exports per report)")


if __name__ == "__main__":
    run_benchmark()
