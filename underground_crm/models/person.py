import uuid
from datetime import date

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from djmoney.models.fields import MoneyField
from phonenumber_field.modelfields import PhoneNumberField
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from .address import Address
from ..contactability import (
    validate_domain_name,
    validate_email_with_deliverability,
)


class Tag(models.Model):
    """A free-form label that can be applied to any person."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PersonManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email address is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_admin", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self.create_user(email, password, **extra_fields)


class Person(AbstractBaseUser, PermissionsMixin):
    """
    Central person/contact record. Serves as both the auth user model and the
    CRM contact. Email is the login credential; username is not used.
    """

    TYPE_PERSON = 0
    TYPE_ORGANISATION = 1
    TYPE_CHOICES = [
        (TYPE_PERSON, "Person"),
        (TYPE_ORGANISATION, "Organisation"),
    ]

    SUPPORT_LEVEL_CHOICES = [(i, str(i)) for i in range(1, 6)]
    PRIORITY_LEVEL_CHOICES = [(i, str(i)) for i in range(0, 6)]

    # --- Identity ---
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(
        unique=True,
        verbose_name=_("Email address"),
        validators=[validate_email_with_deliverability],
    )
    email_is_bad = models.BooleanField(
        null=True,
        blank=True,
        verbose_name=_("Email address is bad"),
        help_text=_("Can the user actually be emailed?"),
    )
    legacy_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        verbose_name=_("Legacy ID"),
        help_text=_("Person ID from the previous CRM, used for data migration."),
    )
    prefix = models.CharField(max_length=10, null=True, blank=True, verbose_name=_("Name prefix"))
    first_name = models.CharField(
        max_length=100, null=True, blank=True, verbose_name=_("First name")
    )
    middle_name = models.CharField(
        max_length=100, null=True, blank=True, verbose_name=_("Middle name")
    )
    last_name = models.CharField(max_length=100, null=True, blank=True, verbose_name=_("Last name"))
    suffix = models.CharField(max_length=20, null=True, blank=True, verbose_name=_("Name suffix"))
    legal_name = models.CharField(
        max_length=200, null=True, blank=True, verbose_name=_("Legal name")
    )
    preferred_name = models.CharField(
        max_length=100, null=True, blank=True, verbose_name=_("Preferred name")
    )
    mailing_name = models.CharField(
        max_length=200,
        null=True,
        blank=True,
        verbose_name=_("Mailing name"),
        help_text=_("Name as it should appear on postal correspondence."),
    )
    record_type = models.SmallIntegerField(
        choices=TYPE_CHOICES,
        default=TYPE_PERSON,
        verbose_name=_("Record type"),
        help_text=_("Whether this record represents an individual or an organisation."),
    )

    # --- Contact --- todo: enforce uniqueness during signup
    phone_number = PhoneNumberField(
        null=True,
        blank=True,
        region=settings.PHONE_REGION,
        db_index=True,
        verbose_name=_("Phone number"),
        help_text=_("This should only be used if the phone number is not a mobile phone."),
    )
    mobile_number = PhoneNumberField(
        null=True,
        blank=True,
        region=settings.PHONE_REGION,
        db_index=True,
        verbose_name=_("Mobile number"),
        help_text=_("This field should be used where possible."),
    )
    mobile_opt_in = models.BooleanField(
        default=False,
        verbose_name=_("Mobile opt-in"),
        help_text=_("Person has opted in to receive SMS updates."),
    )
    is_mobile_bad = models.BooleanField(
        default=False,
        verbose_name=_("Mobile number is bad"),
        help_text=_("Mobile number is known to be invalid or unreachable."),
    )
    # Work number is only here because it was in legacy systems
    work_phone_number = PhoneNumberField(
        null=True,
        blank=True,
        region=settings.PHONE_REGION,
        db_index=True,
        verbose_name=_("Work phone number"),
    )
    twitter_login = models.CharField(
        max_length=100, blank=True, null=True, verbose_name=_("Twitter login")
    )
    facebook_username = models.CharField(
        max_length=100, null=True, blank=True, verbose_name=_("Facebook username")
    )

    # --- Addresses ---
    submitted_address = models.TextField(
        null=True,
        blank=True,
        verbose_name=_("Submitted address"),
        help_text=_("Raw address string as submitted by the person, before geocoding."),
    )
    primary_address = models.OneToOneField(
        Address,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="primary_for",
        verbose_name=_("Primary address"),
    )
    home_address = models.OneToOneField(
        Address,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="home_for",
        verbose_name=_("Home address"),
        help_text=_("This shall be used for knowing when to invite them to events nearby."),
    )
    mailing_address = models.OneToOneField(
        Address,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mailing_for",
        verbose_name=_("Mailing address"),
        help_text=_("Literally just where to send things. No relation to where they live."),
    )
    registered_address = models.OneToOneField(
        Address,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="registered_for",
        verbose_name=_("Registered address"),
        help_text=_("Address as recorded on the electoral roll."),
    )
    billing_address = models.OneToOneField(
        Address,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="billing_for",
        verbose_name=_("Billing address"),
    )

    # --- Professional ---
    website = models.URLField(
        null=True,
        blank=True,
        verbose_name=_("Website"),
        validators=[validate_domain_name],
    )
    bio = models.TextField(null=True, blank=True, verbose_name=_("Biography"))
    description = models.TextField(null=True, blank=True, verbose_name=_("Description"))

    # --- Biographical ---
    date_of_birth = models.DateField(null=True, blank=True, verbose_name=_("Date of birth"))
    gender = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        verbose_name=_("Gender"),
        help_text=_(
            "This is needed for addressing people in gendered languages such as French and Spanish."
        ),
    )
    language_preferences = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        verbose_name=_("Language preferences"),
        help_text=_(
            "The languages property from their web browser: https://developer.mozilla.org/en-US/docs/Web/API/Navigator/languages"
        ),
    )

    # --- Tags ---
    tags = models.ManyToManyField(Tag, through="PersonTag", blank=True, related_name="people")

    # --- Auth M2M (explicit through models so the PK is UUID, not bigint) ---
    groups = models.ManyToManyField(
        "auth.Group",
        through="PersonGroup",
        verbose_name=_("groups"),
        blank=True,
        help_text=_(
            "The groups this user belongs to. A user will get all permissions "
            "granted to each of their groups."
        ),
        related_name="user_set",
        related_query_name="user",
    )
    user_permissions = models.ManyToManyField(
        "auth.Permission",
        through="PersonPermission",
        verbose_name=_("user permissions"),
        blank=True,
        help_text=_("Specific permissions for this user."),
        related_name="user_set",
        related_query_name="user",
    )

    # --- Engagement & consent ---
    email_opt_in = models.BooleanField(
        default=False,
        verbose_name=_("Email opt-in"),
        help_text=_("Person has opted in to receive email updates."),
    )
    unsubscribed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Unsubscribed at"),
        help_text=_("When the person unsubscribed from all emails."),
    )
    is_supporter = models.BooleanField(default=False, verbose_name=_("Is a supporter"))
    support_level = models.SmallIntegerField(
        null=True,
        blank=True,
        choices=SUPPORT_LEVEL_CHOICES,
        verbose_name=_("Support level"),
        help_text=_("Manually assigned support rating from 1 (weak) to 5 (strong)."),
    )
    inferred_support_level = models.SmallIntegerField(
        null=True,
        blank=True,
        choices=SUPPORT_LEVEL_CHOICES,
        verbose_name=_("Inferred support level"),
        help_text=_("Support level derived algorithmically."),
    )
    priority_level = models.SmallIntegerField(
        null=True,
        blank=True,
        choices=PRIORITY_LEVEL_CHOICES,
        verbose_name=_("Priority level"),
        help_text=_("Outreach priority from 0 (lowest) to 5 (highest)."),
    )
    is_volunteer = models.BooleanField(default=False, verbose_name=_("Is a volunteer"))
    is_prospect = models.BooleanField(
        default=False,
        verbose_name=_("Is a prospect"),
        help_text=_("Being cultivated as a potential supporter but not yet confirmed."),
    )
    is_deceased = models.BooleanField(default=False, verbose_name=_("Is deceased"))

    # --- Donation summary (aggregate; individual records are in the Donation model) ---
    is_donor = models.BooleanField(default=False, verbose_name=_("Is a donor"))
    is_fundraiser = models.BooleanField(default=False, verbose_name=_("Is a fundraiser"))
    donations_count = models.PositiveIntegerField(default=0, verbose_name=_("Donations count"))
    donations_amount = MoneyField(
        max_digits=14,
        decimal_places=2,
        default_currency="AUD",
        default=0,
        verbose_name=_("Donations amount"),
        help_text=_("Total amount donated across all time."),
    )
    first_donated_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_("First donated at")
    )
    last_donated_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Last donated at"))

    # --- Contact preferences ---
    do_not_call = models.BooleanField(default=False, verbose_name=_("Do not call"))
    do_not_contact = models.BooleanField(default=False, verbose_name=_("Do not contact"))

    # --- Profile visibility ---
    is_profile_published = models.BooleanField(
        default=True,
        verbose_name=_("Profile is published"),
        help_text=_("Whether this person's public profile is visible on the site."),
    )
    activity_is_private = models.BooleanField(
        default=False,
        verbose_name=_("Activity is private"),
        help_text=_("Whether this person's activity is hidden from public streams."),
    )

    # --- Relationships ---
    recruiter = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="recruits",
        verbose_name=_("Recruiter"),
        help_text=_("The person who recruited this person to join."),
    )
    point_person = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_contacts",
        verbose_name=_("Point person"),
        help_text=_("Staff member or senior contact responsible for this person."),
    )

    # --- Electoral districts (Australian federal system) ---
    federal_district = models.CharField(
        max_length=100, null=True, blank=True, verbose_name=_("Federal district")
    )
    state_upper_district = models.CharField(
        max_length=100, null=True, blank=True, verbose_name=_("State upper district")
    )
    state_lower_district = models.CharField(
        max_length=100, null=True, blank=True, verbose_name=_("State lower district")
    )
    council_district = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name=_("Council district"),
        help_text=_("Local government area (e.g. City of Moreland)."),
    )
    ward = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name=_("Ward"),
        help_text=_("Ward or suburb-level electoral division within the council area."),
    )

    # --- Imported data ---
    membership_number = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name=_("Membership number"),
        help_text=_("Membership number from a previous membership system."),
    )

    # --- Django auth fields ---
    is_staff = models.BooleanField(
        default=False,
        verbose_name=_("Is staff"),
        help_text=_("Grants access to the Django admin interface."),
    )
    is_admin = models.BooleanField(
        default=False,
        verbose_name=_("Is admin"),
        help_text=_("Grants elevated permissions within the CRM."),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("Is active"),
        help_text=_("Inactive users are treated as tombstones."),
    )
    has_html_permission = models.BooleanField(
        default=False,
        verbose_name=_("Has HTML permission"),
        help_text=_("Does this user have permission to create raw HTML for web pages?"),
    )

    # --- Timestamps ---
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Created at"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Updated at"))

    objects = PersonManager()
    history = HistoricalRecords(inherit=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = _("person")
        verbose_name_plural = _("people")
        indexes = [
            models.Index(fields=["last_name", "first_name"]),
            models.Index(fields=["email_opt_in"]),
            models.Index(fields=["is_supporter"]),
            models.Index(fields=["is_donor"]),
        ]

    def __str__(self):
        name = self.full_name
        if name and self.email:
            return f"{name} ({self.email})"
        else:
            # Email address is required
            return self.email

    def clean(self):
        super().clean()
        if self.is_admin and not self.is_staff:
            raise ValidationError(
                f"Admins should always be staff members. This is not the case for {self}"
            )

    @property
    def full_name(self):
        return f"{self.first_name or ''} {self.last_name or ''}".strip()

    @property
    def name_or_email(self):
        return self.full_name or self.email

    @property
    def first_name_or_friend(self):
        return self.first_name or "Friend"

    @property
    def age(self) -> int | None:
        if self.date_of_birth is None:
            return None
        today = date.today()
        return (
            today.year
            - self.date_of_birth.year
            - ((today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day))
        )

    def _parse_language_preferences(self) -> list[str]:
        """Return language tags from the browser string, ordered by preference (highest q first)."""
        if not self.language_preferences:
            return []
        entries: list[tuple[str, float]] = []
        for part in self.language_preferences.split(","):
            part = part.strip()
            if not part:
                continue
            if ";q=" in part:
                tag, q_str = part.split(";q=", 1)
                try:
                    q = float(q_str)
                except ValueError:
                    q = 0.0
            else:
                tag, q = part, 1.0
            entries.append((tag.strip(), q))
        entries.sort(key=lambda x: x[1], reverse=True)
        return [tag for tag, _ in entries]

    @property
    def preferred_language(self) -> str:
        languages = self._parse_language_preferences()
        return languages[0] if languages else "en-AU"

    def language_count(self) -> int:
        return len(self._parse_language_preferences())

    @property
    def location(self):
        # Use this for eg showing their location on a map
        return (
            self.home_address
            or self.registered_address
            or self.billing_address
            or self.mailing_address
        )

    @property
    def latest_engagement(self):
        return self.engagements.first()  # pylint: disable=no-member


class PersonTag(models.Model):
    """Explicit through model for Person.tags, carrying a UUID PK for federation support."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person = models.ForeignKey(Person, on_delete=models.CASCADE)
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE)

    class Meta:
        verbose_name = "tag"
        verbose_name_plural = "tags"
        db_table = "underground_crm_person_tags"
        unique_together = [("person", "tag")]


class PersonGroup(models.Model):
    """Explicit through model for Person.groups, carrying a UUID PK for federation support."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person = models.ForeignKey(Person, on_delete=models.CASCADE)
    group = models.ForeignKey("auth.Group", on_delete=models.CASCADE)

    class Meta:
        db_table = "underground_crm_person_groups"
        unique_together = [("person", "group")]


class PersonPermission(models.Model):
    """Explicit through model for Person.user_permissions, carrying a UUID PK for federation support."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person = models.ForeignKey(Person, on_delete=models.CASCADE)
    permission = models.ForeignKey("auth.Permission", on_delete=models.CASCADE)

    class Meta:
        db_table = "underground_crm_person_user_permissions"
        unique_together = [("person", "permission")]
