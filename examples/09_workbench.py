"""
AlphaLab Examples
=================

Example 09 : AlphaLab Workbench

Difficulty : Advanced

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 08

Topics
------

• Workbench Engine
• Project Navigation
• Dataset Browser
• Report Viewer
• Layout Management
• Strategy Studio Integration

Run

    python examples/09_workbench.py
"""

from alphalab.workbench import (
    WorkbenchEngine,
    active_layout,
    active_tabs,
    current_project,
    current_theme,
    saved_layouts,
)


def main() -> None:
    """Demonstrate the AlphaLab Workbench."""

    # ------------------------------------------------------------
    # Step 1 : Initialize Workbench
    # ------------------------------------------------------------

    state = WorkbenchEngine.initialize(
        workbench_id="WORKBENCH-001",
        ts=1_720_000_000.0,
    )

    # ------------------------------------------------------------
    # Step 2 : Open Project
    # ------------------------------------------------------------

    state = WorkbenchEngine.open_project(
        state,
        project_id="PROJECT-001",
        ts=1_720_000_001.0,
    )

    # ------------------------------------------------------------
    # Step 3 : Open Dataset
    # ------------------------------------------------------------

    state = WorkbenchEngine.open_dataset(
        state,
        dataset_id="DATASET-001",
        ts=1_720_000_002.0,
    )

    # ------------------------------------------------------------
    # Step 4 : Open Report
    # ------------------------------------------------------------

    state = WorkbenchEngine.show_report(
        state,
        report_id="REPORT-001",
        ts=1_720_000_003.0,
    )

    # ------------------------------------------------------------
    # Step 5 : Save Workspace Layout
    # ------------------------------------------------------------

    state = WorkbenchEngine.save_layout(
        state,
        layout_id="LAYOUT-001",
        name="Research Layout",
        ts=1_720_000_004.0,
    )

    # ------------------------------------------------------------
    # Step 6 : Restore Layout
    # ------------------------------------------------------------

    state = WorkbenchEngine.restore_layout(
        state,
        layout_id="LAYOUT-001",
        ts=1_720_000_005.0,
    )

    # ------------------------------------------------------------
    # Step 7 : Inspect Workbench
    # ------------------------------------------------------------

    print("=" * 60)
    print("AlphaLab Example 09")
    print("Workbench")
    print("=" * 60)
    print()

    print(f"Current Project : {current_project(state)}")
    print(f"Theme           : {current_theme(state).name}")

    layout = active_layout(state)

    if layout is not None:
        print(f"Active Layout   : {layout.name}")

    print()
    print(f"Open Tabs       : {len(active_tabs(state))}")

    for tab in active_tabs(state):
        print(f"  • {tab.title}")

    print()
    print(f"Saved Layouts   : {len(saved_layouts(state))}")
    print(f"Workbench Events: {len(state.events)}")


if __name__ == "__main__":
    main()
