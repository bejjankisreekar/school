from django.db import connection
from apps.customers.models import School


def main() -> None:
    schemas = list(School.objects.exclude(schema_name="public").values_list("schema_name", flat=True))
    cur = connection.cursor()

    for schema in schemas:
        cur.execute(
            """
            select column_name
            from information_schema.columns
            where table_schema=%s and table_name=%s
            order by ordinal_position
            """,
            [schema, "school_data_exam"],
        )
        cols = [r[0] for r in cur.fetchall()]
        has_new_cols = all(c in cols for c in ["date", "class_name", "section", "created_by_id"])

        cur.execute(
            'SELECT count(*) FROM "' + schema + '".school_data_exam'
        )
        exam_total = cur.fetchone()[0]

        cur.execute(
            'SELECT count(*) FROM "' + schema + '".school_data_exam WHERE class_name IS NOT NULL AND section IS NOT NULL AND date IS NOT NULL'
        )
        new_exam_rows = cur.fetchone()[0]

        cur.execute(
            'SELECT count(*) FROM "' + schema + '".school_data_exam WHERE date IS NULL'
        )
        legacy_exam_rows = cur.fetchone()[0]

        cur.execute(
            'SELECT count(*) FROM "' + schema + '".school_data_marks'
        )
        marks_total = cur.fetchone()[0]

        cur.execute(
            'SELECT count(*) FROM "' + schema + '".school_data_marks WHERE exam_id IS NOT NULL'
        )
        marks_exam_id_not_null = cur.fetchone()[0]

        cur.execute(
            'SELECT count(*) FROM "' + schema + '".school_data_classroom_sections'
        )
        classroom_sections_total = cur.fetchone()[0]

        print(
            f"{schema}: exam_total={exam_total}, new_exam_rows={new_exam_rows}, legacy_exam_rows={legacy_exam_rows}, classroom_sections_total={classroom_sections_total}, has_new_cols={has_new_cols}, marks_total={marks_total}, marks_exam_id_not_null={marks_exam_id_not_null}"
        )


if __name__ == "__main__":
    main()

