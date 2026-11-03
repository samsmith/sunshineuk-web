"""
The declaration form in a real browser (Chromium, driven by Playwright)
against a live copy of the site: what declare.js does as a doctor fills the
form in, and what then shows on their page. The form is
templates/declare.html and static/declare.js.

Skipped unless Playwright and its Chromium are installed:

    pip install -r requirements-dev.txt
    playwright install chromium
"""
import os
import unittest
from datetime import datetime, timedelta
from unittest import mock
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.utils import timezone

from doctors import convert, models
from doctors.test_register import AWKWARD_TEXT, PLAIN_STATIC, make_doctor

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

UK = ZoneInfo("Europe/London")
BANDS = convert.NEW_BANDS  # as the form labels them

# The category buttons, in the order the form shows them.
CATEGORIES = [
    "Main employment", "Consultancy", "Academic", "Other work", "Financial",
    "Spousal / family", "Sponsored / educational events",
    "Political / membership organisations", "Other declarations",
]

SECTION_1 = {
    "name": "Dr Amara Okafor",
    "job_title": "Consultant Cardiologist",
    "primary_employer": "Leeds Teaching Hospitals NHS Trust",
    "gmc_number": "7100001",
}

# The cards under "Your declarations so far", as (title, text). The
# "Nothing to declare" card has no title.
LEDGER = """() => [...document.querySelectorAll('#ledger .record')].map(card => [
  card.querySelector('.record-title') && card.querySelector('.record-title').textContent,
  card.querySelector('.record-body').textContent])"""

# Each declaration on a doctor's page, newest first, as lines: "h2: ...",
# "h3: ...", "- ..." for each bullet and "p: ..." for each paragraph.
DECLARATIONS = """() => [...document.querySelectorAll('body > .container > .row.box')]
  .slice(1).map(box => {
    const lines = [];
    for (const el of box.querySelector('.col-md-12').children) {
      if (el.tagName === 'HR') break;
      if (el.tagName === 'UL') [...el.children].forEach(li => lines.push('- ' + li.textContent));
      else lines.push(el.tagName.toLowerCase() + ': ' + el.textContent);
    }
    return lines;
  })"""

# Markup in the declarations besides their headings, lists and bold labels:
# formatting, links, images or script made out of what was typed.
STRAY_MARKUP = """() => [...document.querySelectorAll('body > .container > .row.box')]
  .slice(1).flatMap(box => [...box.querySelectorAll('.col-md-12 *')])
  .filter(el => !['H2', 'H3', 'UL', 'LI', 'P', 'HR'].includes(el.tagName)
    && !(el.tagName === 'STRONG' && el.parentElement.tagName === 'LI'
         && ['Type:', 'Relates to:', 'Income band:', 'Details:'].includes(el.textContent)))
  .map(el => el.outerHTML)"""

BROWSER_DATE = """() => new Date().toLocaleDateString(
  'en-GB', {day: 'numeric', month: 'long', year: 'numeric'})"""


def one_line(text):
    return " ".join(text.split())


def shown(typed):
    """How the form shows typed details: on one line, NULs dropped."""
    return one_line(typed.replace("\x00", ""))


def uk_date(date):
    return f"{date.day} {date:%B %Y}"


@unittest.skipIf(
    sync_playwright is None, "Playwright isn't installed: pip install -r requirements-dev.txt"
)
@PLAIN_STATIC
class DeclarationFormBrowserTest(StaticLiveServerTestCase):

    @classmethod
    def setUpClass(cls):
        # Playwright keeps an asyncio event loop running in this thread, and
        # Django won't use the database from a thread that has one.
        cls.enterClassContext(mock.patch.dict(os.environ, {"DJANGO_ALLOW_ASYNC_UNSAFE": "true"}))
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(
                f"Playwright can't start Chromium (playwright install chromium): {error}"
            )
        cls.addClassCleanup(cls.browser.close)
        super().setUpClass()

    def setUp(self):
        self.dialogs, self.script_errors, self.posts = [], [], []

    def tearDown(self):
        # Nothing typed into the form ran as script, and declare.js didn't fail.
        self.assertEqual(self.dialogs, [])
        self.assertEqual(self.script_errors, [])

    # ---------- helpers ----------

    def open_browser(self, timezone_id="Europe/London"):
        """A new browser window, its clock set to timezone_id."""
        context = self.browser.new_context(
            base_url=self.live_server_url, timezone_id=timezone_id, locale="en-GB",
        )
        self.addCleanup(context.close)
        # The pages also load jQuery and a web font from other sites, which
        # the form doesn't need.
        context.route(
            lambda url: not url.startswith(self.live_server_url), lambda route: route.abort()
        )
        page = context.new_page()
        page.set_default_timeout(10_000)
        page.on("dialog", self._on_dialog)
        page.on("pageerror", self._on_page_error)
        page.on("request", self._on_request)
        return page

    def _on_dialog(self, dialog):
        self.dialogs.append(dialog.message)
        dialog.dismiss()

    def _on_page_error(self, error):
        if "declare.js" in (error.stack or ""):
            self.script_errors.append(error.stack)

    def _on_request(self, request):
        if request.method == "POST":
            self.posts.append(request)

    def start(self, page, email="qa-doctor@msmith.net", **section_1):
        """Open a new link for `email` (as if emailed), and fill in section 1."""
        link = models.DeclarationLink.objects.create(email=email)
        page.goto(f"/declare/{link.key}")
        self.fill_section_1(page, **section_1)
        return link

    def fill_section_1(self, page, press_enter=False, **values):
        for field, value in {**SECTION_1, **values}.items():
            page.fill(f"#id_{field}", value)
        if press_enter:
            page.press("#id_gmc_number", "Enter")
        else:
            page.click("#btn-identity-done")
        page.wait_for_selector("#screen-main", state="visible")

    def fill(self, page, category, ticked=(), band="", details=""):
        """Open a category's form and fill it in; `ticked` are box labels."""
        if page.is_visible("#btn-add-another"):
            page.click("#btn-add-another")
        page.locator("#cat-grid button").nth(CATEGORIES.index(category)).click()
        for label in ticked:
            page.locator("#cat-subs").get_by_label(label, exact=True).check()
        if band:
            page.select_option("#cat-band", band)
        page.fill("#cat-details", details)

    def add(self, page, category, ticked=(), band="", details=""):
        """Add a declaration. Returns the error shown instead, if any."""
        self.fill(page, category, ticked, band, details)
        page.click("#btn-add-record")
        if page.is_visible("#cat-error"):
            return page.text_content("#cat-error")
        return None

    def add_ok(self, page, category, ticked=(), band="", details=""):
        self.assertIsNone(self.add(page, category, ticked, band, details))

    def remove(self, page, text):
        page.locator("#ledger .record", has_text=text).locator(".record-remove").click()

    def ledger(self, page):
        return [tuple(card) for card in page.evaluate(LEDGER)]

    def submit(self, page, double_click=False):
        """Make the declaration. Returns the UK dates it could have been made on."""
        if page.is_visible("#btn-all-finished"):
            page.click("#btn-all-finished")
        before = datetime.now(UK).date()
        with page.expect_navigation():
            if double_click:
                page.dblclick("#btn-submit")
            else:
                page.click("#btn-submit")
        return {before, datetime.now(UK).date()}

    def assert_declared(self, page, dates, lines, which=0):
        """
        The doctor's page shows `lines` in a declaration headed with one of
        `dates`, with no markup made out of what was typed.
        """
        heading, *rest = page.evaluate(DECLARATIONS)[which]
        self.assertIn(heading, [f"h2: Declaration on {uk_date(date)}" for date in dates])
        self.assertEqual(rest, lines)
        self.assertEqual(page.evaluate(STRAY_MARKUP), [])

    def header(self, page):
        return one_line(page.text_content("h2.doctor-detail"))

    def identifiers(self, page):
        return [one_line(text) for text in page.locator(".identifiers li").all_text_contents()]

    # ---------- start to finish ----------

    def test_from_the_declare_page_to_the_register(self):
        page = self.open_browser()
        # Ask for a link, then take it from the database rather than the email.
        page.goto("/declare")
        page.fill("#id_email", "  QA-Amara.Okafor@Sub.MSMITH.net ")
        with page.expect_navigation():
            page.click("form[role=form] button[type=submit]")
        self.assertEqual(page.url, f"{self.live_server_url}/declare/pending")
        link = models.DeclarationLink.objects.get(email="qa-amara.okafor@sub.msmith.net")
        page.goto(f"/declare/{link.key}")

        self.fill_section_1(page)
        self.assertEqual(
            page.text_content("#identity-summary"),
            "Declaring as Dr Amara Okafor, Consultant Cardiologist at Leeds Teaching"
            " Hospitals NHS Trust · identifier 7100001",
        )
        self.add_ok(page, "Main employment", ["NHS"],
                    details="Consultant cardiologist, Leeds General Infirmary")
        self.add_ok(page, "Consultancy", ["Pharmaceutical companies"], "£5,000 – £10,000",
                    "Advisory board member, Acme Pharma (anticoagulants)")
        self.add_ok(page, "Sponsored / educational events",
                    ["Conferences", "Meals / hospitality / travel"], "£1,000 – £2,000",
                    "Travel and accommodation for ESC Congress 2026, Munich")
        self.assertEqual(self.ledger(page), [
            ("Main employment", "NHS · Consultant cardiologist, Leeds General Infirmary"),
            ("Consultancy", "Pharmaceutical companies · £5,000 – £10,000"
                            " · “Advisory board member, Acme Pharma (anticoagulants)”"),
            ("Sponsored / educational events",
             "Conferences, Meals / hospitality / travel · £1,000 – £2,000"
             " · “Travel and accommodation for ESC Congress 2026, Munich”"),
        ])
        dates = self.submit(page)

        doctor = models.Doctor.objects.get()
        self.assertEqual(page.url, self.live_server_url + doctor.get_absolute_url())
        self.assertEqual(doctor.email, "qa-amara.okafor@sub.msmith.net")
        self.assertEqual(self.header(page), "Dr Amara Okafor | GMC# 7100001")
        self.assertEqual(
            self.identifiers(page), ["Leeds Teaching Hospitals NHS Trust", "Consultant Cardiologist"]
        )
        self.assert_declared(page, dates, [
            "h3: Main employment",
            "- Type: NHS · Details: Consultant cardiologist, Leeds General Infirmary",
            "h3: Consultancy",
            "- Relates to: Pharmaceutical companies · Income band: £5,000 – £10,000"
            " · Details: Advisory board member, Acme Pharma (anticoagulants)",
            "h3: Sponsored / educational events",
            "- Relates to: Conferences, Meals / hospitality / travel"
            " · Income band: £1,000 – £2,000"
            " · Details: Travel and accommodation for ESC Congress 2026, Munich",
        ])

        # The link is used up: opening it again finds it expired.
        self.assertEqual(page.goto(f"/declare/{link.key}").status, 404)
        self.assertIn("Your link seems to have expired.", page.text_content("body"))

    def test_returning_doctor_adds_a_declaration_from_their_page(self):
        doctor = make_doctor(**SECTION_1, email="qa-amara.okafor@msmith.net")
        models.DeclarationV3.objects.create(
            doctor=doctor, timestamp=timezone.now() - timedelta(days=1),
            markdown="## Declaration on 1 November 2026\n\n"
                     "### Consultancy\n\n- **Details:** First declaration",
        )
        page = self.open_browser()
        page.goto(doctor.get_absolute_url())
        with page.expect_navigation():
            page.get_by_role("link", name="Add a declaration").click()
        page.fill("#id_email", "QA-Amara.Okafor@MSMITH.NET")
        with page.expect_navigation():
            page.click("form[role=form] button[type=submit]")
        self.assertEqual(page.url, f"{self.live_server_url}/re-establish/pending")
        link = models.DeclarationLink.objects.get(email="qa-amara.okafor@msmith.net")
        page.goto(f"/declare/{link.key}")
        self.assertEqual(page.url, f"{self.live_server_url}/declare/{doctor.pk}/add/{link.key}/")

        # Section 1 comes filled in from their record; they update their job title.
        self.assertEqual({field: page.input_value(f"#id_{field}") for field in SECTION_1}, SECTION_1)
        page.fill("#id_job_title", "Consultant Cardiologist and Clinical Director")
        page.click("#btn-identity-done")
        self.add_ok(page, "Consultancy", ["Pharmaceutical companies"], "£10,000 – £50,000",
                    "Advisory board chair, Acme Pharma (2026–27)")
        dates = self.submit(page)

        self.assertEqual(page.url, self.live_server_url + doctor.get_absolute_url())
        self.assertEqual(self.identifiers(page), [
            "Leeds Teaching Hospitals NHS Trust", "Consultant Cardiologist and Clinical Director",
        ])
        # Newest first.
        self.assert_declared(page, dates, [
            "h3: Consultancy",
            "- Relates to: Pharmaceutical companies · Income band: £10,000 – £50,000"
            " · Details: Advisory board chair, Acme Pharma (2026–27)",
        ])
        self.assertEqual(page.evaluate(DECLARATIONS)[1], [
            "h2: Declaration on 1 November 2026", "h3: Consultancy", "- Details: First declaration",
        ])

    # ---------- "Nothing to declare" ----------

    def test_nothing_to_declare(self):
        page = self.open_browser()
        self.start(page)
        page.click("#btn-nothing")
        self.assertEqual(self.ledger(page), [(None, "Nothing to declare.")])
        self.assertFalse(page.is_visible("#btn-nothing"))
        dates = self.submit(page)
        self.assert_declared(page, dates, ["p: Nothing to declare."])

    def test_nothing_to_declare_then_an_interest(self):
        page = self.open_browser()
        self.start(page)
        page.click("#btn-nothing")
        self.add_ok(page, "Financial", ["Shares, stocks or company ownership"], "£1,000 – £2,000",
                    "Shares in a medtech start-up")
        self.assertEqual(self.ledger(page), [
            ("Financial", "Shares, stocks or company ownership · £1,000 – £2,000"
                          " · “Shares in a medtech start-up”"),
        ])
        dates = self.submit(page)
        self.assert_declared(page, dates, [
            "h3: Financial",
            "- Relates to: Shares, stocks or company ownership · Income band: £1,000 – £2,000"
            " · Details: Shares in a medtech start-up",
        ])

    def test_nothing_to_declare_then_main_employment(self):
        page = self.open_browser()
        self.start(page)
        page.click("#btn-nothing")
        self.add_ok(page, "Main employment", ["Private work"],
                    details="Private GP sessions, Manchester")
        self.assertEqual(self.ledger(page), [
            ("Main employment", "Private work · Private GP sessions, Manchester"),
            (None, "Nothing else to declare."),
        ])
        dates = self.submit(page)
        self.assert_declared(page, dates, [
            "h3: Main employment",
            "- Type: Private work · Details: Private GP sessions, Manchester",
            "p: Nothing else to declare.",
        ])

    def test_main_employment_alone_is_nothing_else_to_declare(self):
        # Main employment isn't an interest, so the line comes without the button.
        page = self.open_browser()
        self.start(page)
        self.add_ok(page, "Main employment", ["NHS"],
                    details="ST5 Respiratory Medicine, John Radcliffe Hospital")
        self.add_ok(page, "Main employment", ["Academic"],
                    details="Clinical Research Fellow, University of Oxford")
        listed = [
            ("Main employment", "NHS · ST5 Respiratory Medicine, John Radcliffe Hospital"),
            ("Main employment", "Academic · Clinical Research Fellow, University of Oxford"),
            (None, "Nothing else to declare."),
        ]
        self.assertEqual(self.ledger(page), listed)
        # An interest takes the line away, and removing it brings it back.
        self.add_ok(page, "Political / membership organisations",
                    details="Member, Royal Society of Medicine")
        self.assertNotIn((None, "Nothing else to declare."), self.ledger(page))
        self.remove(page, "Royal Society of Medicine")
        self.assertEqual(self.ledger(page), listed)
        dates = self.submit(page)
        self.assert_declared(page, dates, [
            "h3: Main employment",
            "- Type: NHS · Details: ST5 Respiratory Medicine, John Radcliffe Hospital",
            "- Type: Academic · Details: Clinical Research Fellow, University of Oxford",
            "p: Nothing else to declare.",
        ])

    # ---------- adding declarations ----------

    def test_every_category_option_and_band(self):
        page = self.open_browser()
        self.start(page)
        self.assertEqual(page.eval_on_selector_all(
            "#cat-grid button", "buttons => buttons.map(b => b.firstChild.textContent)"
        ), CATEGORIES)
        self.assertEqual(page.locator("#cat-band option").all_text_contents()[1:], BANDS)
        # Each category's form: its title, its boxes, and whether it has a band.
        for category, boxes, has_band in (
            ("Main employment", ["NHS", "Academic", "Private work"], False),
            ("Consultancy", ["Pharmaceutical companies", "Technology companies", "Other"], True),
            ("Academic", ["Research grants", "Other"], True),
            ("Other work", ["Public relations", "Commercial relationships", "Media"], True),
            ("Financial",
             ["Patents owned / part-owned", "Shares, stocks or company ownership"], True),
            ("Spousal / family", [], True),
            ("Sponsored / educational events",
             ["Conferences", "Meals / hospitality / travel"], True),
            ("Political / membership organisations", [], True),
            ("Other declarations", [], False),
        ):
            with self.subTest(category=category):
                self.fill(page, category)
                self.assertEqual(page.text_content("#cat-form-title"), category)
                self.assertEqual(
                    [one_line(label) for label in page.locator("#cat-subs label").all_text_contents()],
                    boxes,
                )
                self.assertEqual(page.is_visible("#band-field"), has_band)
                page.click("#btn-cancel-cat")

        entries = [
            ("Main employment", ["NHS"], "", "Consultant, Barts Heart Centre"),
            ("Main employment", ["Academic"], "", "Honorary senior lecturer, QMUL"),
            ("Main employment", ["Private work"], "", "Sessional work, Harley Street Clinic"),
            ("Consultancy", ["Pharmaceutical companies", "Technology companies", "Other"],
             "under £100", "Three one-off calls"),
            ("Consultancy", [], "£100 – £1,000", "No box ticked"),
            ("Academic", ["Research grants", "Other"], "£100,000+", "NIHR programme grant"),
            ("Academic", ["Research grants"], "£1,000 – £2,000", ""),
            ("Other work", ["Public relations", "Commercial relationships", "Media"],
             "£2,000 – £5,000", "Media training and paid columns"),
            ("Financial", ["Patents owned / part-owned", "Shares, stocks or company ownership"],
             "£10,000 – £50,000", "Valve sizing patent; shares in ValveCo Ltd"),
            ("Spousal / family", [], "£50,000 – £100,000", ""),
            ("Sponsored / educational events", ["Conferences", "Meals / hospitality / travel"],
             "", "Speaker dinner, no fee"),
            ("Sponsored / educational events", ["Conferences"], "£5,000 – £10,000", ""),
            ("Political / membership organisations", [], "", "Elected member, BMA council"),
            ("Other declarations", [], "", "Trustee of a local hospice charity (unpaid)"),
        ]
        self.assertEqual(sorted({band for _, _, band, _ in entries} - {""}), sorted(BANDS))
        for entry in entries:
            self.add_ok(page, *entry)
        dates = self.submit(page)
        self.assert_declared(page, dates, [
            "h3: Main employment",
            "- Type: NHS · Details: Consultant, Barts Heart Centre",
            "- Type: Academic · Details: Honorary senior lecturer, QMUL",
            "- Type: Private work · Details: Sessional work, Harley Street Clinic",
            "h3: Consultancy",
            "- Relates to: Pharmaceutical companies, Technology companies, Other"
            " · Income band: under £100 · Details: Three one-off calls",
            "- Income band: £100 – £1,000 · Details: No box ticked",
            "h3: Academic",
            "- Relates to: Research grants, Other · Income band: £100,000+"
            " · Details: NIHR programme grant",
            "- Relates to: Research grants · Income band: £1,000 – £2,000",
            "h3: Other work",
            "- Relates to: Public relations, Commercial relationships, Media"
            " · Income band: £2,000 – £5,000 · Details: Media training and paid columns",
            "h3: Financial",
            "- Relates to: Patents owned / part-owned, Shares, stocks or company ownership"
            " · Income band: £10,000 – £50,000 · Details: Valve sizing patent; shares in ValveCo Ltd",
            "h3: Spousal / family",
            "- Income band: £50,000 – £100,000",
            "h3: Sponsored / educational events",
            "- Relates to: Conferences, Meals / hospitality / travel"
            " · Details: Speaker dinner, no fee",
            "- Relates to: Conferences · Income band: £5,000 – £10,000",
            "h3: Political / membership organisations",
            "- Details: Elected member, BMA council",
            "h3: Other declarations",
            "- Details: Trustee of a local hospice charity (unpaid)",
        ])

    def test_grouped_by_category_in_the_order_of_the_buttons(self):
        page = self.open_browser()
        self.start(page)
        for category, letter in (
            ("Consultancy", "A"), ("Academic", "B"), ("Consultancy", "C"),
            ("Main employment", "D"), ("Other declarations", "E"), ("Academic", "F"),
            ("Sponsored / educational events", "G"),
        ):
            ticked = ["NHS"] if category == "Main employment" else []
            self.add_ok(page, category, ticked, details=f"Added {letter}")
        # Each category's declarations stay in the order they were added.
        self.assertEqual([title for title, _ in self.ledger(page)], [
            "Main employment", "Consultancy", "Consultancy", "Academic", "Academic",
            "Sponsored / educational events", "Other declarations",
        ])
        dates = self.submit(page)
        self.assert_declared(page, dates, [
            "h3: Main employment", "- Type: NHS · Details: Added D",
            "h3: Consultancy", "- Details: Added A", "- Details: Added C",
            "h3: Academic", "- Details: Added B", "- Details: Added F",
            "h3: Sponsored / educational events", "- Details: Added G",
            "h3: Other declarations", "- Details: Added E",
        ])

    def test_errors_cancel_and_remove(self):
        page = self.open_browser()
        self.start(page)
        self.add_ok(page, "Consultancy", ["Pharmaceutical companies"], "£100 – £1,000", "Keep me")
        self.add_ok(page, "Academic", ["Research grants"], "£1,000 – £2,000", "Remove me")

        # Each kind of form says what's missing, and adds nothing.
        no_job = "Choose NHS, Academic or Private work, and add your job title and institution."
        for category, ticked, details, error in (
            ("Consultancy", ["Pharmaceutical companies"], "",
             "Add an income band or some details before saving this declaration."),
            ("Main employment", [], "No type chosen", no_job),
            ("Main employment", ["NHS"], "   ", no_job),
            ("Other declarations", [], " \n ", "Add some details before saving this declaration."),
        ):
            with self.subTest(category=category, ticked=ticked, details=details):
                self.assertEqual(self.add(page, category, ticked, details=details), error)
                page.click("#btn-cancel-cat")

        # Cancel drops what was filled in...
        self.fill(page, "Financial", ["Shares, stocks or company ownership"], "£2,000 – £5,000",
                  "Cancelled")
        page.click("#btn-cancel-cat")
        # ...and putting an error right adds it.
        self.assertIsNotNone(self.add(page, "Consultancy", ["Technology companies"]))
        page.select_option("#cat-band", "£2,000 – £5,000")
        page.click("#btn-add-record")
        self.assertTrue(page.is_visible("#after-add"))
        self.remove(page, "Remove me")

        self.assertEqual(self.ledger(page), [
            ("Consultancy", "Pharmaceutical companies · £100 – £1,000 · “Keep me”"),
            ("Consultancy", "Technology companies · £2,000 – £5,000"),
        ])
        dates = self.submit(page)
        self.assert_declared(page, dates, [
            "h3: Consultancy",
            "- Relates to: Pharmaceutical companies · Income band: £100 – £1,000 · Details: Keep me",
            "- Relates to: Technology companies · Income band: £2,000 – £5,000",
        ])

    # ---------- what's typed ----------

    def test_details_shown_exactly_as_typed(self):
        page = self.open_browser()
        self.start(page)
        long_text = " ".join(f"Sentence {n} of a long-running advisory role." for n in range(1, 120))
        self.assertGreater(len(long_text), 5000)
        typed = [*AWKWARD_TEXT, "Before\x00After: a NUL pasted in", long_text]
        for text in typed:
            self.add_ok(page, "Other declarations", details=text)
        self.assertEqual(
            self.ledger(page), [("Other declarations", f"“{shown(text)}”") for text in typed]
        )
        dates = self.submit(page)
        self.assert_declared(
            page, dates, ["h3: Other declarations", *(f"- Details: {shown(text)}" for text in typed)]
        )

    def test_section_1_shown_as_typed(self):
        typed = {
            "name": "  Dr Siân Ó Dálaigh-Nguyễn <img src=x onerror=alert('name')> & Co  ",
            # As long as the box allows.
            "job_title": ("Consultant \"Locum\" & Lead <script>alert('job')</script> " * 4)[:199] + ".",
            "primary_employer": "Guy's & St Thomas' \"><svg onload=alert('employer')>",
            "gmc_number": "ORCID 0000-0002-1825-0097",
        }
        values = {field: value.strip() for field, value in typed.items()}
        page = self.open_browser()
        link = models.DeclarationLink.objects.create(email="qa-doctor@msmith.net")
        page.goto(f"/declare/{link.key}")
        # Enter in a box goes on to section 2, without sending the form.
        self.fill_section_1(page, press_enter=True, **typed)
        self.assertEqual(self.posts, [])
        self.assertEqual(
            page.text_content("#identity-summary"),
            f"Declaring as {values['name']}, {values['job_title']} at"
            f" {values['primary_employer']} · identifier {values['gmc_number']}",
        )
        page.click("#btn-nothing")
        self.submit(page)

        self.assertEqual(self.header(page), f"{values['name']} | GMC# {values['gmc_number']}")
        self.assertEqual(self.identifiers(page), [values["primary_employer"], values["job_title"]])
        # On the register too, once their first declaration has been up an hour.
        models.DeclarationV3.objects.update(timestamp=timezone.now() - timedelta(hours=2))
        page.goto("/doctors/")
        self.assertEqual(
            [one_line(cell) for cell in page.locator("#doctor-table tr.doctor td").all_text_contents()],
            [values["name"], values["gmc_number"], values["job_title"], values["primary_employer"]],
        )
        with page.expect_navigation():
            page.click("#doctor-table tr.doctor a")
        self.assertEqual(self.header(page), f"{values['name']} | GMC# {values['gmc_number']}")

    def test_declarations_kept_when_section_1_comes_back_with_an_error(self):
        make_doctor(gmc_number="7100001", email="someone.else@msmith.net")
        page = self.open_browser()
        self.start(page, name="Dr Laura Chen")  # with GMC number 7100001
        self.add_ok(page, "Main employment", ["NHS"],
                    details="Consultant anaesthetist, Royal Free Hospital")
        self.add_ok(page, "Consultancy", ["Pharmaceutical companies", "Other"], "£2,000 – £5,000",
                    "Restored: *stars* & <angle> “curly”")
        self.add_ok(page, "Other declarations", details="Multi\nline restored")
        listed = self.ledger(page)
        self.submit(page)

        self.assertIn("GMC Number: Doctor with this GMC Number already exists.",
                      one_line(page.text_content(".alert-danger")))
        self.assertEqual(page.input_value("#id_name"), "Dr Laura Chen")
        # With the number put right, the same declarations are listed, and go through.
        page.fill("#id_gmc_number", "7100014")
        page.click("#btn-identity-done")
        self.assertEqual(self.ledger(page), listed)
        dates = self.submit(page)
        self.assertEqual(self.header(page), "Dr Laura Chen | GMC# 7100014")
        self.assert_declared(page, dates, [
            "h3: Main employment",
            "- Type: NHS · Details: Consultant anaesthetist, Royal Free Hospital",
            "h3: Consultancy",
            "- Relates to: Pharmaceutical companies, Other · Income band: £2,000 – £5,000"
            " · Details: Restored: *stars* & <angle> “curly”",
            "h3: Other declarations",
            "- Details: Multi line restored",
        ])

    # ---------- sending it ----------

    def test_dated_by_the_server_in_uk_time_whatever_the_browsers_clock(self):
        # UTC+14 and UTC-11: at any moment, one of them is on another date from the UK.
        browser_dates_differed = []
        for n, zone in enumerate(["Pacific/Kiritimati", "Pacific/Pago_Pago"]):
            with self.subTest(zone=zone):
                page = self.open_browser(timezone_id=zone)
                self.start(page, email=f"qa-{n}@msmith.net", gmc_number=f"710002{n}")
                self.add_ok(page, "Consultancy", ["Technology companies"], "£100 – £1,000",
                            f"Browser clock in {zone}")
                browser_date = page.evaluate(BROWSER_DATE)
                dates = self.submit(page)
                # The browser sends no date of its own...
                sent = parse_qs(self.posts[-1].post_data)["declaration"][0]
                self.assertTrue(sent.startswith("### Consultancy"), sent)
                # ...and the page has the UK one.
                self.assert_declared(page, dates, [
                    "h3: Consultancy",
                    "- Relates to: Technology companies · Income band: £100 – £1,000"
                    f" · Details: Browser clock in {zone}",
                ])
                browser_dates_differed.append(browser_date not in map(uk_date, dates))
        self.assertIn(True, browser_dates_differed)

    def test_double_click_sends_one_declaration(self):
        page = self.open_browser()
        self.start(page)
        self.add_ok(page, "Consultancy", ["Technology companies"], "£100 – £1,000",
                    "Double-clicked")
        self.submit(page, double_click=True)
        page.wait_for_load_state("networkidle")
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(models.DeclarationV3.objects.count(), 1)
        self.assertEqual(
            page.url, self.live_server_url + models.Doctor.objects.get().get_absolute_url()
        )
