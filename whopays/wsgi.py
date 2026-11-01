"""
WSGI config for whopays project.

Static files are served by WhiteNoise (see MIDDLEWARE in settings).
"""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "whopays.settings")

application = get_wsgi_application()
