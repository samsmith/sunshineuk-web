# Django settings for whopays project.
#
# Configuration comes from environment variables. For local development you
# can instead override anything in whopays/local_settings.py, which is not
# committed.
import os

import dj_database_url

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEBUG = os.environ.get('DEBUG', '') == '1'

ADMINS = (
    ('Support', 'support@openhealthcare.org.uk'),
)

MANAGERS = ADMINS

DATABASES = {
    'default': dj_database_url.config(
        default='sqlite:///whopays.sqlite',
        conn_max_age=600,
        conn_health_checks=True,
    )
}

ALLOWED_HOSTS = [
    'localhost',
    '.herokuapp.com',
    '.sunshineuk.org',
    '.whopaysthisdoctor.org',
]

# UK time, for dates shown on the site and in the admin. Declarations are
# dated in UK time too (doctors/views.py). The database stores UTC.
TIME_ZONE = 'Europe/London'
LANGUAGE_CODE = 'en-us'

SITE_ID = 1

USE_I18N = True
USE_TZ = True

# Existing tables use 32 bit AutoField primary keys. Keep it that way: moving
# to BigAutoField would need a migration for every table.
DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'

# Absolute filesystem path to the directory that will hold user-uploaded files.
MEDIA_ROOT = ''

# URL that handles the media served from MEDIA_ROOT.
MEDIA_URL = ''

# `collectstatic` gathers static files here (git-ignored); WhiteNoise serves
# them. Heroku runs collectstatic automatically on deploy.
STATIC_ROOT = os.path.join(ROOT, 'static')
STATIC_URL = '/static/'

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}

# Set the SECRET_KEY environment variable (or define SECRET_KEY in
# whopays/local_settings.py for development). The previous key was committed
# to the repository and must be treated as compromised.
SECRET_KEY = os.environ.get('SECRET_KEY', '')

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'whopays.urls'

WSGI_APPLICATION = 'whopays.wsgi.application'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'whopays.context_processors.settings_processor',
            ],
        },
    },
]

INSTALLED_APPS = (
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.sites',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # The admin, with the Sunshine UK dashboard as its home page.
    'doctors.admin_apps.SunshineAdminConfig',
    'reversion',
    'markdown_deux',
    'whopays',
    'doctors'
)

# Log to stdout (Heroku collects it), and email ADMINS about server errors
# (only when DEBUG is off).
V_FORMAT = '%(asctime)s %(process)d %(thread)d %(filename)s %(funcName)s \
%(levelname)s %(message)s'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'require_debug_false': {
            '()': 'django.utils.log.RequireDebugFalse'
        }
    },
    'formatters': {
        'verbose': {
            'format': V_FORMAT
        }
    },
    'handlers': {
        'console_detailed': {
            'level': 'INFO',
            'filters': [],
            'class': 'logging.StreamHandler',
            'formatter': 'verbose'
        },
        'mail_admins': {
            'level': 'ERROR',
            'filters': ['require_debug_false'],
            'class': 'django.utils.log.AdminEmailHandler'
        },
    },
    'loggers': {
        'django.request': {
            'handlers': ['console_detailed', 'mail_admins'],
            'level': 'INFO',
            'propagate': True,
        },
        # Bots send junk Host headers all day: log them, don't email them.
        'django.security.DisallowedHost': {
            'handlers': ['console_detailed'],
            'propagate': False,
        },
    }
}

# Honor the 'X-Forwarded-Proto' header for request.is_secure()
# (Heroku requirement)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
CSRF_COOKIE_NAME = 'XSRF-TOKEN'

# Set FORCE_HTTPS=1 in production: redirects http to https and marks the
# session and CSRF cookies secure-only. It is opt-in so that local development
# and the test suite (plain http) keep working.
FORCE_HTTPS = os.environ.get('FORCE_HTTPS', '') == '1'
SECURE_SSL_REDIRECT = FORCE_HTTPS
SESSION_COOKIE_SECURE = FORCE_HTTPS
CSRF_COOKIE_SECURE = FORCE_HTTPS
# HSTS is hard to undo, so start small: e.g. SECURE_HSTS_SECONDS=3600, then
# raise it once you are sure every subdomain serves https.
SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', '0'))

DEFAULT_FROM_EMAIL = 'hello@sunshineuk.org'
CONTACT_EMAIL = DEFAULT_FROM_EMAIL
DEFAULT_DOMAIN = os.environ.get('DEFAULT_DOMAIN', 'www.sunshineuk.org')

EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')

SENDGRID_API_KEY = os.getenv('SENDGRID_API_KEY')
EMAIL_HOST = 'smtp.sendgrid.net'
EMAIL_HOST_USER = 'apikey'  # this is exactly the value 'apikey'
EMAIL_HOST_PASSWORD = SENDGRID_API_KEY
EMAIL_PORT = 587
EMAIL_USE_TLS = True
# Don't let a slow mail server hang a web request (the default is no timeout).
EMAIL_TIMEOUT = 10

SKIP_EMAIL_VERIFICATION = False

NHS_EMAIL_SUFFIXES = ['nhs.net', 'nhs.uk', 'nhs.scot', 'hscni.net', 'ac.uk', 'doctors.org.uk', 'doctors.net.uk', 'cochrane.org']
ADMIN_SUFFIXES = ['openhealthcare.org.uk', 'deadpansincerity.com', 'msmith.net']
ALL_SUFFIXES = NHS_EMAIL_SUFFIXES + ADMIN_SUFFIXES

try:
    from whopays.local_settings import *  # noqa: F401,F403
except ImportError:
    pass

if not SECRET_KEY:
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured(
        'SECRET_KEY is not set: set the SECRET_KEY environment variable.'
    )
