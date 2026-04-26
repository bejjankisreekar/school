"""
Parse, validate, and persist per-exam mark components (theory / practical / internal, etc.).
"""
from __future__ import annotations

import json
from typing import Any

from django.core.exceptions import ValidationError

from apps.school_data.models import Exam, ExamMarkComponent


def _norm_name(name: str) -> str:
    return (name or "").strip()


def parse_components_from_json(raw: str | None) -> list[dict[str, Any]] | None:
    """
    Parse JSON array of {name, marks} or {"component_name", "max_marks"}.
    Returns None if raw is None/blank (caller may treat as 'not provided').
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    try:
        data = json.loads(s)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid mark components JSON: {exc}") from exc
    if data is None:
        return None
    if not isinstance(data, list):
        raise ValidationError("Mark components must be a JSON array.")
    return data


def normalize_component_items(items: list[Any]) -> list[tuple[str, int]]:
    """Return list of (trimmed_name, max_marks) with validation."""
    out: list[tuple[str, int]] = []
    seen_lower: set[str] = set()
    for i, row in enumerate(items):
        if not isinstance(row, dict):
            raise ValidationError(f"Component {i + 1} must be an object.")
        name = row.get("name")
        if name is None and row.get("component_name") is not None:
            name = row.get("component_name")
        marks = row.get("marks")
        if marks is None and row.get("max_marks") is not None:
            marks = row.get("max_marks")
        nm = _norm_name(str(name) if name is not None else "")
        if not nm:
            raise ValidationError(f"Component {i + 1}: name cannot be empty.")
        try:
            mm = int(marks)
        except (TypeError, ValueError):
            raise ValidationError(f"Component {i + 1}: marks must be an integer.")
        if mm < 0:
            raise ValidationError(f"Component {i + 1}: marks cannot be negative.")
        key = nm.casefold()
        if key in seen_lower:
            raise ValidationError(f"Duplicate component name (case-insensitive): {nm}")
        seen_lower.add(key)
        out.append((nm, mm))
    total = sum(m for _, m in out)
    if out and total <= 0:
        raise ValidationError("Total marks across components must be greater than zero.")
    return out


def replace_exam_mark_components(exam: Exam, normalized: list[tuple[str, int]]) -> None:
    """Replace all components for this exam and set exam.total_marks to the sum when non-empty."""
    ExamMarkComponent.objects.filter(exam=exam).delete()
    if not normalized:
        return
    bulk = [
        ExamMarkComponent(
            exam=exam,
            component_name=name,
            max_marks=marks,
            sort_order=idx,
        )
        for idx, (name, marks) in enumerate(normalized)
    ]
    ExamMarkComponent.objects.bulk_create(bulk)
    exam.total_marks = sum(m for _, m in normalized)
    exam.save(update_fields=["total_marks"])


def sync_exam_mark_components(exam: Exam, raw_json: str | None, *, skip_if_blank: bool = True) -> bool:
    """
    Parse JSON array of components and replace rows for this exam.
    Returns True if the DB was updated. If skip_if_blank and raw is None/empty, returns False (unchanged).
    Use skip_if_blank=False to treat empty string as [] (clear components).
    """
    if raw_json is None or (skip_if_blank and not str(raw_json).strip()):
        return False
    parsed = parse_components_from_json(str(raw_json))
    if parsed is None:
        parsed = []
    normalized = normalize_component_items(parsed)
    replace_exam_mark_components(exam, normalized)
    return True


def components_for_exam_dict(exam: Exam) -> list[dict[str, Any]]:
    rows = exam.mark_components.order_by("sort_order", "id")
    return [{"name": r.component_name, "marks": r.max_marks} for r in rows]


