"""
Models for Sunshine UK.
"""
import datetime as dt
import secrets

from django.conf import settings
from django.urls import reverse
from django.db import models
from django.utils import timezone

from doctors import send_mail


class Doctor(models.Model):
    name = models.CharField(max_length=200)
    gmc_number = models.CharField(max_length=100, unique=True, verbose_name="GMC Number")
    job_title = models.CharField(max_length=200)
    primary_employer = models.CharField(max_length=200)
    employment_address = models.TextField()
    email = models.EmailField(blank=True, null=True)

    def __str__(self):
        return f'{self.name} - {self.gmc_number}'

    def get_absolute_url(self):
        return reverse('doctor-detail', kwargs={'pk': self.pk})

    def send_declaration_thanks(self):
        """
        Send this doctor an Email thanking them for their declaration,
        Explaining that if this is their frist submission it will not
        turn up for 24 hours, and with a link.
        """
        if settings.DEBUG:
            print("=" * 20)
            print("email sent")
            print("=" * 20)
        else:
            send_mail.send_mail(
                to_emails=[self.email],
                subject="Sunshine UK - Thanks for your declaration",
                template="email/declaration_thanks.html",
                template_context={
                    "settings": settings,
                    "doctor": self,
                    "register": reverse('doctor-list')
                }
            )
        return


class Declaration(models.Model):
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE)
    interests = models.BooleanField(default=False)
    past_declarations = models.TextField(blank=True, null=True)
    other_declarations = models.TextField(blank=True, null=True)
    date_created = models.DateField(default=dt.date.today)
    dt_created = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f'{self.doctor} - {self.date_created}'


class Benefit(models.Model):
    BAND_CHOICES = (
        (1, u'under \u00a3100'),
        (2, u'\u00a3100- \u00a31000'),
        (3, u'\u00a31000- \u00a32000'),
        (4, u'\u00a32000 - \u00a35000'),
        (5, u'\u00a35000 - \u00a310000'),
        (6, u'\u00a310000 - \u00a350 000'),
        (7, u'\u00a350000- \u00a3100000'),
        (8, u'\u00a3100000+'),
    )

    class Meta:
        abstract = True

    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE)
    declaration = models.ForeignKey(
        Declaration, blank=True, null=True,
        on_delete=models.SET_NULL
    )
    company = models.CharField(max_length=200)
    reason = models.CharField(max_length=200)
    band = models.IntegerField(choices=BAND_CHOICES)

class PharmaBenefit(Benefit): pass
class OtherMedicalBenefit(Benefit): pass
class FeeBenefit(Benefit): pass
class GrantBenefit(Benefit): pass

def random_token():
    """
    An unguessable, URL-safe key for emailed links. (Referenced by migration
    0001, so keep the name.) Used to be 8 hex characters, i.e. 32 bits.
    """
    return secrets.token_urlsafe(24)

# How long an emailed link works for.
LINK_LIFETIME = dt.timedelta(weeks=6)

def in_six_weeks():
    return timezone.now() + LINK_LIFETIME

# Migration 0001 names the default by its old name, so keep it working: it is
# the same function.
in_two_weeks = in_six_weeks


class DeclarationLink(models.Model):
    email = models.EmailField(unique=True)
    expires = models.DateTimeField(default=in_six_weeks)
    key = models.CharField(max_length=64, unique=True, default=random_token)
    # When the current key was made (a fresh key counts as a new link). For
    # links from before this field, migration 0008 estimates it from expires.
    issued = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.email

    @property
    def expired(self):
        """
        Has this link expired already?
        """
        return self.expires < timezone.now()

    @property
    def reusable(self):
        """
        Does this link have more than a week left? If so, asking for a link
        again sends the same key, so it doesn't matter which email is clicked.
        """
        return self.expires - timezone.now() > dt.timedelta(days=7)

    def absolute_url(self):
        return 'https://{0}/declare/{1}'.format(settings.DEFAULT_DOMAIN, self.key)

    def send(self):
        """
        Send the link to this email.
        """
        if settings.DEBUG:
            print("=" * 20)
            print(self.absolute_url())
            print("=" * 20)
        else:
            send_mail.send_mail(
                to_emails=[self.email],
                subject='Sunshine UK - Edit your public record',
                template="email/edit_public_record.html",
                template_context={
                    "link": self,
                }
            )

    def new_key(self):
        """
        Make this a new link, lasting six weeks from now. It gets a new key,
        so the old one stops working.
        """
        self.key = random_token()
        self.issued = timezone.now()
        self.expires = in_six_weeks()
        self.save()


class WorkDetails(models.Model):
    CAREER_CHOICES = (
        ("Academic", "Academic"),
        ("NHS", "NHS"),
        ("Private clinical work", "Private clinical work")
    )
    dt_created = models.DateTimeField(auto_now_add=True)
    declaration = models.ForeignKey(
        "DetailedDeclaration", on_delete=models.CASCADE
    )
    category = models.CharField(
        verbose_name="Position type",
        choices=CAREER_CHOICES,
        max_length=256
    )
    institution = models.CharField(max_length=200, blank=True, null=True)
    job_title = models.CharField(max_length=200, blank=True, null=True)


class DetailedDeclaration(models.Model):
    BAND_CHOICES = (
        (
            u'under \u00a3100',
            u'under \u00a3100'
        ),
        (
            u'\u00a3100- \u00a31000',
            u'\u00a3100- \u00a31000'
        ),
        (
            u'\u00a31000- \u00a32000',
            u'\u00a31000- \u00a32000'
        ),
        (
            u'\u00a32000 - \u00a35000',
            u'\u00a32000 - \u00a35000'
        ),
        (
            u'\u00a35000 - \u00a310000',
            u'\u00a35000 - \u00a310000'
        ),
        (
            u'\u00a310000 - \u00a350 000',
            u'\u00a310000 - \u00a350 000'
        ),
        (
            u'\u00a350000- \u00a3100000',
            u'\u00a350000- \u00a3100000'
        ),
        (
            u'\u00a3100000+',
            u'\u00a3100000+'
        )
    )

    dt_created = models.DateTimeField(auto_now_add=True)
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE)
    for_year = models.IntegerField()
    nothing_to_declare = models.BooleanField(default=False)

    class Meta:
        ordering = ["-for_year", "-dt_created"]

    # Consultancy categories
    consultancy = models.BooleanField(default=False)
    consultancy_band = models.CharField(
        choices=BAND_CHOICES,
        max_length=256,
        blank=True,
        null=True,
        verbose_name="Band",
    )
    pharmaceutical_companies = models.BooleanField(default=False)
    technology_companies = models.BooleanField(default=False)
    consultancy_other = models.BooleanField(
        default=False, verbose_name="other"
    )
    consultancy_details = models.TextField(
        blank=True,
        verbose_name="details"
    )

    # Academic categories
    academic = models.BooleanField(
        default=False,
        verbose_name="academic_relationships"
    )
    academic_band = models.CharField(
        choices=BAND_CHOICES,
        max_length=256,
        blank=True,
        null=True,
        verbose_name="Band",
    )
    research_grants = models.BooleanField(default=False)
    academic_other = models.BooleanField(default=False)
    academic_details = models.TextField(
        blank=True,
        verbose_name="details"
    )

    # Other work categories
    other_work = models.BooleanField(default=False)
    other_work_band = models.CharField(
        choices=BAND_CHOICES,
        max_length=256,
        blank=True,
        null=True,
        verbose_name="Band",
    )
    public_relations = models.BooleanField(default=False)
    commercial_relationships = models.BooleanField(default=False)
    media = models.BooleanField(default=False)
    other_work_details = models.TextField(
        blank=True,
        verbose_name="details"
    )

    # Financial categories
    financial = models.BooleanField(default=False)
    financial_band = models.CharField(
        choices=BAND_CHOICES,
        max_length=256,
        blank=True,
        null=True,
        verbose_name="Band",
    )
    patents_owned = models.BooleanField(
        default=False, verbose_name="patents owned/part owned"
    )
    shares = models.BooleanField(
        default=False, verbose_name="shares/stocks/company ownership"
    )
    financial_details = models.TextField(
        blank=True,
        verbose_name="details"
    )

    # Spousal/family
    spousal = models.BooleanField(
        default=False, verbose_name="spousal/family"
    )
    spousal_band = models.CharField(
        choices=BAND_CHOICES,
        max_length=256,
        blank=True,
        null=True,
        verbose_name="Band",
    )
    spousal_details = models.TextField(
        blank=True,
        verbose_name="details"
    )

    # Sponsored/educational events
    sponsored = models.BooleanField(
        default=False,
        verbose_name="sponsored/educational events"
    )
    sponsored_band = models.CharField(
        choices=BAND_CHOICES,
        max_length=256,
        blank=True,
        null=True,
        verbose_name="Band",
    )
    conferences = models.BooleanField(
        default=False
    )
    meals = models.BooleanField(
        default=False,
        verbose_name="meals/hospitality/travel"
    )
    sponsored_details = models.TextField(
        blank=True,
        verbose_name="details"
    )

    # political affiliations
    political = models.BooleanField(
        default=False,
        verbose_name="political/membership organisations of note"
    )
    political_band = models.CharField(
        choices=BAND_CHOICES,
        max_length=256,
        blank=True,
        null=True,
        verbose_name="Band",
    )
    political_details = models.TextField(
        blank=True,
        verbose_name="details"
    )

    def get_absolute_url(self):
        return reverse('doctor-detail', kwargs={'pk': self.doctor.pk})


class DeclarationV3(models.Model):
    """
    A declaration made with the current form: everything the doctor
    declared, written up as markdown when they submitted it.
    """
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(default=timezone.now)
    markdown = models.TextField()
    # For declarations converted from the old tables (doctors/convert.py):
    # which row it came from, e.g. "detaileddeclaration:12". Blank for
    # declarations made with the current form.
    migrated_from = models.CharField(
        max_length=50, blank=True, default="", verbose_name="converted from"
    )

    class Meta:
        db_table = "declaration_v3"
        ordering = ["-timestamp"]
        verbose_name = "declaration (v3)"
        verbose_name_plural = "declarations (v3)"
        constraints = [
            # Each old declaration is converted at most once.
            models.UniqueConstraint(
                fields=["migrated_from"],
                condition=~models.Q(migrated_from=""),
                name="declaration_v3_converted_once",
            ),
        ]

    def __str__(self):
        return f'{self.doctor} - {self.timestamp:%Y-%m-%d %H:%M}'

    def get_absolute_url(self):
        return reverse('doctor-detail', kwargs={'pk': self.doctor.pk})
