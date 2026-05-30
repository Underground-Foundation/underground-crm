import logging
import unittest
from string import whitespace, punctuation

logger = logging.getLogger(__name__)


class PersonTest(unittest.TestCase):
    def test_admin_requires_staff(self):
        from django.core.exceptions import ValidationError
        from underground_crm.models import Person

        person = Person(email="admin.notstaff@example.com", is_admin=True, is_staff=False)
        thrown = False
        try:
            person.clean()
        except ValidationError as e:
            thrown = True
            words = str(e).lower().strip(whitespace + punctuation).split(" ")
            logger.info("A validation error was thrown (as expected): %s", words)
            self.assertTrue(
                ("admin" in words) or ("admins" in words),
                msg=f"It was expected that the problem here would be the inconsistent use of is_admin and is_staff, but actually this error seems to be due to something else: {e}",
            )
            self.assertTrue(
                ("staff" in words) or ("is_staff" in words),
                msg=f"It was expected that the problem here would be the inconsistent use of is_admin and is_staff, but actually this error seems to be due to something else: {e}",
            )
        self.assertTrue(
            thrown,
            msg="It was expected that the inconsistency of is_admin and is_staff would cause an exception to be thrown",
        )
