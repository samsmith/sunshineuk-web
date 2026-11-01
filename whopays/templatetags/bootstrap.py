"""
Render Django forms and fields as Bootstrap 3 markup: ``{{ form|bootstrap }}``.

This replaces the unmaintained django-bootstrap-form package (MIT licence,
(c) Tzang-Ming Shih and contributors), which no longer imports on Python 3.12+
because it uses ``distutils``. Only the plain ``bootstrap`` filter is ported,
as it is the only one this site uses; the markup is unchanged.
"""
from django import forms, template
from django.template.loader import get_template

register = template.Library()

MARKUP_CLASSES = {'label': '', 'value': '', 'single_value': ''}


@register.filter
def bootstrap(element):
    """
    Render a form or a single bound field with Bootstrap 3 markup.
    """
    if isinstance(element, forms.BoundField):
        add_input_classes(element)
        template_name = 'forms/field.html'
        context = {
            'field': element,
            'classes': MARKUP_CLASSES,
            'form': element.form,
        }
    else:
        for field in element.visible_fields():
            add_input_classes(field)
        template_name = 'forms/form.html'
        context = {'form': element, 'classes': MARKUP_CLASSES}

    return get_template(template_name).render(context)


def add_input_classes(field):
    if not (is_checkbox(field) or is_multiple_checkbox(field)
            or is_radio(field) or is_file(field)):
        classes = field.field.widget.attrs.get('class', '')
        if 'form-control' not in classes.split():
            field.field.widget.attrs['class'] = classes + ' form-control'


@register.filter
def is_checkbox(field):
    return isinstance(field.field.widget, forms.CheckboxInput)


@register.filter
def is_multiple_checkbox(field):
    return isinstance(field.field.widget, forms.CheckboxSelectMultiple)


@register.filter
def is_radio(field):
    return isinstance(field.field.widget, forms.RadioSelect)


@register.filter
def is_file(field):
    return isinstance(field.field.widget, forms.FileInput)
