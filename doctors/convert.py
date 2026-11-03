"""
Convert declarations made with the old forms into markdown declarations
(DeclarationV3), in the format the current form produces, so the register
only has to show one kind. Run from the admin: the "Convert old
declarations" button on the declarations (v3) list.

Everything a doctor declared goes into the markdown. The old rows are left
as they are, and each converted declaration records where it came from in
DeclarationV3.migrated_from, so converting again only picks up rows that
haven't been converted yet.
"""
import datetime as dt
import re
from zoneinfo import ZoneInfo

from django.db import transaction

from doctors import models

UK = ZoneInfo("Europe/London")

# The old band labels, in order, and the labels the current form uses for
# the same bands. Old benefits store the band as its position (1-8).
OLD_BANDS = [label for _, label in models.DetailedDeclaration.BAND_CHOICES]
NEW_BANDS = [
    "under £100",
    "£100 to under £1,000",
    "£1,000 to under £2,000",
    "£2,000 to under £5,000",
    "£5,000 to under £10,000",
    "£10,000 to under £50,000",
    "£50,000 to under £100,000",
    "£100,000+",
]

# DetailedDeclaration's categories, with the labels the current form uses:
# (title, category field, [(sub-option field, label)], band field, details field)
DETAILED_CATEGORIES = [
    ("Consultancy", "consultancy",
     [("pharmaceutical_companies", "Pharmaceutical companies"),
      ("technology_companies", "Technology companies"),
      ("consultancy_other", "Other")],
     "consultancy_band", "consultancy_details"),
    ("Academic", "academic",
     [("research_grants", "Research grants"), ("academic_other", "Other")],
     "academic_band", "academic_details"),
    ("Other work", "other_work",
     [("public_relations", "Public relations"),
      ("commercial_relationships", "Commercial relationships"),
      ("media", "Media")],
     "other_work_band", "other_work_details"),
    ("Financial", "financial",
     [("patents_owned", "Patents owned / part-owned"),
      ("shares", "Shares, stocks or company ownership")],
     "financial_band", "financial_details"),
    ("Spousal / family", "spousal", [], "spousal_band", "spousal_details"),
    ("Sponsored / educational events", "sponsored",
     [("conferences", "Conferences"), ("meals", "Meals / hospitality / travel")],
     "sponsored_band", "sponsored_details"),
    ("Political / membership organisations", "political", [],
     "political_band", "political_details"),
]

# The old Declaration's benefit tables, headed as they were on the register.
BENEFIT_KINDS = [
    ("pharmabenefit_set", "Benefits received from pharmaceutical companies"),
    ("othermedicalbenefit_set",
     "Other sources of income or benefits from other organisations relevant to medical practice"),
    ("feebenefit_set", "Fees directly received from other medical companies"),
    ("grantbenefit_set", "Research grants paid by private institutions to your department"),
]


def text(value):
    """
    What a doctor typed, as markdown that shows exactly as typed: on one
    line, with anything markdown would treat as formatting, a link or HTML
    escaped. Matches mdEscape() in static/declare.js.
    """
    value = " ".join(str(value or "").split())
    value = re.sub(r"([\\`*_\[\]<])", r"\\\1", value)
    value = re.sub(r"&(?=#?[a-zA-Z0-9]+;)", r"\\&", value)
    return value.replace("~", "&#126;")


def band(old_label):
    """The current form's label for an old band; unknown values are kept."""
    if old_label in OLD_BANDS:
        return NEW_BANDS[OLD_BANDS.index(old_label)]
    return old_label


def heading(date):
    return f"## Declaration on {date.day} {date:%B %Y}"


def section(title, fields):
    """A "### title" block with labelled bullets, as the current form writes."""
    if not fields:
        return f"### {title}\n\nDeclared, with no further details."
    return f"### {title}\n\n" + "\n".join(f"- **{label}:** {value}" for label, value in fields)


def nothing_line(has_employment):
    return "Nothing else to declare." if has_employment else "Nothing to declare."


def detailed_markdown(declaration):
    """Markdown for a DetailedDeclaration (the 2019 form)."""
    created = declaration.dt_created.astimezone(UK).date()
    blocks = [heading(created), f"**Declaration period:** {declaration.for_year}"]

    positions = list(declaration.workdetails_set.order_by("pk"))
    for position in positions:
        fields = [("Type", text(position.category))]
        details = " / ".join(text(part) for part in (position.job_title, position.institution) if part)
        if details:
            fields.append(("Details", details))
        blocks.append(section("Main employment", fields))

    interests = 0
    for title, flag, subs, band_field, details_field in DETAILED_CATEGORIES:
        chosen = [label for field, label in subs if getattr(declaration, field)]
        band_value = getattr(declaration, band_field)
        details = text(getattr(declaration, details_field))
        if not (getattr(declaration, flag) or chosen or band_value or details):
            continue
        fields = []
        if chosen:
            fields.append(("Relates to", ", ".join(chosen)))
        if band_value:
            fields.append(("Income band", text(band(band_value))))
        if details:
            fields.append(("Details", details))
        blocks.append(section(title, fields))
        interests += 1

    if not interests:
        blocks.append(nothing_line(bool(positions)))
    return "\n\n".join(blocks)


def legacy_markdown(declaration):
    """Markdown for an old Declaration (the original form) and its benefits."""
    blocks = [heading(declaration.date_created)]
    content = False

    for related, title in BENEFIT_KINDS:
        benefits = list(getattr(declaration, related).order_by("pk"))
        if not benefits:
            continue
        lines = [
            f"- **Company:** {text(b.company)} · **Reason for receipt of fees:** {text(b.reason)}"
            f" · **Total amount received:** {text(band(b.get_band_display()))}"
            for b in benefits
        ]
        blocks.append(f"### {title}\n\n" + "\n".join(lines))
        content = True

    # These two were already shown through the markdown filter, so they go
    # in as written.
    for field, title in (("past_declarations", "Past declarations"),
                         ("other_declarations", "Other declarations")):
        value = (getattr(declaration, field) or "").replace("\r\n", "\n").strip()
        if value:
            blocks.append(f"### {title}\n\n{value}")
            content = True

    if not content:
        blocks.append(
            "Interests to declare, but no details given." if declaration.interests
            else nothing_line(False)
        )
    return "\n\n".join(blocks)


def legacy_timestamp(declaration):
    """
    When an old Declaration was made: its dt_created, unless that falls on a
    different day from the date the register showed (date_created), in
    which case midday on that date.
    """
    when = declaration.dt_created
    if when and when.astimezone(UK).date() == declaration.date_created:
        return when
    return dt.datetime.combine(declaration.date_created, dt.time(12), tzinfo=UK)


def _pending():
    """The old declarations not converted yet, as (key, declaration) pairs."""
    done = set(
        models.DeclarationV3.objects.exclude(migrated_from="")
        .values_list("migrated_from", flat=True)
    )
    for declaration in models.DetailedDeclaration.objects.select_related("doctor"):
        key = f"detaileddeclaration:{declaration.pk}"
        if key not in done:
            yield key, declaration
    for declaration in models.Declaration.objects.select_related("doctor"):
        key = f"declaration:{declaration.pk}"
        if key not in done:
            yield key, declaration


def pending_count():
    return sum(1 for _ in _pending())


@transaction.atomic
def convert_old_declarations():
    """Convert every old declaration not converted yet; returns how many."""
    converted = 0
    for key, declaration in list(_pending()):
        if isinstance(declaration, models.DetailedDeclaration):
            markdown, timestamp = detailed_markdown(declaration), declaration.dt_created
        else:
            markdown, timestamp = legacy_markdown(declaration), legacy_timestamp(declaration)
        models.DeclarationV3.objects.create(
            doctor=declaration.doctor, timestamp=timestamp,
            markdown=markdown, migrated_from=key,
        )
        converted += 1
    return converted
