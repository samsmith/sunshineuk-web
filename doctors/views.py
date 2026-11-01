"""
Views relating to the register.
"""
import datetime as dt
from functools import reduce
import operator

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.urls import reverse_lazy, reverse
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import ListView, DetailView
from django.views.generic.edit import FormView, CreateView, UpdateView

from doctors import models, forms

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
    fields = '__all__'

    def get_context_data(self, **kwargs):
        data = super().get_context_data(**kwargs)

        if self.request.POST:
            data['detaileddeclarationinline'] = forms.DeclarationIdentityFormSet(
                self.request.POST
            )
        else:
            data['detaileddeclarationinline'] = forms.DeclarationIdentityFormSet()

        if self.request.POST:
            data['workdetailsinline'] = forms.WorkDetailsFormSet(
                self.request.POST
            )
        else:
            data['workdetailsinline'] = forms.WorkDetailsFormSet()

        return data

    def is_valid(self):
        form = self.get_form()
        if not form.is_valid():
            return False
        context = self.get_context_data()
        detailed_declaration = context['detaileddeclarationinline']
        work_details = context['workdetailsinline']
        if not detailed_declaration.is_valid():
            return False
        if not work_details.is_valid():
            return False
        return True

    @transaction.atomic
    def form_valid(self, form):
        context = self.get_context_data()
        detailed_declarations = context['detaileddeclarationinline']
        work_details = context['workdetailsinline']
        if not self.is_valid():
            return self.form_invalid(form)

        # The verified email is the one the link was sent to; never trust
        # an email typed into the form.
        form.instance.email = self.link.email
        self.object = form.save()
        detailed_declaration_form = detailed_declarations[0]
        detailed_declaration = detailed_declaration_form.save(commit=False)
        detailed_declaration.doctor = self.object
        detailed_declaration.save()
        for work_detail_form in work_details:
            work_detail = work_detail_form.save(commit=False)
            if work_detail_form.is_populated():
                work_detail.declaration = detailed_declaration
                work_detail.save()

        self.link.delete()
        if not settings.SKIP_EMAIL_VERIFICATION:
            self.object.send_declaration_thanks()
        return HttpResponseRedirect(self.get_success_url())


class DeclareView(AbstractDeclareView, CreateView):
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
    model = models.Doctor
    context_object_name = 'doctor'

    def get_context_data(self, *args, **kwargs):
        ctx = super().get_context_data(*args, **kwargs)
        declarations = self.object.detaileddeclaration_set.order_by(
            "-for_year"
        )
        # python 3.7 dicts maintain order!
        ctx["declarations"] = {}
        for declaration in declarations:
            if declaration.for_year not in ctx["declarations"]:
                ctx["declarations"][declaration.for_year] = []

            ctx["declarations"][declaration.for_year].append(
                declaration
            )
        return ctx


class DoctorDetailArchivedView(DetailView):
    model = models.Doctor
    context_object_name = 'doctor'
    template_name = "doctors/doctor_detail_archived.html"


class DoctorJSONView(DetailView):
    queryset = models.Doctor.objects.all()

    def get(self, *args, **kw):
        return JsonResponse(self.get_object().to_dict())


class DoctorListView(ListView):
    template_name = 'doctors/doctor_list.html'
    context_object_name = 'doctors'

    # This is dynamic to avoid the date being process-start bounded.
    def get_queryset(self):
        an_hour_ago = timezone.now() - dt.timedelta(hours=1)
        qs = models.Doctor.objects.filter(
            Q(detaileddeclaration__dt_created__lte=an_hour_ago) |
            Q(detaileddeclaration=None)
        )

        # Always the same order in the HTML (the browser shuffles it for
        # display); the id breaks ties between doctors with the same name.
        return qs.distinct().order_by(
            "name", "pk"
        )


class SearchView(ListView):
    model = models.DetailedDeclaration

    SEARCH_FIELDS = [
        "consultancy_details",
        "academic_details",
        "other_work_details",
        "financial_details",
        "spousal_details",
        "sponsored_details",
        "political_details",
        "doctor__name",
        "doctor__primary_employer",
        "doctor__gmc_number",
        "workdetails__institution"
    ]

    def get_queryset(self):
        query_term = self.request.GET.get('q', '')
        queryset = self.model.objects.all()
        q_objects = [
            Q(**{"{}__icontains".format(field): query_term})
            for field in self.SEARCH_FIELDS
        ]
        return queryset.filter(reduce(operator.or_, q_objects)).distinct()
