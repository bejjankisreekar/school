"""
AI-style performance summary panel for the analytics dashboard.
"""
from __future__ import annotations

from typing import Any

from apps.core.utils import has_feature_access
from apps.school_data.models import Marks


def build_ai_reports_panel_context(school, *, user=None) -> dict[str, Any]:
    if not school or not school.has_feature("ai_reports"):
        return {}
    if not has_feature_access(school, "reports", user=user):
        return {}

    marks_qs = Marks.objects.filter(
        exam__isnull=False,
        student__user__school=school,
    )
    by_student: dict = {}
    for m in marks_qs.select_related("student", "subject", "exam"):
        sid = m.student_id
        if sid not in by_student:
            by_student[sid] = {"student": m.student, "total_o": 0, "total_m": 0}
        by_student[sid]["total_o"] += m.marks_obtained
        by_student[sid]["total_m"] += m.total_marks
    perf = []
    for d in by_student.values():
        tm = d["total_m"]
        pct = round((d["total_o"] / tm * 100) if tm else 0, 1)
        perf.append({"student": d["student"], "pct": pct})
    perf.sort(key=lambda x: -x["pct"])

    by_class: dict = {}
    for m in marks_qs.select_related("student__classroom"):
        cid = m.student.classroom_id if m.student.classroom_id else 0
        if cid not in by_class:
            by_class[cid] = {
                "name": m.student.classroom.name if m.student.classroom else "Unassigned",
                "total_o": 0,
                "total_m": 0,
                "count": 0,
            }
        by_class[cid]["total_o"] += m.marks_obtained
        by_class[cid]["total_m"] += m.total_marks
        by_class[cid]["count"] += 1
    class_perf = [
        {
            "name": v["name"],
            "pct": round((v["total_o"] / v["total_m"] * 100) if v["total_m"] else 0, 1),
            "count": v["count"],
        }
        for v in by_class.values()
    ]

    return {
        "dash_show_ai_panel": True,
        "ai_student_performance": perf[:20],
        "ai_class_performance": class_perf,
    }
