import django.test
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from underground_crm.models.membership import Membership, MembershipType

Person = get_user_model()

APP_LABEL = "underground_crm"
MIGRATION_BEFORE_APPROVAL = "0029_alter_person_is_supporter_default"
MIGRATION_ADDING_APPROVAL = "0030_membership_is_approved"


class NewMembershipApprovalTest(django.test.TestCase):
    def test_new_membership_awaits_approval(self):
        member = Person.objects.create_user(email="priya.sharma@example.com")
        membership = Membership.objects.create(
            person=member,
            type=MembershipType.objects.create(name="Pirate Party"),
            started_at=timezone.now(),
        )
        self.assertFalse(
            membership.is_approved,
            "A membership created without saying otherwise must wait for staff " "to approve it.",
        )


class ApprovalMigrationTest(django.test.TransactionTestCase):
    """
    Migrates back to before Membership.is_approved existed, creates a
    membership there, and migrates forward again, to show that a membership
    granted before approval existed comes out approved.
    """

    def _migrate_to(self, migration_name: str):
        executor = MigrationExecutor(connection)
        target = [(APP_LABEL, migration_name)]
        executor.migrate(target)
        executor.loader.build_graph()
        return executor.loader.project_state(target).apps

    def tearDown(self):
        # Leave the schema at the latest migration for the tests that follow.
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_existing_memberships_are_approved(self):
        old_apps = self._migrate_to(MIGRATION_BEFORE_APPROVAL)
        OldPerson = old_apps.get_model(APP_LABEL, "Person")
        OldMembershipType = old_apps.get_model(APP_LABEL, "MembershipType")
        OldMembership = old_apps.get_model(APP_LABEL, "Membership")
        existing = OldMembership.objects.create(
            person=OldPerson.objects.create(email="lucas.nguyen@example.com"),
            type=OldMembershipType.objects.create(name="Pirate Party"),
            started_at=timezone.now(),
        )

        new_apps = self._migrate_to(MIGRATION_ADDING_APPROVAL)
        migrated = new_apps.get_model(APP_LABEL, "Membership").objects.get(pk=existing.pk)
        self.assertTrue(
            migrated.is_approved,
            "Memberships that existed before approval did were granted without "
            "it, so the migration must mark them approved.",
        )
