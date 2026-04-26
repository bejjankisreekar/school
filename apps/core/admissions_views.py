from __future__ import annotations

from datetime import date
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView, View

from apps.accounts.decorators import admin_required
from apps.core.utils import get_active_academic_year_obj
from apps.school_data.models import Admission, AdmissionDocument, AdmissionStatusHistory, Fee, FeeStructure, Student

from .admissions_forms import AdmissionForm

User = get_user_model()


def _student_gender_code(value: str) -> str:
    """
    Student.gender is a 1-char choice field (M/F/O). Admissions store free text.
    Map common labels to codes; fallback to empty string.
    """
    v = (value or "").strip().lower()
    if not v:
        return ""
    if v in ("m", "male", "boy"):
        return "M"
    if v in ("f", "female", "girl"):
        return "F"
    if v in ("o", "other", "others", "non-binary", "nonbinary", "nb"):
        return "O"
    return ""


def _status_badge(status: str) -> str:
    s = (status or "").upper()
    return {
        "NEW": "primary",
        "UNDER_REVIEW": "info",
        "DOCUMENT_PENDING": "warning",
        "APPROVED": "success",
        "REJECTED": "danger",
        "JOINED": "secondary",
    }.get(s, "secondary")


@method_decorator(admin_required, name="dispatch")
class AdmissionsDashboardView(ListView):
    template_name = "core/admissions/dashboard.html"
    model = Admission
    context_object_name = "applications"
    paginate_by = 20

    def get_queryset(self):
        qs = (
            Admission.objects.select_related("applying_for_class", "created_student")
            .order_by("-created_on")
        )
        q = (self.request.GET.get("q") or "").strip()
        status = (self.request.GET.get("status") or "").strip()
        class_id = (self.request.GET.get("classroom") or "").strip()
        date_from = (self.request.GET.get("from") or "").strip()
        date_to = (self.request.GET.get("to") or "").strip()

        if q:
            qs = qs.filter(
                Q(application_id__icontains=q)
                | Q(admission_number__icontains=q)
                | Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
                | Q(mobile_number__icontains=q)
                | Q(aadhaar_or_student_id__icontains=q)
            )
        if status:
            qs = qs.filter(status=status)
        if class_id:
            qs = qs.filter(applying_for_class_id=class_id)
        if date_from:
            try:
                qs = qs.filter(admission_date__gte=date.fromisoformat(date_from))
            except ValueError:
                pass
        if date_to:
            try:
                qs = qs.filter(admission_date__lte=date.fromisoformat(date_to))
            except ValueError:
                pass

        export = (self.request.GET.get("export") or "").lower().strip()
        if export == "csv":
            return qs  # handled in render_to_response
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        stats = Admission.objects.aggregate(
            total=Count("id"),
            pending=Count("id", filter=Q(status__in=[Admission.Status.NEW, Admission.Status.UNDER_REVIEW, Admission.Status.DOCUMENT_PENDING])),
            approved=Count("id", filter=Q(status=Admission.Status.APPROVED)),
            rejected=Count("id", filter=Q(status=Admission.Status.REJECTED)),
            today=Count("id", filter=Q(created_on__date=today)),
        )
        ctx["stats"] = stats
        ctx["filters"] = {
            "q": (self.request.GET.get("q") or "").strip(),
            "status": (self.request.GET.get("status") or "").strip(),
            "classroom": (self.request.GET.get("classroom") or "").strip(),
            "from": (self.request.GET.get("from") or "").strip(),
            "to": (self.request.GET.get("to") or "").strip(),
        }
        ctx["status_badge"] = _status_badge
        ctx["status_choices"] = Admission.Status.choices
        ctx["classes"] = list(
            Admission._meta.get_field("applying_for_class").remote_field.model.objects.order_by("name")
        )
        return ctx

    def render_to_response(self, context, **response_kwargs):
        export = (self.request.GET.get("export") or "").lower().strip()
        if export == "csv":
            rows = [
                ["Admission No", "Student Name", "Class", "Parent", "Mobile", "Admission Date", "Status"]
            ]
            for a in context["applications"]:
                rows.append(
                    [
                        a.admission_number or a.application_id,
                        f"{a.first_name} {a.last_name}".strip(),
                        getattr(a.applying_for_class, "name", "") or "",
                        (a.father_name or a.mother_name or "").strip(),
                        a.mobile_number,
                        str(a.admission_date or ""),
                        a.status,
                    ]
                )
            import csv
            from io import StringIO

            buf = StringIO()
            w = csv.writer(buf)
            w.writerows(rows)
            resp = HttpResponse(buf.getvalue(), content_type="text/csv")
            resp["Content-Disposition"] = f'attachment; filename="admissions_{timezone.localdate()}.csv"'
            return resp
        return super().render_to_response(context, **response_kwargs)


@method_decorator(admin_required, name="dispatch")
class AdmissionCreateView(CreateView):
    template_name = "core/admissions/form.html"
    form_class = AdmissionForm
    model = Admission

    def form_valid(self, form):
        with transaction.atomic():
            inst = form.save(commit=True, user=self.request.user)
            AdmissionStatusHistory.objects.create(
                admission=inst,
                from_status="",
                to_status=inst.status,
                note="Application created",
                created_by=self.request.user,
                modified_by=self.request.user,
            )
        messages.success(self.request, "Admission application created.")
        return redirect("core:school_admissions_list")


@method_decorator(admin_required, name="dispatch")
class AdmissionUpdateView(UpdateView):
    template_name = "core/admissions/form.html"
    form_class = AdmissionForm
    model = Admission
    pk_url_kwarg = "pk"

    def form_valid(self, form):
        inst: Admission = self.get_object()
        prev_status = inst.status
        with transaction.atomic():
            inst = form.save(commit=True, user=self.request.user)
            if inst.status != prev_status:
                AdmissionStatusHistory.objects.create(
                    admission=inst,
                    from_status=prev_status,
                    to_status=inst.status,
                    note="Status updated from edit",
                    created_by=self.request.user,
                    modified_by=self.request.user,
                )
        messages.success(self.request, "Admission application updated.")
        return redirect("core:school_admission_detail", pk=inst.pk)


@method_decorator(admin_required, name="dispatch")
class AdmissionDetailView(DetailView):
    template_name = "core/admissions/detail.html"
    model = Admission
    context_object_name = "app"
    pk_url_kwarg = "pk"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["status_badge"] = _status_badge
        ctx["documents"] = AdmissionDocument.objects.filter(admission=self.object)
        ctx["timeline"] = AdmissionStatusHistory.objects.filter(admission=self.object).select_related("created_by")
        return ctx


@method_decorator(admin_required, name="dispatch")
class AdmissionDeleteView(DeleteView):
    template_name = "core/admissions/delete_confirm.html"
    model = Admission
    pk_url_kwarg = "pk"

    def get_success_url(self):
        return reverse("core:school_admissions_list")


@method_decorator(admin_required, name="dispatch")
class AdmissionSetStatusView(View):
    """Approve / reject (and later: joined)"""

    def post(self, request, pk: int, action: str):
        app = get_object_or_404(Admission.objects.select_related("applying_for_class"), pk=pk)
        action = (action or "").lower().strip()
        if action not in ("approve", "reject", "joined"):
            raise Http404

        prev = app.status
        note = (request.POST.get("note") or "").strip()

        if action == "reject":
            app.status = Admission.Status.REJECTED
            app.rejected_by = request.user
        elif action == "approve":
            # Approval creates Student + Fee lines when possible
            created_student = None
            with transaction.atomic():
                if not app.created_student_id:
                    username = (app.admission_number or app.application_id or "").strip() or app.application_id
                    # Ensure uniqueness (tenant) via Student admission_number clean patterns; fallback by suffix
                    base_username = username
                    i = 1
                    while User.objects.filter(username=username).exists():
                        i += 1
                        username = f"{base_username}-{i}"
                    pwd = f"{app.first_name.capitalize()}@123"
                    user = User.objects.create_user(
                        username=username,
                        password=pwd,
                        first_name=app.first_name,
                        last_name=app.last_name,
                        email=(app.email or "").strip(),
                        role=User.Roles.STUDENT,
                        school=request.user.school,
                        is_first_login=True,
                        is_active=True,
                    )
                    created_student = Student(
                        user=user,
                        classroom=app.applying_for_class,
                        section=None,
                        roll_number="",
                        admission_number=username,
                        date_of_birth=app.date_of_birth,
                        gender=_student_gender_code(app.gender),
                        parent_name=(app.father_name or "").strip(),
                        parent_phone=(app.mobile_number or "").strip(),
                        academic_year=get_active_academic_year_obj() if callable(get_active_academic_year_obj) else None,
                    )
                    created_student.extra_data = {
                        "basic": {
                            "blood_group": app.blood_group or "",
                            "id_number": app.aadhaar_or_student_id or "",
                            "nationality": "",
                            "religion": "",
                            "mother_tongue": "",
                        },
                        "academic": {
                            "previous_institution": app.previous_school_name or "",
                            "previous_marks": app.previous_marks_percent or "",
                        },
                        "status": {"record_status": "ACTIVE"},
                    }
                    if app.passport_photo:
                        created_student.profile_image = app.passport_photo
                    created_student.save_with_audit(request.user)
                    app.created_student = created_student
                app.status = Admission.Status.APPROVED
                app.approved_by = request.user
                app.save_with_audit(request.user)

                # Fee lines (best-effort): create fees for all matching FeeStructure lines for the class+active year
                if app.created_student_id:
                    try:
                        year = app.created_student.academic_year or get_active_academic_year_obj()
                        if year and app.applying_for_class_id:
                            fs_qs = FeeStructure.objects.filter(classroom_id=app.applying_for_class_id, academic_year=year, is_active=True)
                            for fs in fs_qs:
                                Fee.objects.get_or_create(
                                    student=app.created_student,
                                    fee_structure=fs,
                                    academic_year=year,
                                    due_date=timezone.localdate(),
                                    defaults={"amount": fs.amount},
                                )
                    except Exception:
                        pass
            messages.success(request, "Admission approved. Student account created where possible.")
        else:
            app.status = Admission.Status.JOINED

        if action != "approve":
            app.save_with_audit(request.user)

        AdmissionStatusHistory.objects.create(
            admission=app,
            from_status=prev,
            to_status=app.status,
            note=note or f"Status set to {app.status}",
            created_by=request.user,
            modified_by=request.user,
        )
        return redirect("core:school_admission_detail", pk=app.pk)

