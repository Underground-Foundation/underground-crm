import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from djmoney.models.fields import MoneyField
from phonenumber_field.modelfields import PhoneNumberField

from .address import Address


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
    email = models.EmailField(unique=True)
    legacy_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text="Person ID from the previous CRM, used for data migration.",
    )
    prefix = models.CharField(max_length=10, blank=True)
    first_name = models.CharField(max_length=100, blank=True)
    middle_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    suffix = models.CharField(max_length=20, blank=True)
    legal_name = models.CharField(max_length=200, blank=True)
    preferred_name = models.CharField(max_length=100, blank=True)
    mailing_name = models.CharField(
        max_length=200, blank=True, help_text="Name as it should appear on postal correspondence."
    )
    record_type = models.SmallIntegerField(
        choices=TYPE_CHOICES,
        default=TYPE_PERSON,
        help_text="Whether this record represents an individual or an organisation.",
    )

    # --- Contact ---
    phone_number = PhoneNumberField(null=True, blank=True, region=settings.PHONE_REGION)
    mobile_number = PhoneNumberField(null=True, blank=True, region=settings.PHONE_REGION)
    mobile_opt_in = models.BooleanField(
        default=False, help_text="Person has opted in to receive SMS updates."
    )
    is_mobile_bad = models.BooleanField(
        default=False, help_text="Mobile number is known to be invalid or unreachable."
    )
    work_phone_number = PhoneNumberField(null=True, blank=True, region=settings.PHONE_REGION)
    twitter_login = models.CharField(max_length=100, blank=True)
    facebook_username = models.CharField(max_length=100, blank=True)

    # --- Addresses ---
    submitted_address = models.TextField(
        blank=True, help_text="Raw address string as submitted by the person, before geocoding."
    )
    primary_address = models.OneToOneField(
        Address,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="primary_for",
    )
    home_address = models.OneToOneField(
        Address,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="home_for",
    )
    mailing_address = models.OneToOneField(
        Address,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mailing_for",
    )
    work_address = models.OneToOneField(
        Address,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="work_for",
    )
    registered_address = models.OneToOneField(
        Address,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="registered_for",
        help_text="Address as recorded on the electoral roll.",
    )
    billing_address = models.OneToOneField(
        Address,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="billing_for",
    )

    # --- Professional ---
    website = models.URLField(blank=True)
    bio = models.TextField(blank=True)
    description = models.TextField(blank=True)

    # --- Biographical ---
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=50, blank=True)

    # --- Tags ---
    tags = models.ManyToManyField(Tag, blank=True, related_name="people")

    # --- Engagement & consent ---
    email_opt_in = models.BooleanField(
        default=False, help_text="Person has opted in to receive email updates."
    )
    unsubscribed_at = models.DateTimeField(
        null=True, blank=True, help_text="When the person unsubscribed from email."
    )
    is_supporter = models.BooleanField(default=False)
    support_level = models.SmallIntegerField(
        null=True,
        blank=True,
        choices=SUPPORT_LEVEL_CHOICES,
        help_text="Manually assigned support rating from 1 (weak) to 5 (strong).",
    )
    inferred_support_level = models.SmallIntegerField(
        null=True,
        blank=True,
        choices=SUPPORT_LEVEL_CHOICES,
        help_text="Support level derived algorithmically.",
    )
    priority_level = models.SmallIntegerField(
        null=True,
        blank=True,
        choices=PRIORITY_LEVEL_CHOICES,
        help_text="Outreach priority from 0 (lowest) to 5 (highest).",
    )
    is_volunteer = models.BooleanField(default=False)
    is_prospect = models.BooleanField(
        default=False, help_text="Being cultivated as a potential supporter but not yet confirmed."
    )
    is_deceased = models.BooleanField(default=False)

    # --- Donation summary (aggregate; individual records are in the Donation model) ---
    is_donor = models.BooleanField(default=False)
    is_fundraiser = models.BooleanField(default=False)
    donations_count = models.PositiveIntegerField(default=0)
    donations_amount = MoneyField(
        max_digits=14,
        decimal_places=2,
        default_currency="AUD",
        default=0,
        help_text="Total amount donated across all time.",
    )
    first_donated_at = models.DateTimeField(null=True, blank=True)
    last_donated_at = models.DateTimeField(null=True, blank=True)

    # --- Contact preferences ---
    do_not_call = models.BooleanField(default=False)
    do_not_contact = models.BooleanField(default=False)

    # --- Profile visibility ---
    is_profile_published = models.BooleanField(
        default=True, help_text="Whether this person's public profile is visible on the site."
    )
    activity_is_private = models.BooleanField(
        default=False, help_text="Whether this person's activity is hidden from public streams."
    )

    # --- Relationships ---
    recruiter = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="recruits",
        help_text="The person who recruited this person to join.",
    )
    point_person = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_contacts",
        help_text="Staff member or senior contact responsible for this person.",
    )

    # --- Electoral districts (Australian federal system) ---
    federal_district = models.CharField(max_length=100, blank=True)
    state_upper_district = models.CharField(max_length=100, blank=True)
    state_lower_district = models.CharField(max_length=100, blank=True)
    council_district = models.CharField(
        max_length=100, blank=True, help_text="Local government area (e.g. City of Moreland)."
    )
    ward = models.CharField(
        max_length=100,
        blank=True,
        help_text="Ward or suburb-level electoral division within the council area.",
    )

    # --- Imported data ---
    membership_number = models.CharField(
        max_length=100, blank=True, help_text="Membership number from a previous membership system."
    )

    # --- Django auth fields ---
    is_staff = models.BooleanField(
        default=False, help_text="Grants access to the Django admin interface."
    )
    is_admin = models.BooleanField(
        default=False, help_text="Grants elevated permissions within the CRM."
    )
    is_active = models.BooleanField(default=True)

    # --- Timestamps ---
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = PersonManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "person"
        verbose_name_plural = "people"
        indexes = [
            models.Index(fields=["last_name", "first_name"]),
            models.Index(fields=["email_opt_in"]),
            models.Index(fields=["is_supporter"]),
            models.Index(fields=["is_donor"]),
        ]

    def __str__(self):
        name = self.full_name
        return name if name else self.email

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def name_or_email(self):
        return self.full_name or self.email

    @property
    def first_name_or_friend(self):
        return self.first_name or "Friend"
