"""
Tests for the admin dashboard (doctors/dashboard.py): making links, the list
of unused links, the latest declarations and finding/deleting doctors.
"""
import datetime as dt
import itertools
from unittest import mock

from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.auth.models import Permission, User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from doctors import dashboard, models

PLAIN_STATIC = override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
_gmc_numbers = itertools.count(7700001)


def make_doctor(**fields):
    number = next(_gmc_numbers)
    data = dict(name=f"Dr {number}", gmc_number=str(number), job_title="Consultant",
                primary_employer="An NHS Trust", employment_address="")
    data.update(fields)
    return models.Doctor.objects.create(**data)


class MonthsFromTest(TestCase):
    def test_calendar_months(self):
        when = dt.datetime(2026, 11, 2, 10, tzinfo=dt.timezone.utc)
        self.assertEqual(dashboard.months_from(when, 3), when.replace(year=2027, month=2))
        self.assertEqual(dashboard.months_from(when, -2), when.replace(month=9))

    def test_end_of_month(self):
        when = dt.datetime(2026, 11, 30, tzinfo=dt.timezone.utc)
        self.assertEqual(dashboard.months_from(when, 3), dt.datetime(2027, 2, 28, tzinfo=dt.timezone.utc))


@PLAIN_STATIC
class DashboardTestCase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin", "admin@example.com", "pw")
        self.client.force_login(self.admin)
        self.index = reverse("admin:index")

    def dashboard(self, **params):
        response = self.client.get(self.index, params)
        self.assertEqual(response.status_code, 200)
        return response


class MakeLinkTest(DashboardTestCase):
    url = reverse("admin:dashboard_new_link")

    @mock.patch.object(models.DeclarationLink, "send")
    def test_name_and_address_any_domain_three_months_no_email(self, send):
        before = timezone.now()
        response = self.client.post(self.url, {"email": "Fred bloggs <F.B@NotInTheList.email>"})

        link = models.DeclarationLink.objects.get()
        self.assertEqual(link.email, "f.b@notinthelist.email")
        self.assertEqual(link.expires.date(), dashboard.months_from(before, 3).date())
        self.assertGreaterEqual(link.issued, before)
        send.assert_not_called()
        self.assertEqual(mail.outbox, [])
        self.assertRedirects(response, f"{self.index}?made={link.pk}#links",
                             fetch_redirect_response=False)
        self.assertTrue(LogEntry.objects.filter(action_flag=ADDITION, object_id=str(link.pk)).exists())

        page = self.dashboard(made=link.pk)
        self.assertContains(page, "Link made for Fred bloggs &lt;f.b@notinthelist.email&gt;")
        self.assertContains(page, f'value="{link.absolute_url()}"')
        self.assertContains(page, 'class="just-made"')

    def test_plain_address(self):
        self.client.post(self.url, {"email": " someone@example.org "})
        self.assertEqual(models.DeclarationLink.objects.get().email, "someone@example.org")

    def test_same_address_renews_the_link_and_keeps_its_key(self):
        old = models.DeclarationLink.objects.create(
            email="f.b@notinthelist.email",
            expires=timezone.now() - dt.timedelta(days=5),
            issued=timezone.now() - dt.timedelta(days=200),
        )
        self.client.post(self.url, {"email": "Fred <F.B@notinthelist.email>"})
        link = models.DeclarationLink.objects.get()
        self.assertEqual(link.key, old.key)
        self.assertFalse(link.expired)
        self.assertGreater(link.issued, timezone.now() - dt.timedelta(minutes=1))
        self.assertTrue(LogEntry.objects.filter(action_flag=CHANGE, object_id=str(link.pk)).exists())
        self.assertContains(self.dashboard(), "Link renewed")

    def test_not_an_address(self):
        for typed in ("Fred Bloggs", "fred@", "<>", ""):
            with self.subTest(typed=typed):
                response = self.client.post(self.url, {"email": typed}, follow=True)
                self.assertContains(response, "isn&#x27;t an email address")
        self.assertFalse(models.DeclarationLink.objects.exists())

    def test_post_only_and_needs_permission(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        staff = User.objects.create_user("staff", password="pw", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.post(self.url, {"email": "a@b.com"}).status_code, 403)
        self.assertFalse(models.DeclarationLink.objects.exists())


class RecentLinksTest(DashboardTestCase):
    def test_last_two_months_newest_first_with_full_links(self):
        now = timezone.now()
        def link(email, days_ago, expires_in):
            return models.DeclarationLink.objects.create(
                email=email, issued=now - dt.timedelta(days=days_ago),
                expires=now + dt.timedelta(days=expires_in),
            )
        new = link("new@nhs.net", 1, 13)
        older = link("older@nhs.net", 40, 50)
        expired = link("expired@nhs.net", 30, -16)
        link("too-old@nhs.net", 70, 30)

        page = self.dashboard()
        html = page.content.decode()
        self.assertEqual(list(page.context["recent_links"]), [new, expired, older])
        self.assertNotIn("too-old@nhs.net", html)
        self.assertLess(html.index("new@nhs.net"), html.index("older@nhs.net"))
        for live in (new, older):
            self.assertIn(f'value="https://www.sunshineuk.org/declare/{live.key}"', html)
        self.assertNotIn(expired.key, html)  # no copying a dead link
        self.assertContains(page, "Make it again above to renew it.")


class LatestDeclarationsTest(DashboardTestCase):
    def setUp(self):
        super().setUp()
        self.doctor = make_doctor(name="Dr Latest")
        now = timezone.now()
        self.declarations = [
            models.DeclarationV3.objects.create(
                doctor=self.doctor, timestamp=now - dt.timedelta(days=n),
                markdown=f"## Declaration {n}\n\n### Consultancy\n\n- **Details:** Number {n}\n\nNothing else to declare.",
            )
            for n in range(12)
        ]

    def test_ten_newest_with_summary_and_show_all(self):
        page = self.dashboard()
        self.assertContains(page, ">Delete entry</button>", count=10)
        self.assertEqual(list(page.context["latest_declarations"]), self.declarations[:10])
        self.assertContains(page, "Consultancy, Nothing else to declare.")
        self.assertContains(page, "Number 9")
        self.assertNotContains(page, "Number 10")
        self.assertContains(page, reverse("admin:doctors_declarationv3_changelist"))

    def test_delete_one(self):
        doomed = self.declarations[0]
        url = reverse("admin:dashboard_delete_declaration", args=[doomed.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, follow=True)
        self.assertRedirects(response, self.index)
        self.assertContains(response, "Deleted Dr Latest’s declaration of")
        self.assertFalse(models.DeclarationV3.objects.filter(pk=doomed.pk).exists())
        self.assertEqual(models.DeclarationV3.objects.count(), 11)
        self.assertTrue(models.Doctor.objects.filter(pk=self.doctor.pk).exists())
        self.assertTrue(LogEntry.objects.filter(action_flag=DELETION, object_id=str(doomed.pk)).exists())

    def test_delete_needs_permission(self):
        staff = User.objects.create_user("staff", password="pw", is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename="view_declarationv3"))
        self.client.force_login(staff)
        page = self.dashboard()
        self.assertContains(page, "Latest declarations")
        self.assertNotContains(page, "Delete entry")
        url = reverse("admin:dashboard_delete_declaration", args=[self.declarations[0].pk])
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertEqual(models.DeclarationV3.objects.count(), 12)


class FindDoctorTest(DashboardTestCase):
    def setUp(self):
        super().setUp()
        self.amara = make_doctor(name="Amara Adams", email="amara@leeds.nhs.uk", gmc_number="1112223")
        models.DeclarationV3.objects.create(
            doctor=self.amara,
            markdown="## Declaration on 1 November 2026\n\n### Consultancy\n\n- **Details:** Acme Pharma board",
        )
        self.ben = make_doctor(name="Ben Begum")

    def found(self, q):
        return list(self.dashboard(q=q).context["found"])

    def test_by_name_email_gmc_and_declaration(self):
        for q, expected in (("amara", [self.amara]), ("LEEDS.NHS", [self.amara]),
                            ("1112223", [self.amara]), ("acme pharma", [self.amara]),
                            ("begum", [self.ben]), ("nobody like this", [])):
            with self.subTest(q=q):
                self.assertEqual(self.found(q), expected)

    def test_shows_the_full_entry(self):
        page = self.dashboard(q="amara")
        for text in ("Amara Adams", "1112223", "amara@leeds.nhs.uk",
                     "<h2>Declaration on 1 November 2026</h2>", "Acme Pharma board"):
            self.assertContains(page, text)
        self.assertContains(page, "1 doctor found")

    def test_delete_doctor_and_their_declarations_then_back_to_the_dashboard(self):
        page = self.dashboard(q="amara")
        delete_url = reverse("admin:doctors_doctor_delete", args=[self.amara.pk]) + "?from=dashboard"
        self.assertContains(page, delete_url)

        confirm = self.client.get(delete_url)  # the admin's "Are you sure?" page
        self.assertContains(confirm, "Declaration (v3)")
        response = self.client.post(delete_url, {"post": "yes"})
        self.assertRedirects(response, self.index, fetch_redirect_response=False)
        self.assertFalse(models.Doctor.objects.filter(pk=self.amara.pk).exists())
        self.assertFalse(models.DeclarationV3.objects.filter(doctor_id=self.amara.pk).exists())
        self.assertTrue(models.Doctor.objects.filter(pk=self.ben.pk).exists())

    def test_delete_one_declaration_from_the_results_keeps_the_search(self):
        declaration = self.amara.declarationv3_set.get()
        response = self.client.post(
            reverse("admin:dashboard_delete_declaration", args=[declaration.pk]), {"q": "amara"}
        )
        self.assertRedirects(response, f"{self.index}?q=amara", fetch_redirect_response=False)
        self.assertTrue(models.Doctor.objects.filter(pk=self.amara.pk).exists())

    def test_search_text_is_shown_as_text(self):
        page = self.dashboard(q="<script>alert(1)</script>")
        self.assertNotContains(page, "<script>alert(1)")


@PLAIN_STATIC
class DashboardAccessTest(TestCase):
    def test_needs_a_staff_login(self):
        response = self.client.get(reverse("admin:index"))
        self.assertRedirects(response, f"{reverse('admin:login')}?next={reverse('admin:index')}",
                             fetch_redirect_response=False)
        self.client.force_login(User.objects.create_user("plain", password="pw"))
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)

    def test_staff_without_permissions_see_no_panels(self):
        self.client.force_login(User.objects.create_user("staff", password="pw", is_staff=True))
        page = self.client.get(reverse("admin:index"))
        self.assertEqual(page.status_code, 200)
        for heading in ("Declaration links", "Latest declarations", "Find a doctor"):
            self.assertNotContains(page, heading)


class LinkIssuedTest(TestCase):
    def test_a_fresh_key_counts_as_a_new_link(self):
        link = models.DeclarationLink.objects.create(
            email="a@nhs.net", issued=timezone.now() - dt.timedelta(days=30)
        )
        link.new_key()
        self.assertGreater(link.issued, timezone.now() - dt.timedelta(minutes=1))
