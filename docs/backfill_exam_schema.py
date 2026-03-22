"""
Backfill tenant Exam schema to support the standardized model.

This script is designed for the current dev/demo environment where:
  - Tenant tables still have legacy Exam columns:
      start_date, end_date, classroom_id, teacher_id
  - Our Django model now expects:
      date, class_name, section, created_by

What it does per tenant schema:
  1) Adds missing columns to school_data_exam.
  2) For each legacy exam row, creates one new per-section exam row
     (class_name=classroom.name, section=section.name, date=start_date).
  3) Retargets school_data_marks.exam_id:
       old exam_id -> new exam_id based on the student's section.

Notes:
  - The script is idempotent (it won't re-insert if a matching exam row exists).
  - It does not delete legacy exam rows; instead it leaves class_name/section/date null for them.
"""

from __future__ import annotations

from django.db import connection, transaction
from apps.customers.models import School


class LegacyExam:
    """Lightweight container for legacy exam rows."""

    def __init__(
        self,
        legacy_exam_id: int,
        name: str,
        start_date: str,  # DB returns date as string for raw cursor
        end_date: str,
        classroom_id: int,
        teacher_id: int | None,
    ) -> None:
        self.legacy_exam_id = legacy_exam_id
        self.name = name
        self.start_date = start_date
        self.end_date = end_date
        self.classroom_id = classroom_id
        self.teacher_id = teacher_id


def _column_exists(schema_name: str, table_name: str, column_name: str) -> bool:
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema=%s AND table_name=%s AND column_name=%s
            LIMIT 1
            """,
            [schema_name, table_name, column_name],
        )
        return cur.fetchone() is not None


def _ensure_exam_columns(schema_name: str) -> None:
    table = "school_data_exam"
    # Add columns with nullable defaults to preserve legacy rows.
    # Django model field constraints may be stricter, but until we fully migrate data
    # we keep them NULLable at DB level.
    required = [
        ("date", "date"),
        ("class_name", "varchar(50)"),
        ("section", "varchar(10)"),
        ("created_by_id", "bigint"),
    ]

    for col, col_type in required:
        if _column_exists(schema_name, table, col):
            continue
        with connection.cursor() as cur:
            cur.execute(
                f'ALTER TABLE "{schema_name}"."{table}" ADD COLUMN "{col}" {col_type} NULL'
            )


def _fetch_legacy_exams(schema_name: str) -> list[LegacyExam]:
    table = "school_data_exam"
    with connection.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, name, start_date, end_date, classroom_id, teacher_id
            FROM "{schema_name}"."{table}"
            """
        )
        rows = cur.fetchall()
    exams: list[LegacyExam] = []
    for (eid, name, start_date, end_date, classroom_id, teacher_id) in rows:
        exams.append(
            LegacyExam(
                legacy_exam_id=int(eid),
                name=name,
                start_date=str(start_date),
                end_date=str(end_date),
                classroom_id=int(classroom_id),
                teacher_id=int(teacher_id) if teacher_id is not None else None,
            )
        )
    return exams


def _fetch_sections_for_classroom(schema_name: str, classroom_id: int) -> list[tuple[int, str]]:
    with connection.cursor() as cur:
        cur.execute(
            f"""
            SELECT s.id, s.name
            FROM "{schema_name}"."school_data_classroom_sections" cs
            JOIN "{schema_name}"."school_data_section" s
              ON s.id = cs.section_id
            WHERE cs.classroom_id = %s
            ORDER BY s.name
            """,
            [classroom_id],
        )
        rows = cur.fetchall()
    return [(int(r[0]), r[1]) for r in rows]


def _fetch_classroom_name(schema_name: str, classroom_id: int) -> str:
    with connection.cursor() as cur:
        cur.execute(
            f"""
            SELECT name
            FROM "{schema_name}"."school_data_classroom"
            WHERE id = %s
            """,
            [classroom_id],
        )
        row = cur.fetchone()
    return row[0]


def _fetch_teacher_user_id(schema_name: str, teacher_id: int) -> int | None:
    with connection.cursor() as cur:
        cur.execute(
            f"""
            SELECT user_id
            FROM "{schema_name}"."school_data_teacher"
            WHERE id = %s
            """,
            [teacher_id],
        )
        row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else None


def _existing_section_exam_id(
    schema_name: str,
    *,
    name: str,
    start_date: str,
    end_date: str,
    classroom_id: int,
    teacher_id: int | None,
    class_name: str,
    section: str,
) -> int | None:
    # Match by both legacy identifiers + new fields to avoid duplicates.
    with connection.cursor() as cur:
        cur.execute(
            f"""
            SELECT id
            FROM "{schema_name}"."school_data_exam"
            WHERE name = %s
              AND start_date = %s
              AND end_date = %s
              AND classroom_id = %s
              AND (teacher_id = %s OR (teacher_id IS NULL AND %s IS NULL))
              AND date = %s
              AND class_name = %s
              AND section = %s
            LIMIT 1
            """,
            [
                name,
                start_date,
                end_date,
                classroom_id,
                teacher_id,
                teacher_id,
                start_date,
                class_name,
                section,
            ],
        )
        row = cur.fetchone()
    return int(row[0]) if row else None


def _insert_section_exam(
    schema_name: str,
    *,
    name: str,
    start_date: str,
    end_date: str,
    classroom_id: int,
    teacher_id: int | None,
    created_by_id: int | None,
    class_name: str,
    section: str,
) -> int:
    with connection.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO "{schema_name}"."school_data_exam"
              (name, start_date, end_date, classroom_id, teacher_id, date, class_name, section, created_by_id)
            VALUES
              (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            [
                name,
                start_date,
                end_date,
                classroom_id,
                teacher_id,
                start_date,  # date
                class_name,
                section,
                created_by_id,
            ],
        )
        new_id = cur.fetchone()[0]
    return int(new_id)


def _retarget_marks(
    schema_name: str,
    *,
    old_exam_id: int,
    new_exam_id: int,
    section_id: int,
) -> int:
    # Retarget marks of students in that section only.
    with connection.cursor() as cur:
        cur.execute(
            f"""
            UPDATE "{schema_name}"."school_data_marks" m
            SET exam_id = %s
            FROM "{schema_name}"."school_data_student" st
            WHERE m.exam_id = %s
              AND m.student_id = st.id
              AND st.section_id = %s
            """,
            [new_exam_id, old_exam_id, section_id],
        )
        return cur.rowcount


def backfill_for_schema(schema_name: str) -> None:
    _ensure_exam_columns(schema_name)

    legacy_exams = _fetch_legacy_exams(schema_name)
    if not legacy_exams:
        return

    # Process legacy exams one by one to keep the mapping simple.
    for legacy in legacy_exams:
        # Skip if this legacy exam already has per-section splits (heuristic).
        # If the legacy exam row itself now has class_name/section set, we still
        # create missing section exams but we won't treat it as "legacy" for mapping.
        # Instead, we always create section exams based on the student's section.

        classroom_name = _fetch_classroom_name(schema_name, legacy.classroom_id)
        sections = _fetch_sections_for_classroom(schema_name, legacy.classroom_id)
        if not sections:
            continue

        created_by_id: int | None = None
        if legacy.teacher_id is not None:
            created_by_id = _fetch_teacher_user_id(schema_name, legacy.teacher_id)

        for section_id, section_name in sections:
            existing_id = _existing_section_exam_id(
                schema_name,
                name=legacy.name,
                start_date=legacy.start_date,
                end_date=legacy.end_date,
                classroom_id=legacy.classroom_id,
                teacher_id=legacy.teacher_id,
                class_name=classroom_name,
                section=section_name,
            )
            if existing_id is None:
                new_id = _insert_section_exam(
                    schema_name,
                    name=legacy.name,
                    start_date=legacy.start_date,
                    end_date=legacy.end_date,
                    classroom_id=legacy.classroom_id,
                    teacher_id=legacy.teacher_id,
                    created_by_id=created_by_id,
                    class_name=classroom_name,
                    section=section_name,
                )
            else:
                new_id = existing_id

            # Retarget marks for students in that section from the old exam_id.
            _retarget_marks(
                schema_name,
                old_exam_id=legacy.legacy_exam_id,
                new_exam_id=new_id,
                section_id=section_id,
            )


def main() -> None:
    tenant_schemas = list(School.objects.exclude(schema_name="public").values_list("schema_name", flat=True))
    print("Tenant schemas:", tenant_schemas)

    for schema_name in tenant_schemas:
        print(f"\n=== Backfilling schema: {schema_name} ===")
        with transaction.atomic():
            backfill_for_schema(schema_name)

    print("\nDone.")


if __name__ == "__main__":
    main()

