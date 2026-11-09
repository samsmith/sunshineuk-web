"""
Site-wide tests: settings from the environment, HTTPS and hosts, static files,
the bootstrap form filter, migrations, the Heroku files and the plain pages.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import skipIf

from django import forms
from django.conf import settings
from django.core.management import call_command
from django.template import Context, Template
from django.test import (
    Client, RequestFactory, SimpleTestCase, TestCase, override_settings,
)
from django.urls import reverse
from django.views import defaults

from doctors.test_register import PLAIN_STATIC

ROOT = Path(settings.ROOT)

# The config vars settings.py reads. They're cleared before each check below,
# so a developer's or CI's own values can't leak in.
SETTINGS_ENV_VARS = (
    "SECRET_KEY", "DEBUG", "FORCE_HTTPS", "SECURE_HSTS_SECONDS", "DATABASE_URL",
)
HTTPS_SETTINGS = [
    "SECURE_SSL_REDIRECT", "SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE",
    "SECURE_HSTS_SECONDS",
]


@skipIf(
    (ROOT / "whopays" / "local_settings.py").exists(),
    "whopays/local_settings.py overrides settings",
)
class SettingsFromEnvironmentTest(SimpleTestCase):
    """
    Heroku config vars -> settings. Each check imports the settings in a fresh
    Python process with only the given variables set.
    """

    def run_settings(self, names, **env):
        environ = {k: v for k, v in os.environ.items() if k not in SETTINGS_ENV_VARS}
        environ.update(DJANGO_SETTINGS_MODULE="whopays.settings", **env)
        code = (
            "import json\n"
            "from django.conf import settings\n"
            f"print(json.dumps({{n: getattr(settings, n) for n in {list(names)!r}}},"
            " default=str))"
        )
        return subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, env=environ,
            capture_output=True, text=True, timeout=60,
        )

    def load(self, names, **env):
        result = self.run_settings(names, SECRET_KEY="test", **env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_refuses_to_start_without_a_secret_key(self):
        result = self.run_settings(["DEBUG"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SECRET_KEY is not set", result.stderr)

    def test_defaults_are_safe(self):
        self.assertEqual(self.load(["DEBUG"] + HTTPS_SETTINGS), {
            "DEBUG": False,
            "SECURE_SSL_REDIRECT": False,
            "SESSION_COOKIE_SECURE": False,
            "CSRF_COOKIE_SECURE": False,
            "SECURE_HSTS_SECONDS": 0,
        })

    def test_force_https_and_hsts(self):
        values = self.load(HTTPS_SETTINGS, FORCE_HTTPS="1", SECURE_HSTS_SECONDS="3600")
        self.assertEqual(values, {
            "SECURE_SSL_REDIRECT": True,
            "SESSION_COOKIE_SECURE": True,
            "CSRF_COOKIE_SECURE": True,
            "SECURE_HSTS_SECONDS": 3600,
        })

    def test_debug(self):
        self.assertIs(self.load(["DEBUG"], DEBUG="1")["DEBUG"], True)

    def test_database_url(self):
        database = self.load(
            ["DATABASES"], DATABASE_URL="postgres://user:pw@db.example.com:5432/whopays"
        )["DATABASES"]["default"]
        self.assertEqual(database["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(
            (database["NAME"], database["HOST"], database["PORT"]),
            ("whopays", "db.example.com", 5432),
        )
        self.assertEqual(database["CONN_MAX_AGE"], 600)
        self.assertIs(database["CONN_HEALTH_CHECKS"], True)


@PLAIN_STATIC
class HttpsTest(TestCase):
    """
    What FORCE_HTTPS and SECURE_HSTS_SECONDS switch on, behind Heroku's router
    (which passes on the original scheme in X-Forwarded-Proto).
    """

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_http_is_redirected_to_https(self):
        response = self.client.get("/about")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "https://testserver/about")

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_https_through_herokus_router_is_served(self):
        response = self.client.get("/about", HTTP_X_FORWARDED_PROTO="https")
        self.assertEqual(response.status_code, 200)

    @override_settings(SECURE_HSTS_SECONDS=3600)
    def test_hsts_header(self):
        response = self.client.get("/about", HTTP_X_FORWARDED_PROTO="https")
        self.assertEqual(response["Strict-Transport-Security"], "max-age=3600")

    @override_settings(CSRF_COOKIE_SECURE=True)
    def test_secure_cookie(self):
        response = self.client.get(
            reverse("establish-identity"), HTTP_X_FORWARDED_PROTO="https"
        )
        self.assertTrue(response.cookies[settings.CSRF_COOKIE_NAME]["secure"])

    def test_security_headers(self):
        response = self.client.get("/about")
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")


@PLAIN_STATIC
class AllowedHostsTest(TestCase):
    def test_site_domains_are_served(self):
        for host in (
            "www.sunshineuk.org", "sunshineuk.org",
            "www.whopaysthisdoctor.org", "whopays.herokuapp.com",
        ):
            with self.subTest(host=host):
                self.assertEqual(self.client.get("/about", HTTP_HOST=host).status_code, 200)

    def test_other_hosts_get_a_400_and_are_logged(self):
        with self.assertLogs("django.security.DisallowedHost", level="ERROR"):
            response = self.client.get("/about", HTTP_HOST="evil.example")
        self.assertEqual(response.status_code, 400)


class StaticFilesTest(TestCase):
    """
    Heroku runs collectstatic on every build, and WhiteNoise serves the result.
    """

    def test_text_files_are_utf8(self):
        # collectstatic reads CSS and JS as UTF-8 and fails the build if one
        # isn't (sorttable.js used to be Latin-1).
        static = ROOT / "whopays" / "static"
        not_utf8 = []
        for path in sorted(static.rglob("*")):
            if path.suffix in {".css", ".js", ".svg", ".txt"}:
                try:
                    path.read_bytes().decode("utf-8")
                except UnicodeDecodeError:
                    not_utf8.append(str(path.relative_to(static)))
        self.assertEqual(not_utf8, [])

    def test_collectstatic_then_pages_use_and_serve_hashed_files(self):
        with tempfile.TemporaryDirectory() as static_root, \
                override_settings(STATIC_ROOT=static_root):
            call_command("collectstatic", interactive=False, verbosity=0)
            with open(os.path.join(static_root, "staticfiles.json")) as manifest:
                stylesheet = "/static/" + json.load(manifest)["paths"]["css/wp.css"]
            self.assertRegex(stylesheet, r"/static/css/wp\.[0-9a-f]{12}\.css$")
            client = Client()
            for url in ("/", reverse("doctor-list"), reverse("establish-identity")):
                with self.subTest(url=url):
                    self.assertContains(client.get(url), stylesheet)
            response = client.get(stylesheet)
            self.assertEqual(response.status_code, 200)
            response.close()


class SampleForm(forms.Form):
    name = forms.CharField(help_text="As it appears on the register")
    agree = forms.BooleanField(required=False, label="I agree")
    colour = forms.ChoiceField(choices=[("r", "Red"), ("g", "Green")])
    token = forms.CharField(widget=forms.HiddenInput, required=False)

    def clean(self):
        if self.cleaned_data.get("name") == "clash":
            raise forms.ValidationError("Whole-form problem")
        return self.cleaned_data


def render_bootstrap(template, form):
    return Template("{% load bootstrap %}" + template).render(Context({"form": form}))


def opening_tag(html, name):
    return re.search(r'<(?:input|select)\b[^>]*\bname="%s"[^>]*>' % name, html).group(0)


class BootstrapFilterTest(SimpleTestCase):
    """
    {{ form|bootstrap }}, the in-repo replacement for django-bootstrap-form.
    """

    def test_inputs_and_selects_get_form_control(self):
        html = render_bootstrap("{{ form|bootstrap }}", SampleForm())
        self.assertIn("form-control", opening_tag(html, "name"))
        self.assertIn("form-control", opening_tag(html, "colour"))

    def test_checkboxes_are_not_form_controls(self):
        html = render_bootstrap("{{ form|bootstrap }}", SampleForm())
        self.assertNotIn("form-control", opening_tag(html, "agree"))
        self.assertRegex(
            html,
            r'<div class="checkbox">\s*<label\s*>\s*<input[^>]*name="agree"[^>]*>'
            r"\s*<span>I agree</span>",
        )

    def test_labels_help_text_and_hidden_fields(self):
        html = render_bootstrap("{{ form|bootstrap }}", SampleForm())
        self.assertRegex(html, r'<label class="control-label\s*" for="id_name">Name</label>')
        self.assertRegex(html, r'<p class="help-block">\s*As it appears on the register\s*</p>')
        self.assertIn('type="hidden"', opening_tag(html, "token"))
        self.assertEqual(html.count('class="form-group'), 3)  # not the hidden one

    def test_field_errors(self):
        html = render_bootstrap(
            "{{ form|bootstrap }}", SampleForm({"name": "", "colour": "purple"})
        )
        self.assertEqual(html.count("has-error"), 2)
        self.assertIn("This field is required.", html)
        self.assertIn("Select a valid choice.", html)

    def test_whole_form_errors(self):
        html = render_bootstrap(
            "{{ form|bootstrap }}", SampleForm({"name": "clash", "colour": "r"})
        )
        self.assertRegex(html, r'<div class="alert alert-danger">[\s\S]*Whole-form problem')

    def test_one_field_rendered_twice_gets_the_class_once(self):
        html = render_bootstrap(
            "{{ form.name|bootstrap }}{{ form.name|bootstrap }}", SampleForm()
        )
        tags = re.findall(r'<input\b[^>]*\bname="name"[^>]*>', html)
        self.assertEqual(len(tags), 2)
        for tag in tags:
            self.assertEqual(tag.count("form-control"), 1)


class MigrationsTest(TestCase):
    def test_every_model_change_has_a_migration(self):
        try:
            call_command("makemigrations", "--check", "--dry-run", verbosity=0)
        except SystemExit:
            self.fail("A model has changed without a migration: run makemigrations")


class HerokuFilesTest(SimpleTestCase):
    def test_procfile_runs_gunicorn_and_migrates_on_release(self):
        processes = dict(
            line.split(":", 1)
            for line in (ROOT / "Procfile").read_text().splitlines() if line.strip()
        )
        self.assertTrue(processes["web"].strip().startswith("gunicorn whopays.wsgi"))
        self.assertEqual(processes["release"].strip(), "python manage.py migrate --noinput")

    def test_app_json_generates_a_secret_key(self):
        # Review Apps and Heroku CI runs can't start without one.
        app = json.loads((ROOT / "app.json").read_text())
        self.assertEqual(app["env"]["SECRET_KEY"], {"generator": "secret"})

    def test_wsgi_application_loads(self):
        from whopays.wsgi import application
        self.assertTrue(callable(application))


@PLAIN_STATIC
class PagesTest(TestCase):
    def test_plain_pages(self):
        for name in (
            "home", "about", "contact", "identity-pending", "re-establish-identity-pending",
        ):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_unknown_address_gets_the_sites_404_page(self):
        response = self.client.get("/no-such-page")
        self.assertEqual(response.status_code, 404)
        self.assertTemplateUsed(response, "404.html")

    def test_server_error_page(self):
        response = defaults.server_error(RequestFactory().get("/"))
        self.assertEqual(response.status_code, 500)
        self.assertIn("Looks like something went wrong", response.content.decode())

