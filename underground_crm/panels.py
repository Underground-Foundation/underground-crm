from wagtail.admin.panels import Panel


class ReadOnlyPanel(Panel):
    """Displays a model property or method result as a non-editable field in the Wagtail admin.

    Unlike FieldPanel, this does not require a database field — any attribute or
    zero-argument method on the model instance is accepted.
    """

    def __init__(self, attr: str, **kwargs) -> None:
        self.attr = attr
        if "heading" not in kwargs:
            kwargs["heading"] = attr.replace("_", " ").capitalize()
        super().__init__(**kwargs)

    def clone_kwargs(self) -> dict:
        return {"attr": self.attr, **super().clone_kwargs()}

    @property
    def clean_name(self) -> str:
        return super().clean_name or self.attr

    class BoundPanel(Panel.BoundPanel):
        template_name = "underground_crm/panels/read_only_panel.html"

        def get_context_data(self, parent_context=None) -> dict:
            context = super().get_context_data(parent_context)
            try:
                value = getattr(self.instance, self.panel.attr, None)
                if callable(value):
                    value = value()
            except Exception:
                value = None
            context["value"] = value
            return context
