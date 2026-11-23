from django.urls import re_path
from annotations import views

urlpatterns = [
    re_path(r"^api/session/?$", views.session),
    re_path(r"^api/auth/login/?$", views.sign_in),
    re_path(r"^api/auth/register/?$", views.register),
    re_path(r"^api/auth/logout/?$", views.sign_out),
    re_path(r"^api/project/?$", views.project),
    re_path(r"^api/records/(?P<record_id>\d+)/?$", views.record_detail),
    re_path(r"^api/records/(?P<record_id>\d+)/annotation/?$", views.save_annotation),
    re_path(r"^api/workbook/sync/?$", views.sync_workbook),
    re_path(r"^api/export\.xlsx$", views.download_workbook),
    re_path(r"^api/health/?$", views.health),
]

