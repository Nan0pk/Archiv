"""Evidence-backed Office report generation and validation."""

from archiv.reports.generator import generate_report, generate_report_from_results
from archiv.reports.validation import validate_report

__all__ = ["generate_report", "generate_report_from_results", "validate_report"]
