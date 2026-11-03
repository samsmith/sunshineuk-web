"""
Views relating to the register.
"""
import datetime as dt
from functools import reduce
import operator
import re

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.urls import reverse_lazy, reverse
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import ListView, DetailView
from django.views.generic.edit import FormView, CreateView, UpdateView

from doctors import convert, models, forms

# declare.js used to start the markdown with its own "## Declaration on ..."
# heading, dated by the doctor's computer. The server dates declarations now,
# but a form opened before that change can still send one, so it's dropped.
BROWSER_HEADING = re.compile(r"\A##[ \t]+Declaration\b[^\n]*\n*")

class EstablishIdentityView(FormView):
    """
    View to establish the identity of a user so they can
    edit their public record.
    """
    template_name='establish_identity.html'
    form_class = forms.IdentityForm
    success_url = '/declare/pending'

    def form_valid(self, form):
        form.create_declaration_link()
        return super(EstablishIdentityView, self).form_valid(form)


class ReEstablishIdentityView(FormView):
    """
    View to reestablish the identity of a user so they can
    edit their public record.
    """
    template_name='re_establish_identity.html'
    form_class = forms.ReEstablishIdentityForm
    success_url = reverse_lazy('re-establish-identity-pending')

    def dispatch(self, *args, **kwargs):
        self.doctor = get_object_or_404(models.Doctor, pk=kwargs['pk'])
        return super(ReEstablishIdentityView, self).dispatch(*args, **kwargs)

    def get_context_data(self, *args, **kwargs):
        context = super(ReEstablishIdentityView, self).get_context_data(*args, **kwargs)
        context['doctor'] = self.doctor
        return context

    def get_initial(self):
        return {'gmc': self.doctor.gmc_number}

    def form_valid(self, form):
        form.create_declaration_link()
        return super(ReEstablishIdentityView, self).form_valid(form)


class AbstractDeclareView():
    model = models.Doctor
    template_name = 'declare.html'
    # Shown at the top of the register page they land on after declaring.
    success_message = "Thanks, your declaration is now on the public register."
    # Section 1 of the form: the four fields of DeclareDoctorForm.
    form_class = forms.DeclareDoctorForm

    @transaction.atomic
    def form_valid(self, form):
        # Section 2: everything declared, written up as markdown by
        # declare.js. Browsers send form text with CRLF line endings, and
        # pasted text can carry NUL characters, which can't be seen and
        # which Postgres can't store.
        markdown = self.request.POST.get('declaration', '')
        markdown = markdown.replace('\r\n', '\n').replace('\x00', '').strip()
        markdown = BROWSER_HEADING.sub('', markdown).strip()
        if not markdown:
            form.add_error(
                None, "Your declarations didn't come through. Please add them again."
            )
            return self.form_invalid(form)

        # The verified email is the one the link was sent to; never trust
        # an email typed into the form.
        form.instance.email = self.link.email
        # The form no longer asks for an employment address, and the column
        # can't be null, so it's stored as an empty string.
        form.instance.employment_address = ""
        self.object = form.save()

        # Dated by the server in UK time, not by the doctor's computer, and
        # headed the same way as converted declarations.
        now = timezone.now()
        heading = convert.heading(now.astimezone(convert.UK).date())
        models.DeclarationV3.objects.create(
            doctor=self.object, timestamp=now, markdown=f"{heading}\n\n{markdown}"
        )

        self.link.delete()
        messages.success(self.request, self.success_message)
        if not settings.SKIP_EMAIL_VERIFICATION:
            self.object.send_declaration_thanks()
        return HttpResponseRedirect(self.get_success_url())


class DeclareView(AbstractDeclareView, CreateView):
    # Only doctors not yet on the register come here (see DoctorListView).
    success_message = (
        "Thanks, your declaration is now on the public register. "
        "You'll appear in the list of doctors within an hour."
    )

    def dispatch(self, *args, **kwargs):
        link = get_object_or_404(models.DeclarationLink, key=kwargs['key'])
        if link.expired:
            raise Http404
        doctor = models.Doctor.objects.filter(
            email__iexact=link.email
        ).first()
        if doctor is not None:
            kwargs['pk'] = doctor.pk
            return redirect(reverse('add', kwargs=kwargs))
        self.link = link
        return super().dispatch(*args, **kwargs)

    def get_initial(self, *args, **kwargs):
        initial = super().get_initial(*args, **kwargs)
        initial["email"] = self.link.email
        return initial


class AddDeclarationView(AbstractDeclareView, UpdateView):
    def dispatch(self, *args, **kwargs):
        link = get_object_or_404(models.DeclarationLink, key=kwargs['key'])
        if link.expired:
            raise Http404
        # The link only proves control of one email address, so it may only
        # be used to edit the doctor registered under that address.
        get_object_or_404(
            models.Doctor, pk=kwargs['pk'], email__iexact=link.email
        )
        self.link = link
        return super().dispatch(*args, **kwargs)


class DoctorDetailView(DetailView):
    # The page shows the doctor's declarations (v3) as markdown. Old ones
    # are converted to v3 from the admin (doctors/convert.py).
    model = models.Doctor
    context_object_name = 'doctor'


class DoctorListView(ListView):
    template_name = 'doctors/doctor_list.html'
    context_object_name = 'doctors'

    # This is dynamic to avoid the date being process-start bounded.
    def get_queryset(self):
        an_hour_ago = timezone.now() - dt.timedelta(hours=1)
        # A doctor's first declaration takes an hour to show up here.
        qs = models.Doctor.objects.filter(
            Q(declarationv3__timestamp__lte=an_hour_ago) |
            Q(declarationv3=None)
        )

        # Always the same order in the HTML (the browser shuffles it for
        # display); the id breaks ties between doctors with the same name.
        return qs.distinct().order_by(
            "name", "pk"
        )


class SearchView(ListView):
    model = models.DeclarationV3
    # The template kept its old name; the default for this model would be
    # doctors/declarationv3_list.html.
    template_name = 'doctors/detaileddeclaration_list.html'

    SEARCH_FIELDS = [
        "markdown",
        "doctor__name",
        "doctor__primary_employer",
        "doctor__gmc_number",
    ]

    def get_queryset(self):
        query_term = self.request.GET.get('q', '')
        queryset = self.model.objects.select_related('doctor')
        q_objects = [
            Q(**{"{}__icontains".format(field): query_term})
            for field in self.SEARCH_FIELDS
        ]
        return queryset.filter(reduce(operator.or_, q_objects)).distinct()
