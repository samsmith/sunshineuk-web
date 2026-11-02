"""
Use the Sunshine UK admin site (doctors/dashboard.py) as the admin.

Kept apart from dashboard.py so that INSTALLED_APPS can load it before any
models are ready.
"""
from django.contrib.admin.apps import AdminConfig


class SunshineAdminConfig(AdminConfig):
    default_site = "doctors.dashboard.SunshineAdminSite"
