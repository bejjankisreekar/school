"""
Exam toppers panel for the analytics dashboard (GET filters use top_* prefix).
"""
from __future__ import annotations

import json
from typing import Any

from django.db.models import Sum

from apps.core.utils import has_feature_access
from apps.school_data.models import ClassRoom, Exam, Marks, Section, Student


def _exam_read_qs_safe():
    return Exam.objects.defer("start_time", "end_time", "session", "academic_year")


def build_toppers_panel_context(request, school, *, user=None) -> dict[str, Any]:
    """Context for toppers partial; empty dict if feature off."""
    empty: dict[str, Any] = {}
    if not school or not has_feature_access(school, "topper_list", user=user):
        return empty
    if not has_feature_access(school, "exams", user=user):
        return empty

    exam_id = request.GET.get("top_exam") or request.GET.get("exam") or ""
    classroom_id = request.GET.get("top_classroom") or request.GET.get("classroom") or ""
    section_id = request.GET.get("top_section") or request.GET.get("section") or ""
    top_n = int(request.GET.get("top_limit") or request.GET.get("top") or "10")
    if top_n not in (3, 10):
        top_n = 10

    marks_qs = Marks.objects.filter(
        exam__isnull=False,
        student__user__school=school,
    )
    if exam_id.isdigit():
        marks_qs = marks_qs.filter(exam_id=int(exam_id))
    if classroom_id.isdigit():
        marks_qs = marks_qs.filter(student__classroom_id=int(classroom_id))
    if section_id.isdigit():
        marks_qs = marks_qs.filter(student__section_id=int(section_id))

    marks_by_class = marks_qs.values("student__classroom__name", "student_id").annotate(
        total_o=Sum("marks_obtained"), total_m=Sum("total_marks")
    )
    by_class: dict[str, list] = {}
    for m in marks_by_class:
        cname = m["student__classroom__name"] or "Unassigned"
        if cname not in by_class:
            by_class[cname] = []
        pct = round((m["total_o"] / m["total_m"] * 100) if m["total_m"] else 0, 1)
        by_class[cname].append({"student_id": m["student_id"], "pct": pct})
    for c in by_class:
        by_class[c].sort(key=lambda x: -x["pct"])
        by_class[c] = by_class[c][:top_n]
    student_ids: set[int] = set()
    for v in by_class.values():
        for x in v:
            student_ids.add(x["student_id"])
    students = {
        s.id: s for s in Student.objects.filter(id__in=student_ids).select_related("user")
    }
    class_toppers = []
    for cname, rows in sorted(by_class.items()):
        class_toppers.append(
            {
                "class": cname,
                "toppers": [
                    {"student": students.get(r["student_id"]), "pct": r["pct"]} for r in rows
                ],
            }
        )
    school_agg = marks_qs.values("student_id").annotate(
        total_o=Sum("marks_obtained"), total_m=Sum("total_marks")
    )
    school_list = [
        (x["student_id"], round((x["total_o"] / x["total_m"] * 100) if x["total_m"] else 0, 1))
        for x in school_agg
    ]
    school_list.sort(key=lambda x: -x[1])
    sid_set = {sid for sid, _ in school_list[:top_n]}
    school_students = {
        s.id: s
        for s in Student.objects.filter(id__in=sid_set).select_related("user", "classroom", "section")
    }
    school_toppers_list = [
        {"student": school_students.get(sid), "pct": pct}
        for sid, pct in school_list[:top_n]
        if school_students.get(sid)
    ]
    subj_agg = marks_qs.values(
        "subject_id", "subject__name", "student_id"
    ).annotate(total_o=Sum("marks_obtained"), total_m=Sum("total_marks"))
    by_subj: dict[str, list] = {}
    for m in subj_agg:
        sname = m["subject__name"] or "Unknown"
        if sname not in by_subj:
            by_subj[sname] = []
        pct = round((m["total_o"] / m["total_m"] * 100) if m["total_m"] else 0, 1)
        by_subj[sname].append((m["student_id"], pct))
    for s in by_subj:
        by_subj[s].sort(key=lambda x: -x[1])
        by_subj[s] = by_subj[s][:3]
    subj_students = {
        s.id: s
        for s in Student.objects.filter(
            id__in={x[0] for v in by_subj.values() for x in v}
        ).select_related("user")
    }
    subject_toppers = [
        {
            "subject": sname,
            "toppers": [
                {"student": subj_students.get(x[0]), "pct": x[1]}
                for x in v
                if subj_students.get(x[0])
            ],
        }
        for sname, v in sorted(by_subj.items())
    ]

    chart_labels = [
        t["student"].user.get_full_name() or t["student"].user.username
        for t in school_toppers_list
    ]
    chart_values = [t["pct"] for t in school_toppers_list]
    class_chart_labels = [c["class"] for c in class_toppers]
    class_chart_values = [
        round(sum(x["pct"] for x in c["toppers"]) / len(c["toppers"]), 1) if c["toppers"] else 0
        for c in class_toppers
    ]

    exams = _exam_read_qs_safe().order_by("-date")
    classes = ClassRoom.objects.all().order_by("name")
    sections = Section.objects.all().order_by("name")

    return {
        "dash_show_toppers_panel": True,
        "toppers_class_toppers": class_toppers,
        "toppers_school_toppers": school_toppers_list,
        "toppers_subject_toppers": subject_toppers,
        "toppers_exams": exams,
        "toppers_classes": classes,
        "toppers_sections": sections,
        "toppers_selected_exam": exam_id,
        "toppers_selected_classroom": classroom_id,
        "toppers_selected_section": section_id,
        "toppers_top_n": top_n,
        "toppers_chart_labels": json.dumps(chart_labels),
        "toppers_chart_values": json.dumps(chart_values),
        "toppers_class_chart_labels": json.dumps(class_chart_labels),
        "toppers_class_chart_values": json.dumps(class_chart_values),
    }
