from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.http import url_has_allowed_host_and_scheme
from django.db import connection
from django.db.utils import ProgrammingError
from django.core.management import call_command

from apps.customers.models import School
from apps.school_data.models import Student

from .forms import (
    SchoolInstitutionProfileForm,
    UserAccountCoreForm,
    UserAvatarForm,
    UserProfileContactForm,
    UserProfileOrganizationForm,
    UserProfilePersonalForm,
    UserProfilePreferencesForm,
    UserProfileSecurityPrefsForm,
)
from .models import BlockedLoginAttempt, User, UserProfile


def _require_admin_or_super(user):
    role = getattr(user, "role", None)
    if role not in (User.Roles.ADMIN, User.Roles.SUPERADMIN):
        raise PermissionDenied


def _dashboard_redirect_for_user(user):
    role = getattr(user, "role", None)
    if role == "SUPERADMIN":
        return redirect("core:super_admin:control_center")
    if role == "ADMIN":
        return redirect("core:admin_dashboard")
    if role == "TEACHER":
        return redirect("core:teacher_dashboard")
    if role == "PARENT":
        return redirect("core:parent_dashboard")
    return redirect("core:student_dashboard")


def _profile_breadcrumb_home(user):
    role = getattr(user, "role", None)
    if role == "SUPERADMIN":
        return reverse("core:super_admin:control_center"), "Control Center"
    if role == "ADMIN":
        return reverse("core:admin_dashboard"), "Dashboard"
    if role == "TEACHER":
        return reverse("core:teacher_dashboard"), "Dashboard"
    if role == "PARENT":
        return reverse("core:parent_dashboard"), "Dashboard"
    return reverse("core:student_dashboard"), "Dashboard"


def _touch_password_changed(user) -> None:
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.password_changed_at = timezone.now()
    profile.save(update_fields=["password_changed_at", "profile_updated_at"])


def _sessions_for_user(user, current_session_key: str | None):
    uid = str(user.pk)
    rows = []
    for s in Session.objects.filter(expire_date__gte=timezone.now()).order_by("-expire_date"):
        try:
            data = s.get_decoded()
        except Exception:
            continue
        if data.get("_auth_user_id") != uid:
            continue
        rows.append(
            {
                "session_key_short": f"{s.session_key[:10]}…",
                "expires": s.expire_date,
                "is_current": s.session_key == current_session_key,
            }
        )
    return rows


def _logout_other_sessions(user, keep_session_key: str | None) -> int:
    uid = str(user.pk)
    deleted = 0
    for s in list(Session.objects.filter(expire_date__gte=timezone.now())):
        if keep_session_key and s.session_key == keep_session_key:
            continue
        try:
            data = s.get_decoded()
        except Exception:
            continue
        if data.get("_auth_user_id") == uid:
            s.delete()
            deleted += 1
    return deleted


@login_required
def account_profile(request):
    """Full account profile, preferences, and security overview."""
    user = request.user
    profile, _ = UserProfile.objects.get_or_create(user=user)

    profile_school = getattr(user, "school", None)
    can_edit_school = bool(
        profile_school and getattr(user, "role", None) == User.Roles.ADMIN
    )
    school_form = None
    school_save_failed = False

    core_form = UserAccountCoreForm(instance=user)
    personal_form = UserProfilePersonalForm(instance=profile)
    contact_form = UserProfileContactForm(instance=profile)
    org_form = UserProfileOrganizationForm(instance=profile, user=user)
    prefs_form = UserProfilePreferencesForm(instance=profile)
    security_form = UserProfileSecurityPrefsForm(instance=profile)
    avatar_form = UserAvatarForm(instance=profile)

    profile_save_failed = False
    prefs_save_failed = False
    security_save_failed = False
    avatar_save_failed = False

    if request.method == "POST":
        action = request.POST.get("action") or ""

        if action == "save_profile":
            core_form = UserAccountCoreForm(request.POST, instance=user)
            personal_form = UserProfilePersonalForm(request.POST, instance=profile)
            contact_form = UserProfileContactForm(request.POST, instance=profile)
            org_form = UserProfileOrganizationForm(request.POST, instance=profile, user=user)
            if (
                core_form.is_valid()
                and personal_form.is_valid()
                and contact_form.is_valid()
                and org_form.is_valid()
            ):
                core_form.save()
                personal_form.save()
                contact_form.save()
                org_form.save()
                profile.profile_updated_by = user
                profile.save()
                messages.success(request, "Profile updated successfully.")
                return redirect("accounts:account_profile")
            profile_save_failed = True

        elif action == "save_preferences":
            prefs_form = UserProfilePreferencesForm(request.POST, instance=profile)
            if prefs_form.is_valid():
                prefs_form.save()
                messages.success(request, "Preferences saved.")
                return redirect("accounts:account_profile")
            prefs_save_failed = True

        elif action == "save_security":
            security_form = UserProfileSecurityPrefsForm(request.POST, instance=profile)
            if security_form.is_valid():
                security_form.save()
                messages.success(request, "Security settings updated.")
                return redirect("accounts:account_profile")
            security_save_failed = True

        elif action == "upload_avatar":
            avatar_form = UserAvatarForm(request.POST, request.FILES, instance=profile)
            if avatar_form.is_valid():
                inst = avatar_form.save(commit=False)
                inst.profile_updated_by = user
                inst.save()
                messages.success(request, "Profile photo updated.")
                return redirect("accounts:account_profile")
            avatar_save_failed = True

        elif action == "logout_all_sessions":
            deleted = _logout_other_sessions(user, request.session.session_key)
            messages.success(
                request,
                f"Signed out of {deleted} other session(s). This device stays signed in.",
            )
            return redirect("accounts:account_profile")

        elif action == "save_school":
            if not can_edit_school:
                messages.error(request, "You do not have permission to update institution details.")
                return redirect("accounts:account_profile")
            school_form = SchoolInstitutionProfileForm(request.POST, instance=user.school)
            if school_form.is_valid():
                school_form.save()
                messages.success(request, "Institution profile updated.")
                return redirect("accounts:account_profile")
            school_save_failed = True

    if can_edit_school and school_form is None:
        school_form = SchoolInstitutionProfileForm(instance=profile_school)

    dash_url, dash_label = _profile_breadcrumb_home(user)
    sessions = _sessions_for_user(user, request.session.session_key)

    return render(
        request,
        "accounts/account_profile.html",
        {
            "can_access_account_settings": getattr(user, "role", None)
            in (User.Roles.ADMIN, User.Roles.SUPERADMIN),
            "account_user": user,
            "profile": profile,
            "core_form": core_form,
            "personal_form": personal_form,
            "contact_form": contact_form,
            "org_form": org_form,
            "prefs_form": prefs_form,
            "security_form": security_form,
            "avatar_form": avatar_form,
            "profile_dashboard_url": dash_url,
            "profile_dashboard_label": dash_label,
            "sessions": sessions,
            "other_session_count": sum(1 for s in sessions if not s["is_current"]),
            "profile_save_failed": profile_save_failed,
            "prefs_save_failed": prefs_save_failed,
            "security_save_failed": security_save_failed,
            "avatar_save_failed": avatar_save_failed,
            "profile_school": profile_school,
            "can_edit_school": can_edit_school,
            "school_form": school_form,
            "school_save_failed": school_save_failed,
        },
    )


@login_required
def change_password(request):
    """Voluntary password change while logged in (not first-login flow)."""
    if getattr(request.user, "is_first_login", False):
        return redirect("accounts:change_password_first")

    form = PasswordChangeForm(user=request.user, data=request.POST or None)
    for name, field in form.fields.items():
        field.widget.attrs.setdefault("class", "form-control form-control-lg rounded-4 border-secondary-subtle")
        if "password" in name:
            field.widget.attrs.setdefault("autocomplete", "new-password" if name != "old_password" else "current-password")

    if request.method == "POST" and form.is_valid():
        form.save()
        update_session_auth_hash(request, form.user)
        _touch_password_changed(form.user)
        messages.success(request, "Your password was updated successfully.")
        return _dashboard_redirect_for_user(request.user)

    dash_url, dash_label = _profile_breadcrumb_home(request.user)
    u = request.user
    return render(
        request,
        "accounts/change_password.html",
        {
            "form": form,
            "settings_dashboard_url": dash_url,
            "settings_dashboard_label": dash_label,
            "can_access_account_settings": getattr(u, "role", None)
            in (User.Roles.ADMIN, User.Roles.SUPERADMIN),
        },
    )


@login_required
def account_settings(request):
    """
    Account settings:
    - Student: show only the logged-in student's details (scoped to their school).
    - Admin/Superadmin: master dropdown settings.
    """
    if getattr(request.user, "role", None) == User.Roles.STUDENT:
        student = Student.objects.select_related("user", "classroom", "section").filter(user=request.user).first()
        if not student:
            messages.error(request, "Student profile not found for this account.")
            raise PermissionDenied
        # School-level restriction (student must belong to the same school as the logged-in user)
        if getattr(student.user, "school", None) != getattr(request.user, "school", None):
            raise PermissionDenied

        if request.method == "POST":
            action = (request.POST.get("action") or "").strip()
            if action == "save_student_contact":
                phone = (request.POST.get("phone") or "").strip()
                student.phone = phone
                student.modified_by = request.user
                student.save(update_fields=["phone", "modified_by", "modified_on"])
                messages.success(request, "Your contact details were updated.")
                return redirect(reverse("accounts:account_settings"))

        dash_url, dash_label = _profile_breadcrumb_home(request.user)
        return render(
            request,
            "accounts/student_account_settings.html",
            {
                "settings_dashboard_url": dash_url,
                "settings_dashboard_label": dash_label,
                "student": student,
                "account_user": request.user,
            },
        )

    _require_admin_or_super(request.user)
    dash_url, dash_label = _profile_breadcrumb_home(request.user)

    try:
        from apps.school_data.models import DropdownMaster
    except Exception:
        DropdownMaster = None

    # IMPORTANT: /accounts/ routes are usually public-schema, but this specific settings page is
    # tenant-bound (see apps.core.tenant_bind.TENANT_BIND_FORCE_PATHS). We still keep tenant_context
    # for superadmin "pick a school" support.
    from apps.customers.models import School

    req_school = getattr(request.user, "school", None)
    selected_school = req_school

    # Superadmin may not be bound to a school. Allow selecting a school for master fields.
    school_choices = []
    if getattr(request.user, "role", None) == User.Roles.SUPERADMIN:
        school_choices = list(School.objects.exclude(schema_name="public").order_by("name", "code"))
        code = (request.GET.get("school") or "").strip()
        if code:
            picked = School.objects.filter(code=code).first()
            if picked:
                selected_school = picked
        if not selected_school or getattr(selected_school, "schema_name", None) == "public":
            selected_school = school_choices[0] if school_choices else None

    # For school admins, the selected school must be their linked school (never PUBLIC).
    if getattr(request.user, "role", None) == User.Roles.ADMIN:
        if not selected_school or getattr(selected_school, "schema_name", None) == "public":
            selected_school = None
    tenant_ctx = None
    if DropdownMaster is not None and selected_school:
        try:
            from django_tenants.utils import tenant_context

            tenant_ctx = tenant_context(selected_school)
        except Exception:
            tenant_ctx = None

    if request.method == "POST" and DropdownMaster is not None and tenant_ctx is not None:
        action = (request.POST.get("action") or "").strip()

        if action == "add_field":
            field_key = (request.POST.get("field_key") or "").strip()
            display_label = (request.POST.get("display_label") or "").strip()
            option_value = (request.POST.get("option_value") or "").strip()
            category = (request.POST.get("category") or "").strip()
            try:
                display_order = int((request.POST.get("display_order") or "0").strip() or 0)
            except Exception:
                display_order = 0
            is_active = (request.POST.get("is_active") or "") in ("1", "true", "on", "yes")

            if not field_key or not display_label or not option_value:
                messages.error(request, "Please fill Field Key, Display Label and Option Value.")
            else:
                try:
                    with tenant_ctx:
                        obj = DropdownMaster(
                            field_key=field_key,
                            display_label=display_label,
                            option_value=option_value,
                            category=category,
                            display_order=max(0, int(display_order)),
                            is_active=bool(is_active),
                        )
                        obj.save_with_audit(request.user)
                    messages.success(request, "Master field option added.")
                except Exception:
                    messages.error(request, "Could not add this option. It may already exist.")
            return redirect(reverse("accounts:account_settings"))

        if action == "toggle_active":
            try:
                pk = int(request.POST.get("id") or "0")
            except Exception:
                pk = 0
            if pk:
                try:
                    with tenant_ctx:
                        obj = DropdownMaster.objects.filter(pk=pk).first()
                        if obj:
                            obj.is_active = not bool(obj.is_active)
                            obj.modified_by = request.user
                            obj.save(update_fields=["is_active", "modified_by", "modified_on"])
                            messages.success(request, "Status updated.")
                except Exception:
                    messages.error(request, "Could not update status.")
            return redirect(reverse("accounts:account_settings"))

        if action == "delete_field":
            try:
                pk = int(request.POST.get("id") or "0")
            except Exception:
                pk = 0
            if pk:
                try:
                    with tenant_ctx:
                        DropdownMaster.objects.filter(pk=pk).delete()
                    messages.success(request, "Deleted.")
                except Exception:
                    messages.error(request, "Could not delete.")
            return redirect(reverse("accounts:account_settings"))

        if action == "edit_field":
            try:
                pk = int(request.POST.get("id") or "0")
            except Exception:
                pk = 0
            field_key = (request.POST.get("field_key") or "").strip()
            display_label = (request.POST.get("display_label") or "").strip()
            option_value = (request.POST.get("option_value") or "").strip()
            category = (request.POST.get("category") or "").strip()
            try:
                display_order = int((request.POST.get("display_order") or "0").strip() or 0)
            except Exception:
                display_order = 0
            is_active = (request.POST.get("is_active") or "") in ("1", "true", "on", "yes")

            if pk and field_key and display_label and option_value:
                try:
                    with tenant_ctx:
                        DropdownMaster.objects.filter(pk=pk).update(
                            field_key=field_key,
                            display_label=display_label,
                            option_value=option_value,
                            category=category,
                            display_order=max(0, int(display_order)),
                            is_active=bool(is_active),
                            modified_by=request.user,
                        )
                    messages.success(request, "Updated.")
                except Exception:
                    messages.error(request, "Could not update. It may conflict with an existing option.")
            else:
                messages.error(request, "Invalid update data.")
            return redirect(reverse("accounts:account_settings"))

    dropdown_fields = []
    categories = []
    if DropdownMaster is not None and tenant_ctx is not None:
        try:
            with tenant_ctx:
                dropdown_fields = list(DropdownMaster.objects.all())
                categories = list(
                    DropdownMaster.objects.exclude(category="")
                    .values_list("category", flat=True)
                    .distinct()
                    .order_by("category")
                )
        except ProgrammingError:
            # Tenant migrations not applied for this school schema (or schema mismatch).
            attempted_schema = getattr(selected_school, "schema_name", None) if selected_school else None

            # Best-effort auto-migrate this tenant once per session to self-heal new deployments.
            did_try = bool(request.session.get("did_autofix_dropdownmaster"))
            if not did_try and attempted_schema:
                request.session["did_autofix_dropdownmaster"] = True
                try:
                    call_command(
                        "migrate_schemas",
                        schema_name=attempted_schema,
                        tenant=True,
                        skip_checks=True,
                        app_label="school_data",
                    )
                    with tenant_ctx:
                        dropdown_fields = list(DropdownMaster.objects.all())
                        categories = list(
                            DropdownMaster.objects.exclude(category="")
                            .values_list("category", flat=True)
                            .distinct()
                            .order_by("category")
                        )
                except Exception:
                    messages.error(
                        request,
                        f"Master fields table is not ready for this school (schema: {attempted_schema or 'unknown'}). "
                        "Please run tenant migrations and try again.",
                    )
            else:
                messages.error(
                    request,
                    f"Master fields table is not ready for this school (schema: {attempted_schema or 'unknown'}). "
                    "Please run tenant migrations and try again.",
                )

    # Auto-seed a few common master fields for a brand new tenant.
    if DropdownMaster is not None and tenant_ctx is not None and selected_school and not dropdown_fields:
        if not request.session.get("seeded_dropdownmaster_defaults"):
            request.session["seeded_dropdownmaster_defaults"] = True
            try:
                with tenant_ctx:
                    seeds = [
                        # gender
                        ("gender", "Male", "male", "common", 0, True),
                        ("gender", "Female", "female", "common", 1, True),
                        ("gender", "Other", "other", "common", 2, True),
                        # nationality
                        ("nationality", "Indian", "indian", "common", 0, True),
                        # blood group
                        ("blood_group", "A+", "A+", "common", 0, True),
                        ("blood_group", "B+", "B+", "common", 1, True),
                        ("blood_group", "O+", "O+", "common", 2, True),
                        ("blood_group", "AB+", "AB+", "common", 3, True),
                        # staff type
                        ("staff_type", "Teaching", "teaching", "teacher", 0, True),
                        ("staff_type", "Non-Teaching", "non_teaching", "teacher", 1, True),
                        # department
                        ("department", "General", "general", "teacher", 0, True),
                        # subject
                        ("subject", "General", "general", "teacher", 0, True),
                    ]
                    for fk, lbl, val, cat, order, active in seeds:
                        DropdownMaster.objects.get_or_create(
                            field_key=fk,
                            option_value=val,
                            defaults={
                                "display_label": lbl,
                                "category": cat,
                                "display_order": order,
                                "is_active": active,
                                "created_by": request.user,
                                "modified_by": request.user,
                            },
                        )
                    dropdown_fields = list(DropdownMaster.objects.all())
                    categories = list(
                        DropdownMaster.objects.exclude(category="")
                        .values_list("category", flat=True)
                        .distinct()
                        .order_by("category")
                    )
            except Exception:
                # Seeding is best-effort; don't block the page.
                pass
    elif getattr(request.user, "role", None) == User.Roles.ADMIN and selected_school is None:
        messages.error(request, "This admin user is not linked to a school tenant. Please contact Super Admin.")

    # Legacy context key (Analytics card removed from template). Keeps render safe if templates still reference it.
    analytics_fields = []

    return render(
        request,
        "accounts/account_settings.html",
        {
            "settings_dashboard_url": dash_url,
            "settings_dashboard_label": dash_label,
            "dropdown_fields": dropdown_fields,
            "dropdown_categories": categories,
            "settings_school": selected_school,
            "settings_school_choices": school_choices,
            "analytics_fields": analytics_fields,
        },
    )


def logout_view(request):
    logout(request)
    return redirect("core:home")


@login_required
def change_password_first(request):
    """Force password change on first login."""
    user = request.user
    if not getattr(user, "is_first_login", False):
        # Already changed; redirect to dashboard
        role = getattr(user, "role", None)
        if role == "SUPERADMIN":
            return redirect("core:super_admin:control_center")
        if role == "ADMIN":
            return redirect("core:admin_dashboard")
        if role == "TEACHER":
            return redirect("core:teacher_dashboard")
        if role == "PARENT":
            return redirect("core:parent_dashboard")
        return redirect("core:student_dashboard")

    form = PasswordChangeForm(user=user, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        update_session_auth_hash(request, form.user)
        _touch_password_changed(user)
        user.is_first_login = False
        user.save(update_fields=["is_first_login"])

        role = getattr(user, "role", None)
        if role == "SUPERADMIN":
            return redirect("core:super_admin:control_center")
        if role == "ADMIN":
            return redirect("core:admin_dashboard")
        if role == "TEACHER":
            return redirect("core:teacher_dashboard")
        if role == "PARENT":
            return redirect("core:parent_dashboard")
        return redirect("core:student_dashboard")

    return render(request, "accounts/change_password_first.html", {"form": form})


@never_cache
@ensure_csrf_cookie
def login_view(request, login_type: str = "portal"):
    if request.method == "POST":
        # Security requirement:
        # - Only show the "inactive account" screen when credentials are correct.
        # - Do NOT leak whether a username/email exists or is inactive.
        raw_login = (request.POST.get("username") or "").strip()
        raw_password = request.POST.get("password") or ""
        # Normalize username for AuthenticationForm:
        # If the user typed an admission number / username with different casing,
        # map it to the canonical stored username before validating the form.
        post_data = request.POST
        if raw_login and raw_password:
            try:
                cand = (
                    User.objects.filter(username__iexact=raw_login)
                    .only("id", "username", "email", "role", "is_active", "school_id")
                    .first()
                    or User.objects.filter(email__iexact=raw_login)
                    .only("id", "username", "email", "role", "is_active", "school_id")
                    .first()
                )
                if cand and cand.check_password(raw_password):
                    role = getattr(cand, "role", "") or ""
                    school_block = None
                    if role != User.Roles.SUPERADMIN:
                        school_block = School.school_login_block_reason_for_code(
                            getattr(cand, "school_id", None)
                        )
                    if school_block:
                        ip = request.META.get("HTTP_X_FORWARDED_FOR") or request.META.get("REMOTE_ADDR")
                        ip = (ip.split(",")[0].strip() if isinstance(ip, str) and ip else None)
                        try:
                            BlockedLoginAttempt.objects.create(
                                username=cand.username or raw_login,
                                role=role,
                                ip_address=ip,
                                reason=school_block,
                                school_id=getattr(cand, "school_id", None),
                                user=cand,
                            )
                        except Exception:
                            pass
                        return redirect(
                            f"{reverse('accounts:access_restricted')}?type={school_block}&role={role}&login_type={login_type}"
                        )
                    # Admin/Superadmin login should not be affected.
                    if role in (User.Roles.TEACHER, User.Roles.STUDENT) and not cand.is_active:
                        # Audit log
                        ip = request.META.get("HTTP_X_FORWARDED_FOR") or request.META.get("REMOTE_ADDR")
                        ip = (ip.split(",")[0].strip() if isinstance(ip, str) and ip else None)
                        try:
                            BlockedLoginAttempt.objects.create(
                                username=cand.username or raw_login,
                                role=role,
                                ip_address=ip,
                                reason="inactive_account",
                                school=getattr(cand, "school", None),
                                user=cand,
                            )
                        except Exception:
                            pass
                        return redirect(
                            f"{reverse('accounts:access_restricted')}?type=inactive&role={role}&login_type={login_type}"
                        )
                    # Credentials are correct and account is active.
                    # Force the canonical username into the posted data so AuthenticationForm succeeds.
                    try:
                        post_data = request.POST.copy()
                        post_data["username"] = cand.username
                    except Exception:
                        post_data = request.POST
            except Exception:
                pass

        form = AuthenticationForm(request, data=post_data)
        if form.is_valid():
            user = form.get_user()
            school_block = None
            if getattr(user, "role", None) != User.Roles.SUPERADMIN:
                school_block = School.school_login_block_reason_for_code(getattr(user, "school_id", None))
            if school_block:
                ip = request.META.get("HTTP_X_FORWARDED_FOR") or request.META.get("REMOTE_ADDR")
                ip = (ip.split(",")[0].strip() if isinstance(ip, str) and ip else None)
                try:
                    BlockedLoginAttempt.objects.create(
                        username=user.username,
                        role=getattr(user, "role", "") or "",
                        ip_address=ip,
                        reason=school_block,
                        school_id=getattr(user, "school_id", None),
                        user=user,
                    )
                except Exception:
                    pass
                return redirect(
                    f"{reverse('accounts:access_restricted')}?type={school_block}&role={getattr(user, 'role', '') or ''}&login_type={login_type}"
                )
            login(request, user)
            # Clear setup-warning flags so fresh messages can appear if needed
            for key in ("invalid_setup_shown", "fee_not_available_shown", "school_soft_inactive_notice_shown"):
                request.session.pop(key, None)

            remember = request.POST.get("remember_me")
            if not remember:
                request.session.set_expiry(0)

            # First-login: force password change before accessing dashboard
            if getattr(user, "is_first_login", False):
                return redirect("accounts:change_password_first")

            next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)

            role = getattr(user, "role", None)
            if role == "SUPERADMIN":
                target = reverse("core:super_admin:control_center")
            elif role == "ADMIN":
                target = reverse("core:admin_dashboard")       # /school/dashboard/
            elif role == "TEACHER":
                target = reverse("core:teacher_dashboard")     # /teacher/dashboard/
            elif role == "STUDENT":
                target = reverse("core:student_dashboard")     # /student/dashboard/
            elif role == "PARENT":
                target = reverse("core:parent_dashboard")      # /parent/dashboard/
            else:
                target = reverse("core:student_dashboard")

            return redirect(target)
    else:
        form = AuthenticationForm(request)

    return render(
        request,
        "accounts/login.html",
        {
            "form": form,
            "login_type": login_type,
            "next": request.GET.get("next", "") or "",
            # POST must hit the same URL the user loaded (login vs portal-login vs school-login).
            "login_form_action": request.path,
        },
    )


def access_restricted(request):
    """
    Full-screen blocked access screen for inactive teacher/student.
    This is intentionally generic and does not confirm identity beyond the message.
    """
    login_type = (request.GET.get("login_type") or "portal").strip().lower()
    role = (request.GET.get("role") or "").strip().upper()
    blocked_type = (request.GET.get("type") or "inactive").strip().lower()

    # Optional contact info (if user is logged in somehow, use their school; else best-effort from session)
    school = getattr(getattr(request, "user", None), "school", None) if getattr(request, "user", None) and request.user.is_authenticated else None
    if login_type == "school":
        return_to = reverse("accounts:school_login")
    else:
        return_to = reverse("accounts:portal_login")

    return render(
        request,
        "accounts/access_restricted.html",
        {
            "blocked_type": blocked_type,
            "blocked_role": role,
            "school": school,
            "return_to_login_url": return_to,
        },
    )
