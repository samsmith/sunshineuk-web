"""
Admin config for Doctors app.
"""
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import path
from django.views.decorators.http import require_POST
from reversion.admin import VersionAdmin

from doctors import convert, models

class PharmaBenefitInline(admin.StackedInline):
    model = models.PharmaBenefit

class OtherMedicalBenefitInline(admin.StackedInline):
    model = models.OtherMedicalBenefit

class FeeBenefitInline(admin.StackedInline):
    model = models.FeeBenefit

class GrantBenefitInline(admin.StackedInline):
    model = models.GrantBenefit

class WorkDetailsInline(admin.StackedInline):
    model = models.WorkDetails

class DoctorAdmin(VersionAdmin):
    def response_delete(self, request, obj_display, obj_id):
        # Deleting from the dashboard's search results goes back there.
        response = super().response_delete(request, obj_display, obj_id)
        if request.GET.get("from") == "dashboard":
            return redirect("admin:index")
        return response

class DeclarationAdmin(VersionAdmin):
    inlines = (PharmaBenefitInline, OtherMedicalBenefitInline,
               FeeBenefitInline, GrantBenefitInline)
    list_display = ['doctor', 'dt_created']
    search_fields = ['doctor__name']
    list_filter = ['doctor__name']

class DeclarationLinkAdmin(admin.ModelAdmin):
    search_fields = ['email']
    list_display = ['email', 'issued', 'expires', 'key']
    ordering = ['-issued']


class DetailedDeclarationAdmin(VersionAdmin):
    inlines = (WorkDetailsInline,)
    search_fields = ['doctor__name']

class DeclarationV3Admin(VersionAdmin):
    list_display = ['doctor', 'timestamp', 'migrated_from']
    search_fields = ['doctor__name', 'markdown']
    readonly_fields = ['migrated_from']
    # Adds the "Convert old declarations" button.
    change_list_template = 'admin/doctors/declarationv3/change_list.html'

    def get_urls(self):
        return [
            path(
                'convert-old/',
                self.admin_site.admin_view(require_POST(self.convert_old_view)),
                name='doctors_declarationv3_convert_old',
            ),
        ] + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        extra_context = {
            **(extra_context or {}),
            'old_declarations_to_convert': convert.pending_count(),
        }
        return super().changelist_view(request, extra_context)

    def convert_old_view(self, request):
        """
        Turn every old declaration (DetailedDeclaration and Declaration)
        not converted yet into a markdown declaration, keeping its date.
        """
        if not self.has_add_permission(request):
            raise PermissionDenied
        converted = convert.convert_old_declarations()
        self.message_user(
            request,
            f"Converted {converted} old declaration{'' if converted == 1 else 's'} to markdown.",
            messages.SUCCESS,
        )
        return redirect('admin:doctors_declarationv3_changelist')

admin.site.register(models.DeclarationV3, DeclarationV3Admin)
admin.site.register(models.DetailedDeclaration, DetailedDeclarationAdmin)
admin.site.register(models.Doctor, DoctorAdmin)
admin.site.register(models.Declaration, DeclarationAdmin)
admin.site.register(models.DeclarationLink, DeclarationLinkAdmin)
