import logging

import django.test
from django.core.exceptions import PermissionDenied
from django.db import transaction

from underground_crm.models import Tag

logger = logging.getLogger(__name__)


class ProtectedTagTest(django.test.TestCase):
    def test_protected_tag_cannot_be_deleted(self):
        tag = Tag.objects.create(name="Founding member", is_protected=True)
        # A raised exception inside TestCase's outer atomic block would otherwise
        # poison it for the rest of the test — an inner atomic() gives the failed
        # delete its own savepoint to roll back, without breaking the transaction
        # this test's other assertions still need to run in.
        with self.assertRaises(PermissionDenied):
            with transaction.atomic():
                tag.delete()
        self.assertTrue(
            Tag.objects.filter(pk=tag.pk).exists(),
            msg="A protected tag must still exist after a blocked delete attempt",
        )

    def test_unprotected_tag_can_be_deleted(self):
        tag = Tag.objects.create(name="Casual mailing list", is_protected=False)
        tag.delete()
        self.assertFalse(Tag.objects.filter(pk=tag.pk).exists())

    def test_volunteer_tag_is_seeded_and_protected_by_migration(self):
        volunteer_tag_name = "Volunteer"
        tag = Tag.objects.get(name=volunteer_tag_name)
        self.assertTrue(
            tag.is_protected,
            msg="The Volunteer tag is seeded by migration 0008 and protected by 0009 "
            "— every Underground CRM install should have it, unremovable via the UI",
        )
