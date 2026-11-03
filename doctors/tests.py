import datetime as dt
import json
import re
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from doctors import forms, models
from whopays.context_processors import settings_processor


# The manifest static storage used in production needs `collectstatic` to have
# run before templates can use {% static %}; use plain storage in tests.
PLAIN_STATIC = override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
})


def declaration_post_data(**fields):
    """
    A valid POST body for the declare / add-declaration views: the four
    section 1 fields, and section 2 as declare.js sends it (the server adds
    the dated heading).
    """
    data = {
        "name": "Dr Example",
        "gmc_number": "7654321",
        "job_title": "Consultant",
        "primary_employer": "An NHS Trust",
        "declaration": "Nothing to declare.",
        "declaration_records": '[{"nothing_to_declare": true}]',
    }
    data.update(fields)
    return data


def posted_records(response):
    """
    The declarations a form sent back with errors hands to declare.js, which
    puts them back in the list.
    """
    script = re.search(
        r'<script id="posted-declaration-records"[^>]*>(.*?)</script>',
        response.content.decode(), re.S,
    )
    return json.loads(json.loads(script.group(1)))


class EmailDomainTest(TestCase):
    def allowed(self, email):
        return forms.email_domain_allowed(email, settings.ALL_SUFFIXES)

    def test_exact_and_subdomain_allowed(self):
        self.assertTrue(self.allowed("a@nhs.net"))
        self.assertTrue(self.allowed("a@trust.nhs.uk"))
        self.assertTrue(self.allowed("a@ox.ac.uk"))
        self.assertTrue(self.allowed("a@doctors.org.uk"))

    def test_case_insensitive(self):
        self.assertTrue(self.allowed("A@NHS.NET"))

    def test_lookalike_domains_rejected(self):
        self.assertFalse(self.allowed("evil@notnhs.net"))
        self.assertFalse(self.allowed("x@evil-ac.uk"))
        self.assertFalse(self.allowed("x@nhs.net.evil.com"))
        self.assertFalse(self.allowed("x@gmail.com"))

    def test_identity_form(self):
        self.assertFalse(forms.IdentityForm({"email": "evil@notnhs.net"}).is_valid())
        form = forms.IdentityForm({"email": "Someone@NHS.net"})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["email"], "someone@nhs.net")


class DeclarationLinkTest(TestCase):
    def test_keys_are_long_and_unique(self):
        keys = {models.random_token() for _ in range(50)}
        self.assertEqual(len(keys), 50)
        self.assertTrue(all(len(k) >= 32 for k in keys))

    def test_new_key_is_long(self):
        link = models.DeclarationLink.objects.create(email="a@nhs.net")
        old = link.key
        link.new_key()
        self.assertNotEqual(link.key, old)
        self.assertGreaterEqual(len(link.key), 32)


@PLAIN_STATIC
class LinkOwnershipTest(TestCase):
    def setUp(self):
        self.victim = models.Doctor.objects.create(
            name="Victim", gmc_number="1111111", job_title="x",
            primary_employer="y", employment_address="z",
            email="victim@nhs.net",
        )
        self.attacker_link = models.DeclarationLink.objects.create(
            email="attacker@nhs.net"
        )
        self.victim_link = models.DeclarationLink.objects.create(
            email="Victim@NHS.net"
        )

    def add_url(self, link):
        return reverse("add", kwargs={"pk": self.victim.pk, "key": link.key})

    def test_other_peoples_link_cannot_open_record(self):
        self.assertEqual(self.client.get(self.add_url(self.attacker_link)).status_code, 404)

    def test_other_peoples_link_cannot_edit_record(self):
        response = self.client.post(
            self.add_url(self.attacker_link),
            declaration_post_data(name="HACKED", gmc_number="1111111"),
        )
        self.assertEqual(response.status_code, 404)
        self.victim.refresh_from_db()
        self.assertEqual(self.victim.name, "Victim")
        self.assertEqual(self.victim.declarationv3_set.count(), 0)

    def test_own_link_works_whatever_the_email_case(self):
        url = self.add_url(self.victim_link)
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(
            url, declaration_post_data(name="Victim", gmc_number="1111111")
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.victim.declarationv3_set.count(), 1)

    def test_employment_address_is_saved_blank(self):
        # No longer on the form: new and returning doctors both get "".
        link = models.DeclarationLink.objects.create(email="new@nhs.net")
        self.client.post(
            reverse("declare", kwargs={"key": link.key}),
            declaration_post_data(employment_address="1 Hospital Road"),
        )
        doctor = models.Doctor.objects.get(gmc_number="7654321")
        self.assertEqual(doctor.employment_address, "")

        self.client.post(
            self.add_url(self.victim_link),
            declaration_post_data(name="Victim", gmc_number="1111111"),
        )
        self.victim.refresh_from_db()
        self.assertEqual(self.victim.employment_address, "")

    def test_declare_redirects_existing_doctor_to_add(self):
        response = self.client.get(
            reverse("declare", kwargs={"key": self.victim_link.key})
        )
        self.assertRedirects(
            response, self.add_url(self.victim_link),
            fetch_redirect_response=False,
        )

    def test_new_doctor_gets_the_links_email_not_the_posted_one(self):
        link = models.DeclarationLink.objects.create(email="new@nhs.net")
        response = self.client.post(
            reverse("declare", kwargs={"key": link.key}),
            declaration_post_data(email="someone-elses@nhs.net"),
        )
        self.assertEqual(response.status_code, 302)
        doctor = models.Doctor.objects.get(gmc_number="7654321")
        self.assertEqual(doctor.email, "new@nhs.net")

    def test_expired_link_is_404(self):
        from datetime import timedelta
        from django.utils import timezone
        self.victim_link.expires = timezone.now() - timedelta(days=1)
        self.victim_link.save()
        self.assertEqual(self.client.get(self.add_url(self.victim_link)).status_code, 404)


@PLAIN_STATIC
class DeclareFormTemplateTest(TestCase):
    def setUp(self):
        self.doctor = models.Doctor.objects.create(
            name="Dr Returning", gmc_number="3333333", job_title="Consultant",
            primary_employer="An NHS Trust", employment_address="z",
            email="returning@nhs.net",
        )
        link = models.DeclarationLink.objects.create(email="returning@nhs.net")
        self.url = reverse("add", kwargs={"pk": self.doctor.pk, "key": link.key})

    def test_section_1_posts_with_the_doctor_form_field_names(self):
        html = self.client.get(self.url).content.decode()
        for name, value in [("name", "Dr Returning"), ("job_title", "Consultant"),
                            ("primary_employer", "An NHS Trust"),
                            ("gmc_number", "3333333")]:
            self.assertRegex(html, rf'name="{name}"[^>]*value="{value}"')
        # Section 2 goes up in new fields, which declare.js fills in.
        self.assertIn('name="declaration"', html)
        self.assertIn('name="declaration_records"', html)

    def test_posted_declarations_come_back_safely_with_errors(self):
        response = self.client.post(self.url, {
            "name": "",
            "job_title": "Consultant",
            "primary_employer": "An NHS Trust",
            "gmc_number": "3333333",
            "declaration": "## Declaration",
            "declaration_records": '[{"details": "</script><script>alert(1)</script>"}]',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="posted-declaration-records"')
        self.assertNotContains(response, "<script>alert(1)</script>")


@PLAIN_STATIC
class DeclarationV3Test(TestCase):
    def setUp(self):
        self.link = models.DeclarationLink.objects.create(email="new@nhs.net")
        self.url = reverse("declare", kwargs={"key": self.link.key})

    def declare_at(self, when, **fields):
        """Make a declaration as if the server's clock said `when`."""
        with mock.patch("django.utils.timezone.now", return_value=when):
            response = self.client.post(self.url, declaration_post_data(**fields))
        self.assertEqual(response.status_code, 302)
        return models.Doctor.objects.get(gmc_number="7654321").declarationv3_set.get()

    def test_saved_to_declaration_v3_with_a_timestamp(self):
        self.assertEqual(models.DeclarationV3._meta.db_table, "declaration_v3")
        before = timezone.now()
        response = self.client.post(self.url, declaration_post_data(
            declaration="### Consultancy\r\n\r\n- **Details:** Acme\r\n- **Details:** Beta",
        ))

        doctor = models.Doctor.objects.get(gmc_number="7654321")
        self.assertRedirects(response, doctor.get_absolute_url(),
                             fetch_redirect_response=False)
        declaration = doctor.declarationv3_set.get()
        # Under the server's dated heading (tested below), and stored with
        # "\n" line endings, not the browser's "\r\n".
        heading, body = declaration.markdown.split("\n\n", 1)
        self.assertRegex(heading, r"^## Declaration on \d{1,2} [A-Z][a-z]+ \d{4}$")
        self.assertEqual(body, "### Consultancy\n\n- **Details:** Acme\n- **Details:** Beta")
        self.assertGreaterEqual(declaration.timestamp, before)
        self.assertFalse(models.DeclarationLink.objects.filter(pk=self.link.pk).exists())

    def test_dated_by_the_server_in_uk_time(self):
        # 23:30 UTC on 14 July is 00:30 on 15 July in London (BST). Any date
        # the browser sent, as declare.js used to, is replaced.
        when = dt.datetime(2026, 7, 14, 23, 30, tzinfo=dt.timezone.utc)
        declaration = self.declare_at(
            when, declaration="## Declaration on 16 July 2026\r\n\r\nNothing to declare."
        )
        self.assertEqual(declaration.timestamp, when)
        self.assertEqual(declaration.markdown,
                         "## Declaration on 15 July 2026\n\nNothing to declare.")

    def test_dated_in_uk_time_in_winter_too(self):
        # GMT: London and UTC agree.
        declaration = self.declare_at(dt.datetime(2026, 1, 14, 23, 30, tzinfo=dt.timezone.utc))
        self.assertEqual(declaration.markdown,
                         "## Declaration on 14 January 2026\n\nNothing to declare.")

    def test_search_results_show_the_same_date(self):
        self.declare_at(dt.datetime(2026, 7, 14, 23, 30, tzinfo=dt.timezone.utc))
        response = self.client.get(reverse("search"), {"q": "Dr Example"})
        self.assertContains(response, "Declaration on 15 July 2026")

    def test_nul_characters_are_dropped(self):
        # Pasted text can carry one, unseen; Postgres can't store it.
        declaration = self.declare_at(
            dt.datetime(2026, 11, 2, 12, tzinfo=dt.timezone.utc),
            declaration="### Other declarations\r\n\r\n- **Details:** Before\x00After",
        )
        self.assertEqual(declaration.markdown, (
            "## Declaration on 2 November 2026\n\n"
            "### Other declarations\n\n- **Details:** BeforeAfter"
        ))

    def test_missing_declaration_sends_the_form_back(self):
        # Nothing at all, or nothing but a heading or a NUL.
        for declaration in ("  ", "## Declaration on 1 November 2026\r\n\r\n", "\x00"):
            with self.subTest(declaration=declaration):
                response = self.client.post(
                    self.url, declaration_post_data(declaration=declaration)
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "come through. Please add them again.")
                self.assertFalse(models.Doctor.objects.exists())
                self.assertTrue(
                    models.DeclarationLink.objects.filter(pk=self.link.pk).exists()
                )

    def test_shown_on_the_register_page_safely(self):
        doctor = models.Doctor.objects.create(
            name="Dr Shown", gmc_number="4444444", job_title="x",
            primary_employer="y", email="shown@nhs.net",
        )
        # As if someone skipped declare.js and posted their own markdown.
        models.DeclarationV3.objects.create(doctor=doctor, markdown=(
            "## Declaration on 1 November 2026\n\n"
            "### Consultancy\n\n"
            "- **Details:** <script>alert(1)</script> [x](javascript:alert(1))"
        ))
        response = self.client.get(doctor.get_absolute_url())
        self.assertContains(response, "<h2>Declaration on 1 November 2026</h2>")
        self.assertContains(response, "<h3>Consultancy</h3>")
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertNotContains(response, 'href="javascript:')

    def test_first_declaration_takes_an_hour_to_reach_the_list(self):
        self.client.post(self.url, declaration_post_data())
        doctor = models.Doctor.objects.get(gmc_number="7654321")
        listed = lambda: self.client.get(reverse("doctor-list")).context["doctors"]
        self.assertNotIn(doctor, listed())
        doctor.declarationv3_set.update(timestamp=timezone.now() - timedelta(hours=2))
        self.assertIn(doctor, listed())

    def test_a_link_works_once(self):
        # Pressing Back after declaring, or a second click getting through:
        # the link has gone, so nothing more is saved.
        self.assertEqual(self.client.post(self.url, declaration_post_data()).status_code, 302)
        self.assertEqual(self.client.post(self.url, declaration_post_data()).status_code, 404)
        self.assertContains(
            self.client.get(self.url), "Your link seems to have expired.", status_code=404
        )
        self.assertEqual(models.DeclarationV3.objects.count(), 1)


@PLAIN_STATIC
class SectionOneTest(TestCase):
    """
    Section 1 of the form, about the doctor: what's saved, and what happens
    when the form comes back with an error.
    """

    def setUp(self):
        self.link = models.DeclarationLink.objects.create(email="new@nhs.net")
        self.url = reverse("declare", kwargs={"key": self.link.key})

    def test_saved_as_typed_less_surrounding_spaces_and_shown_as_text(self):
        typed = {
            "name": "  Dr Siân Ó Dálaigh-Nguyễn <b>Jr</b> & Co  ",
            "job_title": "Consultant \"Locum\" & Lead <script>alert(1)</script>",
            "primary_employer": "Guy's & St Thomas' NHS Foundation Trust",
            "gmc_number": " ORCID 0000-0002-1825-0097 ",
        }
        self.assertEqual(
            self.client.post(self.url, declaration_post_data(**typed)).status_code, 302
        )
        doctor = models.Doctor.objects.get()
        saved = {field: getattr(doctor, field) for field in typed}
        self.assertEqual(saved, {field: value.strip() for field, value in typed.items()})

        # On their page, and on the register once their first declaration has
        # been up an hour.
        doctor.declarationv3_set.update(timestamp=timezone.now() - timedelta(hours=2))
        for url in (doctor.get_absolute_url(), reverse("doctor-list")):
            with self.subTest(url=url):
                response = self.client.get(url)
                for value in saved.values():
                    self.assertContains(response, escape(value))
                self.assertNotContains(response, "<script>alert(1)")
        self.assertContains(
            self.client.get(reverse("doctor-list")), f'href="{doctor.get_absolute_url()}"'
        )

    def test_longest_values_are_kept_whole(self):
        # The boxes take as much as the database does.
        html = self.client.get(self.url).content.decode()
        longest = {}
        for field in ("name", "job_title", "primary_employer", "gmc_number"):
            length = models.Doctor._meta.get_field(field).max_length
            self.assertRegex(html, rf'name="{field}"[^>]*maxlength="{length}"')
            longest[field] = ("Long value " * 20)[:length - 1] + "!"
        details = " ".join(f"Sentence {n} of a long declaration." for n in range(1, 160))
        self.assertGreater(len(details), 5000)

        response = self.client.post(self.url, declaration_post_data(
            **longest, declaration=f"### Other declarations\n\n- **Details:** {details}"
        ))
        self.assertEqual(response.status_code, 302)
        doctor = models.Doctor.objects.get()
        self.assertEqual({field: getattr(doctor, field) for field in longest}, longest)
        page = self.client.get(doctor.get_absolute_url())
        for value in [*longest.values(), details]:
            self.assertContains(page, value)

    def test_gmc_number_already_registered_sends_the_form_back(self):
        models.Doctor.objects.create(
            name="Dr First", gmc_number="7654321", job_title="x",
            primary_employer="y", email="first@nhs.net",
        )
        records = [
            {"category": "main_employment", "category_label": "Main employment",
             "employment_type": "nhs", "details": "Consultant, Royal Free Hospital"},
            {"category": "consultancy", "category_label": "Consultancy",
             "subs": {"pharmaceutical_companies": True, "technology_companies": False,
                      "consultancy_other": True},
             "band": "£2,000 – £5,000", "details": "*stars* & <angle> “curly”\nline two"},
        ]
        response = self.client.post(self.url, declaration_post_data(
            name="Dr Laura Chen", declaration_records=json.dumps(records),
        ))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "GMC Number: Doctor with this GMC Number already exists.")
        # Nothing saved, and the link still works...
        self.assertEqual(models.Doctor.objects.count(), 1)
        self.assertFalse(models.DeclarationV3.objects.exists())
        self.assertTrue(models.DeclarationLink.objects.filter(pk=self.link.pk).exists())
        # ...and everything typed comes back: section 1 in its boxes, and the
        # declarations for declare.js to list again.
        self.assertRegex(response.content.decode(), r'name="name"[^>]*value="Dr Laura Chen"')
        self.assertEqual(posted_records(response), records)

    def test_nul_character_in_a_box_is_an_error_not_a_crash(self):
        response = self.client.post(self.url, declaration_post_data(name="Dr Nul\x00Name"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Name: Null characters are not allowed.")
        self.assertFalse(models.Doctor.objects.exists())
        self.assertTrue(models.DeclarationLink.objects.filter(pk=self.link.pk).exists())


@PLAIN_STATIC
class ReturningDoctorTest(TestCase):
    """
    A doctor on the register makes another declaration, starting from "Add a
    declaration" on their page or from the Declare page.
    """

    def setUp(self):
        link = models.DeclarationLink.objects.create(email="returning@nhs.net")
        self.client.post(reverse("declare", kwargs={"key": link.key}), declaration_post_data(
            name="Dr Returning", gmc_number="5555555",
            declaration="### Consultancy\n\n- **Details:** First declaration",
        ))
        self.doctor = models.Doctor.objects.get()
        self.doctor.declarationv3_set.update(timestamp=timezone.now() - timedelta(days=1))

    def test_from_add_a_declaration_on_their_page(self):
        add_url = reverse("re-establish-identity", kwargs={"pk": self.doctor.pk})
        self.assertContains(self.client.get(self.doctor.get_absolute_url()), f'href="{add_url}"')
        response = self.client.post(add_url, {"email": "Returning@NHS.net", "gmc": "5555555"})
        self.assertRedirects(
            response, reverse("re-establish-identity-pending"), fetch_redirect_response=False
        )
        self.declare_again()

    def test_from_the_declare_page(self):
        response = self.client.post(reverse("establish-identity"), {"email": "returning@nhs.net"})
        self.assertRedirects(response, reverse("identity-pending"), fetch_redirect_response=False)
        self.declare_again()

    def declare_again(self):
        # The new link (from the database, not the email) leads to their
        # record, with section 1 filled in from it.
        key = models.DeclarationLink.objects.get(email="returning@nhs.net").key
        add_url = reverse("add", kwargs={"pk": self.doctor.pk, "key": key})
        self.assertRedirects(
            self.client.get(reverse("declare", kwargs={"key": key})), add_url,
            fetch_redirect_response=False,
        )
        self.assertRegex(
            self.client.get(add_url).content.decode(), r'name="name"[^>]*value="Dr Returning"'
        )

        response = self.client.post(add_url, declaration_post_data(
            name="Dr Returning", gmc_number="5555555", job_title="Clinical Director",
            declaration="### Consultancy\n\n- **Details:** Second declaration",
        ))
        self.assertRedirects(response, self.doctor.get_absolute_url(), fetch_redirect_response=False)
        # Still one doctor, with the new job title and the same email.
        self.assertEqual(models.Doctor.objects.count(), 1)
        self.doctor.refresh_from_db()
        self.assertEqual(
            (self.doctor.job_title, self.doctor.email), ("Clinical Director", "returning@nhs.net")
        )
        # Both declarations on their page, newest first.
        html = self.client.get(self.doctor.get_absolute_url()).content.decode()
        self.assertLess(html.index("Second declaration"), html.index("First declaration"))


class ContextProcessorTest(TestCase):
    def test_secrets_are_not_exposed_to_templates(self):
        exposed = settings_processor(None)
        for name in ("SECRET_KEY", "DATABASES", "SENDGRID_API_KEY",
                     "EMAIL_HOST_PASSWORD"):
            self.assertNotIn(name, exposed)
        self.assertEqual(exposed["DEFAULT_DOMAIN"], settings.DEFAULT_DOMAIN)
