import logging

import django.test
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from simple_history.models import HistoricalRecords
from simple_history.utils import get_history_model_for_model

from underground_crm.contactability import get_user_by_full_name
from underground_crm.models import Person

logger = logging.getLogger(__name__)


class PersonHistoryTest(django.test.TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user_model = get_user_model()
        self.original_first_name = "Elaine"
        self.original_last_name = "Paige"
        self.staff_user = self.user_model.objects.create_user(
            email="staff@example.com",
            password="password",
            is_staff=True,
        )
        self.contact = Person.objects.create(
            email="contact@example.com",
            first_name=self.original_first_name,
            last_name=self.original_last_name,
        )

    def test_expected_history_creation_on_save(self):
        """Verify that saving a Person model instance creates a historical record."""
        initial_history_count = self.contact.history.count()
        self.assertEqual(initial_history_count, 1)

        # Modify first_name and save
        new_first_name = "Elliot"
        self.contact.first_name = new_first_name
        self.contact.save()

        self.assertEqual(
            self.contact.history.count(),
            2,
            msg="We created the person and updated the person once, so that should be 2 items total",
        )
        latest_history = self.contact.history.first()
        self.assertEqual(latest_history.first_name, new_first_name)
        self.assertEqual(latest_history.history_change_reason, None)
        found_new_user = get_user_by_full_name(f"{new_first_name} {self.original_last_name}")
        self.assertTrue(
            found_new_user, msg=f"Why couldn't we find {new_first_name} after an update?"
        )
        found_old_user = get_user_by_full_name(
            f"{self.original_first_name} {self.original_last_name}"
        )
        self.assertIsNone(
            found_old_user, msg=f"It was thought that {found_old_user} would disappear"
        )
        logging.info(
            "Verified that a change of name from « %s %s » to « %s » was persisted in history and the old version disappeared from the users table",
            self.original_first_name,
            self.original_last_name,
            found_new_user,
        )

    def test_expected_user_attribution_with_middleware_context(self):
        """Verify that edits are correctly attributed to the user via request context."""
        request = self.factory.post("/admin/underground_crm/person/", {})
        request.user = self.staff_user

        # Manually set request context to simulate middleware environment
        HistoricalRecords.context.request = request
        edited_first_name = "Whoops, Edited by Staff"
        try:
            self.contact.first_name = edited_first_name
            self.contact.save()
        finally:
            if hasattr(HistoricalRecords.context, "request"):
                del HistoricalRecords.context.request

        latest_history = self.contact.history.first()
        self.assertEqual(latest_history.first_name, edited_first_name)
        self.assertEqual(latest_history.history_user, self.staff_user)

    def test_unexpected_empty_request_user(self):
        """Verify that history tracking behaves correctly without a request context."""
        # Clean request context
        if hasattr(HistoricalRecords.context, "request"):
            del HistoricalRecords.context.request

        edited_first_name = "Edited without request context"
        self.contact.first_name = edited_first_name
        self.contact.save()

        latest_history = self.contact.history.first()
        self.assertEqual(latest_history.first_name, edited_first_name)
        self.assertIsNone(latest_history.history_user)

    def test_multiple_updates(self):
        """Verify that multiple updates result in sequential history revisions."""
        first_update = "Elliot"
        second_update = "Eleanor"
        self.contact.first_name = first_update
        self.contact.save()
        self.contact.first_name = second_update
        self.contact.save()

        history_records = list(self.contact.history.all().order_by("history_date"))
        self.assertEqual(
            len(history_records),
            3,
            msg="1 record for creation and 2 for updates should give 3 total history records",
        )
        self.assertEqual(history_records[0].first_name, self.original_first_name)
        self.assertEqual(history_records[1].first_name, first_update)
        self.assertEqual(history_records[2].first_name, second_update)

    def test_get_historical_changes_via_diff(self):
        """Verify that changes can be tracked between historical records using diffs."""
        updated_first_name = "Edward"
        self.contact.first_name = updated_first_name
        self.contact.save()

        history_records = list(self.contact.history.all().order_by("history_date"))
        self.assertEqual(
            len(history_records),
            2,
            msg="1 record for creation and 1 for the update should give 2 total history records",
        )

        delta = history_records[1].diff_against(history_records[0])
        self.assertEqual(
            len(delta.changes),
            1,
            msg="Only first_name was changed, so there should be exactly 1 diff entry",
        )
        self.assertEqual(delta.changes[0].field, "first_name")
        self.assertEqual(delta.changes[0].old, self.original_first_name)
        self.assertEqual(delta.changes[0].new, updated_first_name)

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
