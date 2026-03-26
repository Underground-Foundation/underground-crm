from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Address, Donation, Engagement, Interaction, Membership, MembershipType, Person, PersonNote, Tag


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
    list_display = ["email", "first_name", "last_name", "is_supporter", "is_volunteer", "is_donor", "created_at"]
    list_filter = ["is_supporter", "is_volunteer", "is_donor", "email_opt_in", "is_staff", "is_active", "is_prospect", "is_deceased"]
    search_fields = ["email", "first_name", "last_name"]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [PersonNoteInline, InteractionInline]

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Name", {"fields": ("prefix", "first_name", "middle_name", "last_name", "suffix", "legal_name", "preferred_name", "mailing_name")}),
        ("Contact", {"fields": ("phone_number", "mobile_number", "mobile_opt_in", "is_mobile_bad", "work_phone_number", "twitter_login", "facebook_username", "website")}),
        ("Address", {"fields": ("submitted_address",)}),
        ("Biography", {"fields": ("bio", "description", "date_of_birth", "gender")}),
        ("Engagement", {"fields": ("email_opt_in", "unsubscribed_at", "is_supporter", "support_level", "inferred_support_level", "priority_level", "is_volunteer", "is_prospect", "is_deceased")}),
        ("Donations", {"fields": ("is_donor", "is_fundraiser", "donations_count", "donations_amount", "first_donated_at", "last_donated_at")}),
        ("Preferences", {"fields": ("do_not_call", "do_not_contact", "is_profile_published", "activity_is_private")}),
        ("Electoral", {"fields": ("federal_district", "state_upper_district", "state_lower_district", "council_district", "ward")}),
        ("Relationships", {"fields": ("recruiter", "point_person")}),
        ("Membership", {"fields": ("membership_number",)}),
        ("Migration", {"fields": ("legacy_id",)}),
        ("System", {"fields": ("is_staff", "is_admin", "is_active", "is_superuser", "groups", "user_permissions")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "first_name", "last_name", "password1", "password2"),
        }),
    )


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
    search_fields = ["person__email", "person__first_name", "person__last_name", "stripe_payment_id"]
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
