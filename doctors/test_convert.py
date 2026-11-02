"""
Tests for converting old declarations into markdown declarations (v3):
doctors/convert.py, run from the admin.
"""
import datetime as dt
import html
import itertools
import re

from django.template import Context, Template
from django.test import TestCase

from doctors import convert, models

UTC = dt.timezone.utc
_gmc_numbers = itertools.count(8000001)


def make_doctor():
    number = next(_gmc_numbers)
    return models.Doctor.objects.create(
        name=f"Dr {number}", gmc_number=str(number), job_title="Consultant",
        primary_employer="An NHS Trust", employment_address="",
    )


def detailed(doctor, created, **fields):
    declaration = models.DetailedDeclaration.objects.create(doctor=doctor, **fields)
    # dt_created is auto_now_add, so set the original date afterwards.
    models.DetailedDeclaration.objects.filter(pk=declaration.pk).update(dt_created=created)
    declaration.refresh_from_db()
    return declaration


def shown_text(markdown):
    """The text the register shows for some markdown, through the site's filter."""
    rendered = Template("{% load markdown_deux_tags %}{{ md|markdown }}").render(
        Context({"md": markdown})
    )
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", rendered)).split())


def converted(old):
    prefix = "detaileddeclaration" if isinstance(old, models.DetailedDeclaration) else "declaration"
    return models.DeclarationV3.objects.get(migrated_from=f"{prefix}:{old.pk}")


class DetailedDeclarationTest(TestCase):
    def test_markdown_and_date(self):
        doctor = make_doctor()
        old = detailed(
            doctor, dt.datetime(2019, 7, 9, 10, 30, tzinfo=UTC), for_year=2018,
            pharmaceutical_companies=True, technology_companies=True,
            consultancy_band="£1000- £2000",
            consultancy_details="Advisory board,\r\n  Acme_Pharma *2* meetings",
            conferences=True, meals=True, sponsored_band="£100- £1000",
            sponsored_details="Conference in Rome", political=True,
        )
        models.WorkDetails.objects.create(
            declaration=old, category="NHS",
            job_title="Consultant cardiologist", institution="Leeds Teaching Hospitals",
        )
        models.WorkDetails.objects.create(
            declaration=old, category="Private clinical work", institution="Spire Leeds",
        )
        self.assertEqual(convert.convert_old_declarations(), 1)

        new = converted(old)
        self.assertEqual(new.doctor, doctor)
        self.assertEqual(new.timestamp, old.dt_created)
        self.assertEqual(new.markdown, (
            "## Declaration on 9 July 2019\n\n"
            "**Declaration period:** 2018\n\n"
            "### Main employment\n\n"
            "- **Type:** NHS\n"
            "- **Details:** Consultant cardiologist / Leeds Teaching Hospitals\n\n"
            "### Main employment\n\n"
            "- **Type:** Private clinical work\n"
            "- **Details:** Spire Leeds\n\n"
            "### Consultancy\n\n"
            "- **Relates to:** Pharmaceutical companies, Technology companies\n"
            "- **Income band:** £1,000 – £2,000\n"
            "- **Details:** Advisory board, Acme\\_Pharma \\*2\\* meetings\n\n"
            "### Sponsored / educational events\n\n"
            "- **Relates to:** Conferences, Meals / hospitality / travel\n"
            "- **Income band:** £100 – £1,000\n"
            "- **Details:** Conference in Rome\n\n"
            "### Political / membership organisations\n\n"
            "Declared, with no further details."
        ))

    def test_nothing_to_declare(self):
        when = dt.datetime(2020, 1, 15, 9, 0, tzinfo=UTC)
        alone = detailed(make_doctor(), when, for_year=2019, nothing_to_declare=True)
        employed = detailed(make_doctor(), when, for_year=2019, nothing_to_declare=True)
        models.WorkDetails.objects.create(declaration=employed, category="Academic")
        convert.convert_old_declarations()
        self.assertTrue(converted(alone).markdown.endswith("\n\nNothing to declare."))
        self.assertTrue(converted(employed).markdown.endswith(
            "- **Type:** Academic\n\nNothing else to declare."
        ))

    def test_every_declared_value_is_on_the_page(self):
        values = {}
        for _, flag, subs, band_field, details_field in convert.DETAILED_CATEGORIES:
            values[flag] = True
            values.update({field: True for field, _ in subs})
            values[band_field] = "£50000- £100000"
            values[details_field] = f"Details for {flag} [x] <b>"
        old = detailed(make_doctor(), dt.datetime(2019, 5, 1, tzinfo=UTC), for_year=2018, **values)
        models.WorkDetails.objects.create(
            declaration=old, category="NHS", job_title="Registrar", institution="St Elsewhere",
        )
        convert.convert_old_declarations()

        text = shown_text(converted(old).markdown)
        expected = ["2018", "NHS", "Registrar", "St Elsewhere"]
        for title, flag, subs, _, details_field in convert.DETAILED_CATEGORIES:
            expected += [title, f"Details for {flag} [x] <b>", *(label for _, label in subs)]
        for value in expected:
            with self.subTest(value=value):
                self.assertIn(value, text)
        self.assertEqual(text.count("£50,000 – £100,000"), len(convert.DETAILED_CATEGORIES))


class LegacyDeclarationTest(TestCase):
    def test_markdown_and_date(self):
        doctor = make_doctor()
        old = models.Declaration.objects.create(
            doctor=doctor, interests=True,
            date_created=dt.date(2014, 3, 3),
            dt_created=dt.datetime(2014, 3, 3, 9, 0, tzinfo=UTC),
            past_declarations="Spoke at:\r\n\r\n* Event A\r\n* Event B",
            other_declarations="",
        )
        for company, reason, band in (("Acme Pharma", "Speaker fees", 1),
                                      ("Beta Labs", "Advisory board", 3)):
            models.PharmaBenefit.objects.create(
                doctor=doctor, declaration=old, company=company, reason=reason, band=band,
            )
        models.GrantBenefit.objects.create(
            doctor=doctor, declaration=old, company="Gamma Foundation",
            reason="Trial funding", band=8,
        )
        # Not attached to a declaration, so never shown, and not converted.
        models.FeeBenefit.objects.create(doctor=doctor, company="Loose", reason="x", band=2)
        convert.convert_old_declarations()

        new = converted(old)
        self.assertEqual(new.timestamp, old.dt_created)
        self.assertEqual(new.markdown, (
            "## Declaration on 3 March 2014\n\n"
            "### Benefits received from pharmaceutical companies\n\n"
            "- **Company:** Acme Pharma · **Reason for receipt of fees:** Speaker fees"
            " · **Total amount received:** under £100\n"
            "- **Company:** Beta Labs · **Reason for receipt of fees:** Advisory board"
            " · **Total amount received:** £1,000 – £2,000\n\n"
            "### Research grants paid by private institutions to your department\n\n"
            "- **Company:** Gamma Foundation · **Reason for receipt of fees:** Trial funding"
            " · **Total amount received:** £100,000+\n\n"
            "### Past declarations\n\n"
            "Spoke at:\n\n* Event A\n* Event B"
        ))

    def test_nothing_declared(self):
        when = dict(date_created=dt.date(2013, 6, 1),
                    dt_created=dt.datetime(2013, 6, 1, 12, tzinfo=UTC))
        nothing = models.Declaration.objects.create(doctor=make_doctor(), **when)
        flagged = models.Declaration.objects.create(doctor=make_doctor(), interests=True, **when)
        convert.convert_old_declarations()
        self.assertEqual(converted(nothing).markdown,
                         "## Declaration on 1 June 2013\n\nNothing to declare.")
        self.assertTrue(converted(flagged).markdown.endswith(
            "Interests to declare, but no details given."
        ))

    def test_date_shown_on_the_register_wins(self):
        # dt_created on another day (say, edited later): keep date_created.
        old = models.Declaration.objects.create(
            doctor=make_doctor(), date_created=dt.date(2012, 2, 1),
            dt_created=dt.datetime(2015, 8, 20, tzinfo=UTC),
        )
        convert.convert_old_declarations()
        new = converted(old)
        self.assertEqual(new.timestamp.astimezone(convert.UK).date(), dt.date(2012, 2, 1))
        self.assertTrue(new.markdown.startswith("## Declaration on 1 February 2012\n\n"))

    def test_every_declared_value_is_on_the_page(self):
        doctor = make_doctor()
        old = models.Declaration.objects.create(
            doctor=doctor, past_declarations="Past *one*", other_declarations="Other two",
        )
        related = ("pharmabenefit_set", "othermedicalbenefit_set", "feebenefit_set", "grantbenefit_set")
        for n, name in enumerate(related, start=1):
            getattr(old, name).create(doctor=doctor, company=f"Company {n}",
                                      reason=f"Reason {n} <i>", band=n)
        convert.convert_old_declarations()
        text = shown_text(converted(old).markdown)
        for value in ["Past one", "Other two", *(f"Company {n}" for n in range(1, 5)),
                      *(f"Reason {n} <i>" for n in range(1, 5)), *convert.NEW_BANDS[:4],
                      *(title for _, title in convert.BENEFIT_KINDS)]:
            with self.subTest(value=value):
                self.assertIn(value, text)


class ConvertingTwiceTest(TestCase):
    def test_only_new_rows_are_converted(self):
        doctor = make_doctor()
        detailed(doctor, dt.datetime(2019, 1, 1, tzinfo=UTC), for_year=2018)
        models.Declaration.objects.create(doctor=doctor)
        models.DeclarationV3.objects.create(doctor=doctor, markdown="## Made with the form")
        self.assertEqual(convert.pending_count(), 2)
        self.assertEqual(convert.convert_old_declarations(), 2)
        self.assertEqual(convert.pending_count(), 0)
        self.assertEqual(convert.convert_old_declarations(), 0)

        models.Declaration.objects.create(doctor=doctor)
        self.assertEqual(convert.convert_old_declarations(), 1)
        self.assertEqual(models.DeclarationV3.objects.count(), 4)
