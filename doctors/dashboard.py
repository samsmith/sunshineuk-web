"""
The Sunshine UK admin site. Its home page is a dashboard for the team:

  a) make a declaration link for any email address, lasting 3 months, without
     emailing it;
  b) the links made in the last 2 months that haven't been used, with a
     button to copy each one;
  c) the 10 newest declarations, each with a delete button;
  d) find a doctor by name, email, GMC number or anything in a declaration,
     see everything they've declared, and delete them.

Template: templates/admin/sunshine_index.html.
"""
import calendar
import re
from email.utils import parseaddr
from urllib.parse import urlencode

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from doctors import models

LINK_MONTHS = 3          # how long a link made here lasts
RECENT_LINK_MONTHS = 2   # how far back the list of unused links goes
LATEST_DECLARATIONS = 10
MAX_FOUND = 20


def months_from(when, months):
    """The same day `months` calendar months on (or back), kept within the month."""
    month_index = when.month - 1 + months
    year, month = when.year + month_index // 12, month_index % 12 + 1
    day = min(when.day, calendar.monthrange(year, month)[1])
    return when.replace(year=year, month=month, day=day)


def summary(markdown):
    """
    One line for a declaration: the sections it has, e.g. "Main employment,
    Consultancy", plus "Nothing to declare." where it says so.
    """
    sections = re.findall(r"^### (.+)$", markdown, re.M)
    nothing = re.findall(r"^(Nothing (?:else )?to declare\.)$", markdown, re.M)
    return ", ".join(sections + nothing) or "—"


def dashboard_url(q=""):
    url = reverse("admin:index")
    return f"{url}?{urlencode({'q': q})}" if q else url


class SunshineAdminSite(admin.AdminSite):
    index_template = "admin/sunshine_index.html"

    def get_urls(self):
        return [
            path(
                "dashboard/new-link/",
                self.admin_view(require_POST(self.new_link_view)),
                name="dashboard_new_link",
            ),
            path(
                "dashboard/declarations/<int:pk>/delete/",
                self.admin_view(require_POST(self.delete_declaration_view)),
                name="dashboard_delete_declaration",
            ),
        ] + super().get_urls()

    def model_admin(self, model):
        return self._registry[model]

    # ---------- the home page ----------

    def index(self, request, extra_context=None):
        links = self.model_admin(models.DeclarationLink)
        declarations = self.model_admin(models.DeclarationV3)
        doctors = self.model_admin(models.Doctor)
        now = timezone.now()
        q = request.GET.get("q", "").strip()

        context = {
            "can_make_links": links.has_add_permission(request)
                              and links.has_change_permission(request),
            "can_see_links": links.has_view_or_change_permission(request),
            "can_see_declarations": declarations.has_view_or_change_permission(request),
            "can_delete_declarations": declarations.has_delete_permission(request),
            "can_see_doctors": doctors.has_view_or_change_permission(request),
            "can_delete_doctors": doctors.has_delete_permission(request),
            "link_months": LINK_MONTHS,
            "recent_link_months": RECENT_LINK_MONTHS,
            "recent_links": models.DeclarationLink.objects.filter(
                issued__gte=months_from(now, -RECENT_LINK_MONTHS)
            ).order_by("-issued", "-pk"),
            "just_made": request.GET.get("made", ""),
            "latest_declarations": [
                self._with_summary(d)
                for d in models.DeclarationV3.objects.select_related("doctor")[:LATEST_DECLARATIONS]
            ],
            "q": q,
        }
        if q:
            found = models.Doctor.objects.filter(
                Q(name__icontains=q) | Q(email__icontains=q) | Q(gmc_number__icontains=q)
                | Q(declarationv3__markdown__icontains=q)
            ).distinct().order_by("name", "pk")
            context["found_count"] = found.count()
            context["found"] = found.prefetch_related("declarationv3_set")[:MAX_FOUND]
        context.update(extra_context or {})
        return super().index(request, context)

    @staticmethod
    def _with_summary(declaration):
        declaration.summary = summary(declaration.markdown)
        return declaration

    # ---------- a) make a link ----------

    def new_link_view(self, request):
        """
        Make (or renew) the declaration link for an email address, which can
        be written "Fred Bloggs <f.bloggs@example.com>". Any address is
        allowed, NHS or not. Nothing is emailed.
        """
        model_admin = self.model_admin(models.DeclarationLink)
        if not (model_admin.has_add_permission(request)
                and model_admin.has_change_permission(request)):
            raise PermissionDenied

        typed = request.POST.get("email", "").strip()
        name, address = parseaddr(typed)
        address = address.strip().lower()
        try:
            validate_email(address)
        except ValidationError:
            messages.error(
                request,
                f"“{typed}” isn't an email address. Enter one like "
                f"f.bloggs@example.com or Fred Bloggs <f.bloggs@example.com>.",
            )
            return redirect(dashboard_url())

        now = timezone.now()
        expires = months_from(now, LINK_MONTHS)
        link = models.DeclarationLink.objects.filter(email__iexact=address).first()
        if link is None:
            link = models.DeclarationLink.objects.create(email=address, expires=expires, issued=now)
            model_admin.log_addition(request, link, [{"added": {}}])
            done = "made"
        else:
            # Keep the key, so a link already sent to them works again too.
            link.expires, link.issued = expires, now
            link.save(update_fields=["expires", "issued"])
            model_admin.log_change(request, link, [{"changed": {"fields": ["Expires", "Issued"]}}])
            done = "renewed"

        who = f"{name} <{address}>" if name else address
        messages.success(
            request,
            f"Link {done} for {who}. It works until {expires.day} {expires:%B %Y}. "
            f"Nothing has been emailed: copy it from the list below.",
        )
        return redirect(f"{reverse('admin:index')}?{urlencode({'made': link.pk})}#links")

    # ---------- c) and d) delete one declaration ----------

    def delete_declaration_view(self, request, pk):
        declaration = get_object_or_404(models.DeclarationV3.objects.select_related("doctor"), pk=pk)
        model_admin = self.model_admin(models.DeclarationV3)
        if not model_admin.has_delete_permission(request, declaration):
            raise PermissionDenied

        made = declaration.timestamp
        model_admin.log_deletions(request, [declaration])
        declaration.delete()
        messages.success(
            request,
            f"Deleted {declaration.doctor.name}’s declaration of {made.day} {made:%B %Y}.",
        )
        return redirect(dashboard_url(request.POST.get("q", "")))
