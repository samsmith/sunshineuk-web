"""
Context processors for Sunshine UK
"""
from django.conf import settings

# Only these settings are exposed to templates. Never expose the whole of
# settings: it contains SECRET_KEY, database credentials and API keys.
EXPOSED_SETTINGS = ['CONTACT_EMAIL', 'DEFAULT_DOMAIN', 'DEFAULT_FROM_EMAIL']


def settings_processor(request):
    return {name: getattr(settings, name) for name in EXPOSED_SETTINGS}
