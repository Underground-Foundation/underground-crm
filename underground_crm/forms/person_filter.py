import json

from django import forms

from underground_crm.models.filter import FIELD_OPERATORS, PERSON_FILTER_FIELDS


class QueryBuilderWidget(forms.Textarea):
    """
    Replaces the raw JSONField textarea with a visual AND/OR query builder.

    The textarea is hidden; JavaScript reads its value on page load to
    pre-populate the builder, then serialises the builder state back into
    the textarea on form submit so Django's normal form machinery saves it.
    """

    class Media:
        css = {"all": ("underground_crm/css/query_builder.css",)}
        js = ("underground_crm/js/query_builder.js",)

    def build_attrs(self, base_attrs, extra_attrs=None):
        attrs = super().build_attrs(base_attrs, extra_attrs)
        attrs["class"] = (attrs.get("class") or "") + " query-builder-source"
        attrs["style"] = "display:none"
        attrs["data-fields"] = json.dumps(
            [{"id": f[0], "label": f[1], "type": f[2]} for f in PERSON_FILTER_FIELDS]
        )
        attrs["data-operators"] = json.dumps(
            {
                ftype: [
                    {"id": op_id, "label": label, "has_value": has_value}
                    for op_id, label, has_value in ops
                ]
                for ftype, ops in FIELD_OPERATORS.items()
            }
        )
        return attrs


class PeopleFilterAdminForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from underground_crm.models.filter import PeopleFilter

        current_pk = self.instance.pk if self.instance and self.instance.pk else None
        qs = PeopleFilter.objects.all()
        if current_pk:
            qs = qs.exclude(pk=current_pk)
        people_filters = [{"id": str(f.pk), "label": f.name} for f in qs]
        widget = QueryBuilderWidget(attrs={"rows": 6})
        widget.attrs["data-people-filters"] = json.dumps(people_filters)
        self.fields["criteria"].widget = widget
