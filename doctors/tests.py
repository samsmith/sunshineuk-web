from datetime import timedelta

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

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
    section 1 fields, and section 2 as declare.js sends it.
    """
    data = {
        "name": "Dr Example",
        "gmc_number": "7654321",
        "job_title": "Consultant",
        "primary_employer": "An NHS Trust",
        "declaration": "## Declaration on 1 November 2026\r\n\r\nNothing to declare.",
        "declaration_records": '[{"nothing_to_declare": true}]',
    }
    data.update(fields)
    return data


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

    def test_saved_to_declaration_v3_with_a_timestamp(self):
        self.assertEqual(models.DeclarationV3._meta.db_table, "declaration_v3")
        before = timezone.now()
        response = self.client.post(self.url, declaration_post_data())

        doctor = models.Doctor.objects.get(gmc_number="7654321")
        self.assertRedirects(response, doctor.get_absolute_url(),
                             fetch_redirect_response=False)
        declaration = doctor.declarationv3_set.get()
        # Stored with "\n" line endings, not the browser's "\r\n".
        self.assertEqual(declaration.markdown,
                         "## Declaration on 1 November 2026\n\nNothing to declare.")
        self.assertGreaterEqual(declaration.timestamp, before)
        self.assertFalse(models.DeclarationLink.objects.filter(pk=self.link.pk).exists())

    def test_missing_declaration_sends_the_form_back(self):
        response = self.client.post(self.url, declaration_post_data(declaration="  "))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "come through. Please add them again.")
        self.assertFalse(models.Doctor.objects.exists())
        self.assertTrue(models.DeclarationLink.objects.filter(pk=self.link.pk).exists())

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


class DoctorJSONTest(TestCase):
    def test_json_view(self):
        doctor = models.Doctor.objects.create(
            name="Dr J", gmc_number="2222222", job_title="x",
            primary_employer="y", employment_address="z",
        )
        response = self.client.get(
            reverse("doctor-json", kwargs={"pk": doctor.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json()["name"], "Dr J")
        self.assertNotIn("email", response.json())


class ContextProcessorTest(TestCase):
    def test_secrets_are_not_exposed_to_templates(self):
        exposed = settings_processor(None)
        for name in ("SECRET_KEY", "DATABASES", "SENDGRID_API_KEY",
                     "EMAIL_HOST_PASSWORD"):
            self.assertNotIn(name, exposed)
        self.assertEqual(exposed["DEFAULT_DOMAIN"], settings.DEFAULT_DOMAIN)
