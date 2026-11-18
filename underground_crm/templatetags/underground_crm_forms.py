from django import forms, template
from django.utils.safestring import SafeString

register = template.Library()


@register.filter
def is_checkbox(field: forms.BoundField) -> bool:
    return isinstance(field.field.widget, forms.CheckboxInput)


@register.filter
def is_option_group(field: forms.BoundField) -> bool:
    """Whether the field is answered by picking from a group of radio buttons
    or checkboxes (CheckboxSelectMultiple is a kind of RadioSelect)."""
    return isinstance(field.field.widget, forms.RadioSelect)


@register.filter
def bootstrap_widget(field: forms.BoundField) -> SafeString:
    """
    Renders a bound field's widget with the Bootstrap classes that
    underground_crm/auth/login_with_oauth_or_form.html writes by hand, for forms
    whose markup underground-crm does not write itself, such as django-allauth's.
    """
    css_classes: list[str] = field.field.widget.attrs.get("class", "").split()
    css_classes.append("custom-control-input" if is_checkbox(field) else "form-control")
    if field.errors:
        css_classes.append("is-invalid")
    return field.as_widget(attrs={"class": " ".join(css_classes)})
