from djmoney.contrib.django_rest_framework import MoneyField
from rest_framework import serializers

from ..models import Address, Donation, Engagement, Interaction, PersonNote, Tag


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "name"]
        read_only_fields = ["id"]


class PersonNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = PersonNote
        fields = [
            "id",
            "person",
            "text",
            "created_by",
            "legacy_activity_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class InteractionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Interaction
        fields = [
            "id",
            "legacy_contact_id",
            "person",
            "author",
            "method",
            "note",
            "status",
            "created_at",
        ]
        read_only_fields = ["id"]


class EngagementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Engagement
        fields = [
            "id",
            "person",
            "action_type",
            "page_url",
            "page_title",
            "recorded_by",
            "created_at",
            "metadata",
        ]
        read_only_fields = ["id", "created_at"]


class DonationSerializer(serializers.ModelSerializer):
    amount = MoneyField(max_digits=14, decimal_places=2)

    class Meta:
        model = Donation
        fields = [
            "id",
            "person",
            "amount",
            "amount_currency",
            "stripe_payment_id",
            "is_recurring",
            "donated_at",
            "page_url",
            "metadata",
        ]
        read_only_fields = ["id"]


class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = [
            "id",
            "line1",
            "line2",
            "line3",
            "city",
            "state",
            "postcode",
            "country_code",
            "latitude",
            "longitude",
            "geocode_reliability",
        ]
        read_only_fields = [
            "id",
            "latitude",
            "longitude",
            "geocode_reliability",
        ]


class UnverifiedAddressSerializer(serializers.ModelSerializer):
    """Read-only address serializer for addresses that have not yet been geocoded.

    Omits geocoding fields and coerces null field values to empty strings, making
    it safe to use directly in JSON responses without null-checking on the client.
    """

    class Meta:
        model = Address
        fields = ["line1", "line2", "line3", "city", "state", "postcode", "country_code"]

    def to_representation(self, instance):
        return {k: v or "" for k, v in super().to_representation(instance).items()}
