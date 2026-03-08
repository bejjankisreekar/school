"""
URL configuration for school_erp_demo project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.core import api_views

urlpatterns = [
    path("api/<str:school_code>/students/", api_views.api_students),
    path("api/<str:school_code>/fees/", api_views.api_fees),
    path("api/<str:school_code>/results/", api_views.api_results),
    path("api/<str:school_code>/attendance/", api_views.api_attendance),
    path("api/admin/schools/<str:school_code>/classrooms/", api_views.api_admin_classrooms),
    path("api/admin/schools/<str:school_code>/sections/", api_views.api_admin_sections),
    path("api/admin/schools/by-id/<int:school_id>/classrooms/", api_views.api_admin_classrooms_by_id),
    path("api/admin/schools/by-id/<int:school_id>/sections/", api_views.api_admin_sections_by_id),
    path("", include(("apps.core.urls", "core"), namespace="core")),
    path("", include(("apps.timetable.urls", "timetable"), namespace="timetable")),
    path("accounts/", include(("apps.accounts.urls", "accounts"), namespace="accounts")),
    path("admin/", include(("apps.core.admin_urls", "admin_manage"))),
    path("django-admin/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
