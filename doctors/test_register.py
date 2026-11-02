"""
Tests for the register around the declaration form: asking for a link, which
links open the form, the list of doctors and its shuffle, doctors' pages,
search, dates and the admin. Submitting the form is tested in doctors/tests.py.
"""
import itertools
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.contrib.staticfiles import finders
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from reversion.models import Version

from doctors import convert, models

# The manifest static storage used in production needs `collectstatic` to have
# run before templates can use {% static %}; use plain storage in tests.
PLAIN_STATIC = override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
})

_gmc_numbers = itertools.count(9000001)


def make_doctor(**fields):
    """
    A doctor with sensible defaults and a unique GMC number.
    """
    number = next(_gmc_numbers)
    data = {
        "name": f"Dr Number {number}",
        "gmc_number": str(number),
        "job_title": "Consultant",
        "primary_employer": "An NHS Trust",
        "employment_address": "",
    }
    data.update(fields)
    return models.Doctor.objects.create(**data)


def no_email(cls):
    """
    Class decorator: the methods that send email do nothing during these
    tests, so no test depends on email.
    """
    cls = mock.patch.object(models.DeclarationLink, "send", lambda self: None)(cls)
    return mock.patch.object(
        models.Doctor, "send_declaration_thanks", lambda self: None
    )(cls)


class LinkModelTest(TestCase):
    def test_keys_are_safe_in_urls(self):
        for _ in range(50):
            self.assertRegex(models.random_token(), r"^[A-Za-z0-9_-]+$")

    def test_new_links_last_two_weeks(self):
        link = models.DeclarationLink.objects.create(email="a@nhs.net")
        self.assertFalse(link.expired)
        self.assertAlmostEqual(
            link.expires, timezone.now() + timedelta(days=14),
            delta=timedelta(minutes=5),
        )

    def test_expired_link_can_be_given_another_day(self):
        link = models.DeclarationLink.objects.create(
            email="a@nhs.net", expires=timezone.now() - timedelta(minutes=1)
        )
        self.assertTrue(link.expired)
        link.expire_tomorrow()
        link.refresh_from_db()
        self.assertFalse(link.expired)
        self.assertAlmostEqual(
            link.expires, timezone.now() + timedelta(days=1),
            delta=timedelta(minutes=5),
        )


class DatesTest(TestCase):
    def test_default_dates_are_timezone_aware(self):
        doctor = make_doctor()
        self.assertTrue(timezone.is_aware(models.in_one_day()))
        self.assertTrue(timezone.is_aware(models.in_two_weeks()))
        self.assertTrue(timezone.is_aware(
            models.Declaration.objects.create(doctor=doctor).dt_created
        ))
        self.assertTrue(timezone.is_aware(
            models.DeclarationV3.objects.create(doctor=doctor, markdown="x").timestamp
        ))


@no_email
@PLAIN_STATIC
class IdentityViewTest(TestCase):
    """
    Asking for a link to declare, on the Declare page.
    """

    def setUp(self):
        self.url = reverse("establish-identity")

    def test_page(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_nhs_address_gets_a_link(self):
        response = self.client.post(self.url, {"email": "Someone@NHS.net"})
        self.assertRedirects(
            response, reverse("identity-pending"), fetch_redirect_response=False
        )
        link = models.DeclarationLink.objects.get()
        self.assertEqual(link.email, "someone@nhs.net")
        self.assertFalse(link.expired)

    def test_other_addresses_are_refused(self):
        response = self.client.post(self.url, {"email": "someone@gmail.com"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)
        self.assertFalse(models.DeclarationLink.objects.exists())

    def test_asking_again_replaces_the_key(self):
        link = models.DeclarationLink.objects.create(email="someone@nhs.net")
        old_key, old_expiry = link.key, link.expires
        self.client.post(self.url, {"email": "someone@nhs.net"})
        link.refresh_from_db()
        self.assertNotEqual(link.key, old_key)
        self.assertEqual(link.expires, old_expiry)
        self.assertEqual(models.DeclarationLink.objects.count(), 1)

    def test_asking_again_after_expiry_gives_another_day(self):
        link = models.DeclarationLink.objects.create(
            email="someone@nhs.net", expires=timezone.now() - timedelta(days=1)
        )
        self.client.post(self.url, {"email": "someone@nhs.net"})
        link.refresh_from_db()
        self.assertFalse(link.expired)
        self.assertAlmostEqual(
            link.expires, timezone.now() + timedelta(days=1),
            delta=timedelta(minutes=5),
        )


@no_email
@PLAIN_STATIC
class ReEstablishIdentityTest(TestCase):
    """
    Asking for a link from a doctor's page ("Add a declaration").
    """

    def setUp(self):
        self.doctor = make_doctor(email="doc@nhs.net")
        self.url = reverse("re-establish-identity", kwargs={"pk": self.doctor.pk})

    def post(self, email, gmc=None):
        return self.client.post(
            self.url, {"email": email, "gmc": gmc or self.doctor.gmc_number}
        )

    def test_page_carries_the_doctors_gmc_number(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].initial["gmc"], self.doctor.gmc_number)

    def test_unknown_doctor_is_404(self):
        url = reverse("re-establish-identity", kwargs={"pk": 999999})
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_same_address_in_any_case_gets_a_link(self):
        response = self.post("Doc@NHS.net")
        self.assertRedirects(
            response, reverse("re-establish-identity-pending"),
            fetch_redirect_response=False,
        )
        self.assertTrue(models.DeclarationLink.objects.filter(email="doc@nhs.net").exists())

    def test_different_address_is_refused(self):
        response = self.post("someone.else@nhs.net")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertFalse(models.DeclarationLink.objects.exists())

    def test_unknown_gmc_number_is_refused_not_a_crash(self):
        response = self.post("doc@nhs.net", gmc="0000000")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertFalse(models.DeclarationLink.objects.exists())


@no_email
@PLAIN_STATIC
class LinkRulesTest(TestCase):
    """
    Which links open the declaration form.
    """

    def setUp(self):
        self.link = models.DeclarationLink.objects.create(email="new@nhs.net")
        self.url = reverse("declare", kwargs={"key": self.link.key})

    def test_unknown_key_is_404(self):
        doctor = make_doctor(email="new@nhs.net")
        for url in (
            reverse("declare", kwargs={"key": "no-such-key"}),
            reverse("add", kwargs={"pk": doctor.pk, "key": "no-such-key"}),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_expired_link_is_404(self):
        self.link.expires = timezone.now() - timedelta(minutes=1)
        self.link.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_short_keys_from_before_the_upgrade_still_work(self):
        models.DeclarationLink.objects.create(email="old@nhs.net", key="a1b2c3d4")
        response = self.client.get(reverse("declare", kwargs={"key": "a1b2c3d4"}))
        self.assertEqual(response.status_code, 200)

    def test_invalid_details_save_nothing_and_keep_the_link(self):
        response = self.client.post(self.url, {
            "name": "",
            "gmc_number": "7654321",
            "job_title": "Consultant",
            "primary_employer": "An NHS Trust",
            "declaration": "## Declaration\n\nNothing to declare.",
            "declaration_records": '[{"nothing_to_declare": true}]',
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("name", response.context["form"].errors)
        self.assertFalse(models.Doctor.objects.exists())
        self.assertTrue(models.DeclarationLink.objects.filter(pk=self.link.pk).exists())


@PLAIN_STATIC
class DoctorListTest(TestCase):
    """
    The register, /doctors/.
    """

    def listed(self):
        return list(self.client.get(reverse("doctor-list")).context["doctors"])

    def test_doctors_without_declarations_are_listed(self):
        doctor = make_doctor()
        self.assertEqual(self.listed(), [doctor])

    def test_new_declarations_wait_an_hour(self):
        doctor = make_doctor()
        declaration = models.DeclarationV3.objects.create(doctor=doctor, markdown="## Declaration")
        self.assertEqual(self.listed(), [])
        models.DeclarationV3.objects.filter(pk=declaration.pk).update(
            timestamp=timezone.now() - timedelta(hours=2)
        )
        self.assertEqual(self.listed(), [doctor])

    def test_each_doctor_listed_once_whatever_their_declarations(self):
        doctor = make_doctor()
        for year in (2022, 2023, 2024):
            models.DetailedDeclaration.objects.create(doctor=doctor, for_year=year)
        for _ in range(2):
            models.DeclarationV3.objects.create(doctor=doctor, markdown="## Declaration")
        two_days_ago = timezone.now() - timedelta(days=2)
        models.DetailedDeclaration.objects.update(dt_created=two_days_ago)
        models.DeclarationV3.objects.update(timestamp=two_days_ago)
        self.assertEqual(self.listed(), [doctor])

    def test_name_order(self):
        for name in ("Zara Zhou", "Amara Adams", "Ben Begum"):
            make_doctor(name=name)
        self.assertEqual(
            [doctor.name for doctor in self.listed()],
            ["Amara Adams", "Ben Begum", "Zara Zhou"],
        )


@PLAIN_STATIC
class DoctorListShuffleTest(TestCase):
    """
    The browser shows the register in a random order; the HTML itself must be
    the same on every request, in name order.
    """

    def test_same_names_come_out_in_id_order(self):
        # Inserted in reverse id order; they must still come out by id.
        doctors = [make_doctor(name="Sam Patel", pk=pk) for pk in (300, 200, 100)]
        listed = list(self.client.get(reverse("doctor-list")).context["doctors"])
        self.assertEqual(listed, sorted(doctors, key=lambda doctor: doctor.pk))

    def test_html_is_the_same_on_every_request(self):
        for name in ("Zara Zhou", "Sam Patel", "Amara Adams", "Sam Patel"):
            make_doctor(name=name)
        pages = {self.client.get(reverse("doctor-list")).content for _ in range(3)}
        self.assertEqual(len(pages), 1)

    def test_rows_hidden_before_the_table_and_shuffled_after_it(self):
        make_doctor()
        make_doctor()
        html = self.client.get(reverse("doctor-list")).content.decode()
        table = html.index('id="doctor-table"')
        after_table = html[html.index("</table>", table):]
        self.assertLess(html.index("classList.add('shuffling-doctors')"), table)
        self.assertIn("Math.random", after_table)
        self.assertIn("classList.remove('shuffling-doctors')", after_table)

    def test_stylesheet_hides_rows_while_shuffling(self):
        with open(finders.find("css/wp.css")) as stylesheet:
            css = stylesheet.read()
        self.assertRegex(
            css, r"\.shuffling-doctors #doctor-table tr\.doctor\s*\{\s*visibility:\s*hidden;"
        )


@PLAIN_STATIC
class DoctorPageTest(TestCase):
    def setUp(self):
        self.doctor = make_doctor()

    def test_page_and_unknown_doctor(self):
        self.assertEqual(self.client.get(self.doctor.get_absolute_url()).status_code, 200)
        url = reverse("doctor-detail", kwargs={"pk": 999999})
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_json_endpoint_is_gone(self):
        response = self.client.get(f"/doctor/{self.doctor.pk}.json")
        self.assertEqual(response.status_code, 404)

    def test_declarations_newest_first(self):
        now = timezone.now()
        for days, text in ((400, "Oldest"), (0, "Newest"), (30, "Middle")):
            models.DeclarationV3.objects.create(
                doctor=self.doctor, timestamp=now - timedelta(days=days),
                markdown=f"## Declaration\n\n{text} declaration.",
            )
        html = self.client.get(self.doctor.get_absolute_url()).content.decode()
        positions = [html.index(f"{text} declaration.") for text in ("Newest", "Middle", "Oldest")]
        self.assertEqual(positions, sorted(positions))

    def test_what_doctors_type_is_shown_as_text_not_html(self):
        attack = "<script>alert(1)</script><img src=x onerror=alert(2)>"
        doctor = make_doctor(name=attack)
        declaration = models.DetailedDeclaration.objects.create(
            doctor=doctor, for_year=2024, consultancy_details=attack
        )
        models.WorkDetails.objects.create(
            declaration=declaration, category="NHS", institution=attack
        )
        convert.convert_old_declarations()  # old declarations show once converted
        make_doctor(name=attack)  # no declarations, so on the register straight away
        for url in (doctor.get_absolute_url(), reverse("doctor-list")):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertNotContains(response, "<script>alert(1)")
                self.assertNotContains(response, "<img src=x")
                self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")


@PLAIN_STATIC
class SearchTest(TestCase):
    def setUp(self):
        # Made with the old form, then converted: search looks at the markdown.
        leeds = models.DetailedDeclaration.objects.create(
            doctor=make_doctor(name="Amara Adams", primary_employer="Leeds Trust"),
            for_year=2024, consultancy_details="Advisory board for Acme Pharma",
        )
        oxford = models.DetailedDeclaration.objects.create(
            doctor=make_doctor(
                name="Ben Begum", primary_employer="Oxford University", gmc_number="1234500"
            ),
            for_year=2024, sponsored_details="Conference in Rome",
        )
        for place in ("Wellcome Centre", "Wellcome Clinic"):
            models.WorkDetails.objects.create(
                declaration=oxford, category="Academic", institution=place
            )
        convert.convert_old_declarations()
        converted = lambda old: models.DeclarationV3.objects.get(
            migrated_from=f"detaileddeclaration:{old.pk}"
        )
        self.leeds, self.oxford = converted(leeds), converted(oxford)

    def search(self, query):
        response = self.client.get(reverse("search"), {"q": query})
        self.assertEqual(response.status_code, 200)
        return list(response.context["object_list"])

    def test_finds_declarations_by_details_doctor_and_position(self):
        for query, expected in (
            ("acme", [self.leeds]),
            ("BEGUM", [self.oxford]),
            ("leeds trust", [self.leeds]),
            ("1234500", [self.oxford]),
            ("rome", [self.oxford]),
            ("nothing like this", []),
        ):
            with self.subTest(query=query):
                self.assertEqual(self.search(query), expected)

    def test_each_declaration_appears_once(self):
        self.assertEqual(self.search("wellcome"), [self.oxford])

    def test_search_term_is_shown_as_text_not_html(self):
        response = self.client.get(reverse("search"), {"q": "<script>alert(1)</script>"})
        self.assertNotContains(response, "<script>alert(1)")


@PLAIN_STATIC
class AdminTest(TestCase):
    """
    The admin and its edit history (django-reversion) after the Django upgrade.
    """

    def setUp(self):
        self.client.force_login(
            User.objects.create_superuser("admin", "admin@example.com", "password")
        )
        self.doctor = make_doctor()
        self.detailed = models.DetailedDeclaration.objects.create(
            doctor=self.doctor, for_year=2024
        )
        models.WorkDetails.objects.create(declaration=self.detailed, category="NHS")
        self.legacy = models.Declaration.objects.create(doctor=self.doctor)
        models.PharmaBenefit.objects.create(
            doctor=self.doctor, declaration=self.legacy,
            company="Acme", reason="Talk", band=1,
        )
        self.v3 = models.DeclarationV3.objects.create(
            doctor=self.doctor, markdown="## Declaration"
        )
        self.link = models.DeclarationLink.objects.create(email="a@nhs.net")

    def test_admin_pages(self):
        urls = [
            reverse(f"admin:doctors_{model}_changelist")
            for model in (
                "doctor", "detaileddeclaration", "declaration", "declarationv3",
                "declarationlink",
            )
        ] + [
            reverse("admin:doctors_doctor_add"),
            reverse("admin:doctors_doctor_change", args=[self.doctor.pk]),
            reverse("admin:doctors_detaileddeclaration_change", args=[self.detailed.pk]),
            reverse("admin:doctors_declaration_change", args=[self.legacy.pk]),
            reverse("admin:doctors_declarationv3_change", args=[self.v3.pk]),
            reverse("admin:doctors_declarationlink_change", args=[self.link.pk]),
            reverse("admin:doctors_doctor_history", args=[self.doctor.pk]),
            reverse("admin:doctors_doctor_recoverlist"),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_editing_a_doctor_keeps_a_version(self):
        url = reverse("admin:doctors_doctor_change", args=[self.doctor.pk])
        response = self.client.post(url, {
            "name": "Renamed",
            "gmc_number": self.doctor.gmc_number,
            "job_title": "Consultant",
            "primary_employer": "An NHS Trust",
            "employment_address": "1 Hospital Road",
            "email": "",
        })
        self.assertEqual(response.status_code, 302)
        self.doctor.refresh_from_db()
        self.assertEqual(self.doctor.name, "Renamed")
        self.assertEqual(Version.objects.get_for_object(self.doctor).count(), 1)

    def test_convert_old_declarations_button(self):
        changelist = reverse("admin:doctors_declarationv3_changelist")
        convert_url = reverse("admin:doctors_declarationv3_convert_old")
        page = self.client.get(changelist)
        self.assertContains(page, "2 declarations")
        self.assertContains(page, 'value="Convert old declarations"')
        self.assertEqual(self.client.get(convert_url).status_code, 405)  # POST only

        response = self.client.post(convert_url, follow=True)
        self.assertRedirects(response, changelist)
        self.assertContains(response, "Converted 2 old declarations to markdown.")
        self.assertNotContains(response, 'value="Convert old declarations"')
        self.assertEqual(
            set(models.DeclarationV3.objects.values_list("migrated_from", flat=True)),
            {"", f"detaileddeclaration:{self.detailed.pk}", f"declaration:{self.legacy.pk}"},
        )
        # Pressing it again converts nothing new.
        self.client.post(convert_url)
        self.assertEqual(models.DeclarationV3.objects.count(), 3)

    def test_convert_button_needs_permission_to_add(self):
        staff = User.objects.create_user("staff", password="password", is_staff=True)
        self.client.force_login(staff)
        response = self.client.post(reverse("admin:doctors_declarationv3_convert_old"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(models.DeclarationV3.objects.count(), 1)

    def test_admin_needs_a_login(self):
        self.client.logout()
        response = self.client.get(reverse("admin:doctors_doctor_changelist"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(reverse("admin:login")))


class CatchAllCategoryTest(TestCase):
    """
    The declaration form keeps the original form's catch-all, "Other
    declarations": the last category in declare.js, free text only.
    """

    def test_other_declarations_is_offered_last_as_free_text(self):
        with open(finders.find("declare.js"), encoding="utf-8") as script:
            js = script.read()
        categories = js[js.index("const CATEGORIES = {"):js.index("const state = {")]
        catch_all = categories[categories.index("other_declarations: {"):]
        self.assertIn('label: "Other declarations"', catch_all)
        self.assertIn('kind: "free_text"', catch_all)
        # Its closing brace is the last one before the list ends.
        self.assertRegex(catch_all, r"^other_declarations: \{[^{}]*\}\s*\};")
