"""Deterministic export engine for standard formats."""

import csv
import io
import json
from decimal import Decimal
from enum import Enum
from typing import Any

from alphalab.reporting.exceptions import ExportError
from alphalab.reporting.report import Report
from alphalab.reporting.sections import ReportSectionType


class ReportJSONEncoder(json.JSONEncoder):
    """Deterministic JSON encoder for report content.

    A ``Decimal`` is written as its exact text -- ``"1000.50"`` -- as every
    durable format in AlphaLab writes one. Until v3.12 it was written as a
    ``float``, which loses digits a money amount has, and any other value this
    encoder did not know was written as ``str(value)``, whose text can carry a
    memory address and so differs between two exports of one report. Such a
    value is now refused, and the export with it.
    """

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, Enum):
            return obj.name
        return super().default(obj)


def export_json(report: Report) -> str:
    """Deterministically exports a report to JSON."""
    try:
        data = {
            "report_id": report.report_id,
            "title": report.title,
            "timestamp": report.timestamp,
            "report_type": report.report_type.name,
            "summary": report.summary,
            "metadata": dict(report.metadata),
            "sections": [
                {
                    "name": s.name,
                    "section_type": s.section_type.name,
                    "description": s.description,
                    "content": s.content,
                }
                for s in report.sections
            ],
        }
        # Strict JSON: a non-finite float is refused, not written as NaN.
        return json.dumps(data, cls=ReportJSONEncoder, sort_keys=True, indent=2, allow_nan=False)
    except Exception as e:
        raise ExportError(f"Failed to export report {report.report_id} to JSON: {e}") from e


def export_csv(report: Report) -> str:
    """Deterministically exports all tabular sections of a report to CSV."""
    try:
        output = io.StringIO()

        for section in report.sections:
            if section.section_type != ReportSectionType.TABLE:
                continue

            if not isinstance(section.content, list) or not section.content:
                continue

            output.write(f"--- {section.name} ---\n")
            keys = list(section.content[0].keys())
            writer = csv.DictWriter(output, fieldnames=keys)
            writer.writeheader()
            for row in section.content:
                # Stringify all values to ensure deterministic formatting
                str_row = {k: str(v) for k, v in row.items()}
                writer.writerow(str_row)
            output.write("\n")

        return output.getvalue()
    except Exception as e:
        raise ExportError(f"Failed to export report {report.report_id} to CSV: {e}") from e


def export_markdown(report: Report) -> str:
    """Deterministically exports a report to a Markdown document."""
    try:
        lines = [
            f"# {report.title}",
            f"**Report ID:** {report.report_id}  ",
            f"**Type:** {report.report_type.name}  ",
            f"**Timestamp:** {report.timestamp}  ",
            "",
            f"{report.summary}",
            "",
        ]

        for section in report.sections:
            lines.append(f"## {section.name}")
            if section.description:
                lines.append(f"*{section.description}*")

            if section.section_type == ReportSectionType.TEXT:
                lines.append(str(section.content))
            elif section.section_type == ReportSectionType.METRICS:
                if isinstance(section.content, dict):
                    for k, v in section.content.items():
                        lines.append(f"- **{k}:** {v}")
            elif (
                section.section_type == ReportSectionType.TABLE
                and isinstance(section.content, list)
                and section.content
            ):
                keys = list(section.content[0].keys())
                header = "| " + " | ".join(keys) + " |"
                sep = "|" + "|".join(["---"] * len(keys)) + "|"
                lines.extend([header, sep])
                for row in section.content:
                    row_str = "| " + " | ".join(str(row.get(k, "")) for k in keys) + " |"
                    lines.append(row_str)
            lines.append("")

        return "\n".join(lines)
    except Exception as e:
        raise ExportError(f"Failed to export report {report.report_id} to Markdown: {e}") from e
