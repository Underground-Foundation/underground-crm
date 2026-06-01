import django.test
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from simple_history.models import HistoricalRecords
from simple_history.utils import get_history_model_for_model
from underground_crm.models import Person


class PersonHistoryTest(django.test.TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user_model = get_user_model()
        self.staff_user = self.user_model.objects.create_user(
            email="staff@example.com",
            password="password",
            is_staff=True,
        )
        self.contact = Person.objects.create(
            email="contact@example.com",
            first_name="Original",
            last_name="Name",
        )

    def test_expected_history_creation_on_save(self):
        """Verify that saving a Person model instance creates a historical record."""
        initial_history_count = self.contact.history.count()
        self.assertEqual(initial_history_count, 1)

        # Modify first_name and save
        self.contact.first_name = "Updated"
        self.contact.save()

        self.assertEqual(self.contact.history.count(), 2)
        latest_history = self.contact.history.first()
        self.assertEqual(latest_history.first_name, "Updated")
        self.assertEqual(latest_history.history_change_reason, None)

    def test_expected_user_attribution_with_middleware_context(self):
        """Verify that edits are correctly attributed to the user via request context."""
        request = self.factory.post("/admin/underground_crm/person/", {})
        request.user = self.staff_user

        # Manually set request context to simulate middleware environment
        HistoricalRecords.context.request = request
        try:
            self.contact.first_name = "Edited by Staff"
            self.contact.save()
        finally:
            if hasattr(HistoricalRecords.context, "request"):
                del HistoricalRecords.context.request

        latest_history = self.contact.history.first()
        self.assertEqual(latest_history.first_name, "Edited by Staff")
        self.assertEqual(latest_history.history_user, self.staff_user)

    def test_unexpected_empty_request_user(self):
        """Verify that history tracking behaves correctly without a request context."""
        # Clean request context
        if hasattr(HistoricalRecords.context, "request"):
            del HistoricalRecords.context.request

        self.contact.first_name = "Edited without request context"
        self.contact.save()

        latest_history = self.contact.history.first()
        self.assertEqual(latest_history.first_name, "Edited without request context")
        self.assertIsNone(latest_history.history_user)

    def test_unexpected_duplicate_history_keys(self):
        """Verify that multiple updates result in sequential history revisions."""
        self.contact.first_name = "Update 1"
        self.contact.save()
        self.contact.first_name = "Update 2"
        self.contact.save()

        history_records = list(self.contact.history.all().order_by("history_date"))
        # 1 for creation, 2 for updates = 3 records
        self.assertEqual(len(history_records), 3)
        self.assertEqual(history_records[0].first_name, "Original")
        self.assertEqual(history_records[1].first_name, "Update 1")
        self.assertEqual(history_records[2].first_name, "Update 2")

    def test_get_historical_changes_via_diff(self):
        """Verify that changes can be tracked between historical records using diffs."""
        self.contact.first_name = "Changed"
        self.contact.save()

        history_records = list(self.contact.history.all().order_by("history_date"))
        self.assertEqual(len(history_records), 2)

        delta = history_records[1].diff_against(history_records[0])
        self.assertEqual(len(delta.changes), 1)
        self.assertEqual(delta.changes[0].field, "first_name")
        self.assertEqual(delta.changes[0].old, "Original")
        self.assertEqual(delta.changes[0].new, "Changed")

    def test_history_retained_on_delete(self):
        """Verify that when a Person is deleted, their historical records are retained for auditing."""
        contact_id = self.contact.id
        self.contact.delete()
        history_model = get_history_model_for_model(Person)
        # Verify that historical records are retained (audit/no-loss rule)
        self.assertGreater(history_model.objects.filter(id=contact_id).count(), 0)
        latest_history = (
            history_model.objects.filter(id=contact_id).order_by("-history_date").first()
        )
        self.assertEqual(latest_history.history_type, "-")
