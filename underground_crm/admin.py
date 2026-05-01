from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.template.response import TemplateResponse
from django.urls import path

from .forms.person_filter import PeopleFilterAdminForm
from .models import (
    Address,
    Donation,
    Engagement,
    Interaction,
    Membership,
    MembershipType,
    Person,
    PeopleFilter,
    PersonNote,
    Tag,
)
from .models.person import PersonTag


class SavedFilterListFilter(admin.SimpleListFilter):
    """
    Populates the Person changelist sidebar with all saved PeopleFilters.
    Selecting one applies its stored criteria to the queryset.
    """

    title = "Saved filter"
    parameter_name = "saved_filter"

    def lookups(self, request, model_admin):
        return PeopleFilter.objects.values_list("id", "name")

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        try:
            pf = PeopleFilter.objects.get(pk=self.value())
        except PeopleFilter.DoesNotExist:
            return queryset
        return pf.apply(queryset)


class PersonTagInline(admin.TabularInline):
    model = PersonTag
    extra = 1
    fields = ["tag"]


class PersonNoteInline(admin.TabularInline):
    model = PersonNote
    fk_name = "person"
    extra = 1
    fields = ["text", "created_by", "created_at"]
    readonly_fields = ["created_at"]


class InteractionInline(admin.TabularInline):
    model = Interaction
    fk_name = "person"
    extra = 0
    fields = ["method", "status", "author", "note", "created_at"]
    readonly_fields = ["created_at"]


@admin.register(Person)
class PersonAdmin(UserAdmin):
    ordering = ["email"]
    list_display = [
        "email",
        "first_name",
        "last_name",
        "is_supporter",
        "is_volunteer",
        "is_donor",
        "created_at",
    ]
    list_filter = [
        SavedFilterListFilter,
        "is_supporter",
        "is_volunteer",
        "is_donor",
        "email_opt_in",
        "is_staff",
        "is_active",
        "is_prospect",
        "is_deceased",
    ]
    search_fields = ["email", "first_name", "last_name"]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [PersonTagInline, PersonNoteInline, InteractionInline]

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            "Name",
            {
                "fields": (
                    "prefix",
                    "first_name",
                    "middle_name",
                    "last_name",
                    "suffix",
                    "legal_name",
                    "preferred_name",
                    "mailing_name",
                )
            },
        ),
        (
            "Contact",
            {
                "fields": (
                    "phone_number",
                    "mobile_number",
                    "mobile_opt_in",
                    "is_mobile_bad",
                    "work_phone_number",
                    "twitter_login",
                    "facebook_username",
                    "website",
                )
            },
        ),
        ("Address", {"fields": ("submitted_address",)}),
        ("Biography", {"fields": ("bio", "description", "date_of_birth", "gender")}),
        (
            "Engagement",
            {
                "fields": (
                    "email_opt_in",
                    "unsubscribed_at",
                    "is_supporter",
                    "support_level",
                    "inferred_support_level",
                    "priority_level",
                    "is_volunteer",
                    "is_prospect",
                    "is_deceased",
                )
            },
        ),
        (
            "Donations",
            {
                "fields": (
                    "is_donor",
                    "is_fundraiser",
                    "donations_count",
                    "donations_amount",
                    "first_donated_at",
                    "last_donated_at",
                )
            },
        ),
        (
            "Preferences",
            {
                "fields": (
                    "do_not_call",
                    "do_not_contact",
                    "is_profile_published",
                    "activity_is_private",
                )
            },
        ),
        (
            "Electoral",
            {
                "fields": (
                    "federal_district",
                    "state_upper_district",
                    "state_lower_district",
                    "council_district",
                    "ward",
                )
            },
        ),
        ("Relationships", {"fields": ("recruiter", "point_person")}),
        ("Membership", {"fields": ("membership_number",)}),
        ("Migration", {"fields": ("legacy_id",)}),
        (
            "System",
            {
                "fields": (
                    "is_staff",
                    "is_admin",
                    "is_active",
                    "is_superuser",
                    "has_html_permission",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "first_name", "last_name", "password1", "password2"),
            },
        ),
    )

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if not request.user.is_admin:
            readonly.append("has_html_permission")
        return readonly

    def save_model(self, request, obj, form, change):
        if not request.user.is_admin:
            if change:
                original = Person.objects.get(pk=obj.pk)
                obj.has_html_permission = original.has_html_permission
            else:
                obj.has_html_permission = False
        super().save_model(request, obj, form, change)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    search_fields = ["name"]


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ["line1", "city", "state", "country_code"]
    search_fields = ["line1", "city", "state", "postcode"]


@admin.register(Engagement)
class EngagementAdmin(admin.ModelAdmin):
    list_display = ["person", "action_type", "page_title", "created_at"]
    list_filter = ["action_type"]
    search_fields = ["person__email", "person__first_name", "person__last_name", "page_title"]
    readonly_fields = ["created_at"]


@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    list_display = ["person", "amount", "is_recurring", "donated_at"]
    list_filter = ["is_recurring"]
    search_fields = [
        "person__email",
        "person__first_name",
        "person__last_name",
        "stripe_payment_id",
    ]
    readonly_fields = ["donated_at"]


@admin.register(MembershipType)
class MembershipTypeAdmin(admin.ModelAdmin):
    search_fields = ["name"]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ["person", "type", "started_at", "expires_on", "suspended_at"]
    list_filter = ["type"]
    search_fields = ["person__email", "person__first_name", "person__last_name"]


@admin.register(PersonNote)
class PersonNoteAdmin(admin.ModelAdmin):
    list_display = ["person", "created_by", "created_at"]
    search_fields = ["person__email", "person__first_name", "person__last_name", "text"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(Interaction)
class InteractionAdmin(admin.ModelAdmin):
    list_display = ["person", "method", "author", "status", "created_at"]
    list_filter = ["method", "status"]
    search_fields = ["person__email", "person__first_name", "person__last_name", "note"]
    readonly_fields = ["created_at"]


@admin.register(PeopleFilter)
class PeopleFilterAdmin(admin.ModelAdmin):
    form = PeopleFilterAdminForm
    list_display = ["name", "description", "evaluation_link"]
    search_fields = ["name", "description"]
    readonly_fields = ["sql", "evaluation_link"]

    def get_fieldsets(self, request, obj=None):
        base = [(None, {"fields": ("name", "description", "criteria")})]
        if obj is not None:
            base.append((None, {"fields": ("evaluation_link",)}))
            base.append(("Generated SQL", {"fields": ("sql",)}))
        return base

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "people-filter-evaluation/<uuid:pk>/",
                self.admin_site.admin_view(self.evaluation_view),
                name="underground_crm_peoplefilter_evaluate",
            ),
        ]
        return custom + urls

    def evaluation_view(self, request, pk):
        from django.shortcuts import get_object_or_404

        people_filter = get_object_or_404(PeopleFilter, pk=pk)
        people = people_filter.apply(Person.objects.prefetch_related("tags")).order_by(
            "last_name", "first_name"
        )
        show_donations = "show-donations" in request.GET
        show_engagement = "show-engagement" in request.GET
        context = {
            **self.admin_site.each_context(request),
            "people_filter": people_filter,
            "people": people,
            "show_donations": show_donations,
            "show_engagement": show_engagement,
            "title": f"Evaluate: {people_filter.name}",
        }
        return TemplateResponse(
            request,
            "admin/underground_crm/peoplefilter/evaluate.html",
            context,
        )
