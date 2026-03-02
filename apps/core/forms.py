INPUT_CLASS = (
    "form-control block w-full rounded-lg border border-slate-200/80 dark:border-slate-700 px-3 py-2 text-sm "
    "text-slate-900 placeholder:text-slate-400 shadow-sm focus:border-indigo-500 focus:outline-none "
    "focus:ring-2 focus:ring-indigo-500/40 dark:bg-slate-900/80 dark:text-slate-50"
)

from django import forms
from .models import Homework, Marks, Attendance


class HomeworkForm(forms.ModelForm):
    class Meta:
        model = Homework
        fields = ["subject", "title", "description", "due_date"]
        widgets = {
            "subject": forms.Select(attrs={"class": INPUT_CLASS}),
            "title": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Homework title"}),
            "description": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 3, "placeholder": "Description"}),
            "due_date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
        }


class TeacherHomeworkForm(forms.ModelForm):
    """Homework form for teachers - subject is set from teacher's assignment."""
    class Meta:
        model = Homework
        fields = ["title", "description", "due_date"]
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Homework title"}),
            "description": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 3, "placeholder": "Description"}),
            "due_date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
        }


class MarksForm(forms.ModelForm):
    class Meta:
        model = Marks
        fields = ["student", "subject", "exam_name", "exam_date", "marks_obtained", "total_marks"]
        widgets = {
            "student": forms.Select(attrs={"class": INPUT_CLASS}),
            "subject": forms.Select(attrs={"class": INPUT_CLASS}),
            "exam_name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. Mid-term Exam"}),
            "exam_date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
            "marks_obtained": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0}),
            "total_marks": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1}),
        }


class AttendanceForm(forms.ModelForm):
    class Meta:
        model = Attendance
        fields = ["student", "date", "status"]
        widgets = {
            "student": forms.Select(attrs={"class": INPUT_CLASS}),
            "date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
            "status": forms.Select(attrs={"class": INPUT_CLASS}),
        }
