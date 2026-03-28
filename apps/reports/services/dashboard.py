"""
School reports hub (/school/reports/) — context for the dashboard template.

Add entries to report_cards_primary / report_cards_more as new reports ship.
Each card: title, description, icon (Bootstrap Icons class), theme (Bootstrap color),
url, button_label; optional same_page_anchor for in-page anchors.
Use django.urls.reverse(...) for internal URLs and has_feature_access(school, ...) when gating cards.
"""

from django.urls import reverse

from apps.core.utils import has_feature_access

from .analytics_dashboard import build_analytics_summary_metrics
from .dashboard_charts import extend_dashboard_charts_context
from .hub_charts import build_hub_chart_context


def build_school_reports_dashboard_context(school) -> dict:
    """Analytics hub: KPI metrics + report cards for schools with the reports feature."""
    report_cards_primary: list = []
    report_cards_more: list = []

    analytics = build_analytics_summary_metrics(school)

    if school and has_feature_access(school, "reports"):
        # Primary report (first in module)
        report_cards_primary.append(
            {
                "title": "Students by Class",
                "description": "View the number of students enrolled in each class.",
                "icon": "bi-bar-chart-fill",
                "theme": "primary",
                "url": reverse("reports:students_by_class"),
                "button_label": "View report",
            }
        )
        report_cards_more.append(
            {
                "title": "Student analytics",
                "description": "Students by class and section, admission trends, and year context.",
                "icon": "bi-people-fill",
                "theme": "info",
                "url": reverse("reports:student_analytics"),
                "ready": True,
            }
        )

    hub_charts = build_hub_chart_context(school)
    extend_dashboard_charts_context(school, hub_charts)
    hub_charts_enabled = bool(school and has_feature_access(school, "reports"))

    return {
        "analytics_metrics": analytics["analytics_metrics"],
        "attendance_enabled": analytics["attendance_enabled"],
        "report_cards_primary": report_cards_primary,
        "report_cards_more": report_cards_more,
        "hub_charts_enabled": hub_charts_enabled,
        **hub_charts,
    }
