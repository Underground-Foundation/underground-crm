"""Tests for name-title (Person.prefix) cleaning.

Covers the pure recogniser in underground_crm.person_titles, the Person.save()
path that enforces it, and the CSV importer's warn-and-drop behaviour for a
title it does not recognise.
"""

import csv
import logging
import os
import tempfile

import django.test
from django.contrib.auth import get_user_model
from django.core.management import call_command

from underground_crm.person_titles import InvalidNamePrefixError, clean_name_prefix

Person = get_user_model()


class CleanNamePrefixTest(django.test.SimpleTestCase):
    """Unit tests for clean_name_prefix — no database required."""

    def test_none_and_blank_return_none(self):
        for raw in (None, "", "   ", "\t\n"):
            with self.subTest(raw=raw):
                self.assertIsNone(clean_name_prefix(raw))

    def test_canonicalises_case_and_punctuation(self):
        cases = {
            "Mr": "Mr",
            "mr": "Mr",
            "MR": "Mr",
            "Mr.": "Mr",
            " mrs. ": "Mrs",
            "MRS": "Mrs",
            "ms": "Ms",
            "Miss": "Miss",
            "mx": "Mx",
            "dr,": "Dr",
            "PROF.": "Prof",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(clean_name_prefix(raw), expected)

    def test_multi_word_title_each_word_canonicalised(self):
        # "Hon Dr" is METEOR's own worked example of a repeatable name title:
        # https://meteor.aihw.gov.au/content/453731
        self.assertEqual(clean_name_prefix("Hon Dr"), "Hon Dr")
        self.assertEqual(clean_name_prefix("hon  dr."), "Hon Dr")
        self.assertEqual(clean_name_prefix("  REV   DR  "), "Rev Dr")

    def test_rejects_non_title(self):
        with self.assertRaises(InvalidNamePrefixError) as ctx:
            clean_name_prefix("Citizen")
        self.assertEqual(ctx.exception.bad_words, ["Citizen"])

    def test_rejects_when_any_word_is_not_a_title(self):
        with self.assertRaises(InvalidNamePrefixError) as ctx:
            clean_name_prefix("Mr Blobby")
        self.assertEqual(ctx.exception.bad_words, ["Blobby"])

    def test_rejects_punctuation_only_word(self):
        with self.assertRaises(InvalidNamePrefixError):
            clean_name_prefix("Mr -")

    def test_error_is_a_valueerror(self):
        self.assertTrue(issubclass(InvalidNamePrefixError, ValueError))

    def test_nameparser_set_is_permissive_by_design(self):
        # Documents a deliberate limitation: nameparser's title set exists to
        # strip leading role words, so "associate" and "professor" are members
        # and, with no length limit, "Associate Professor" is accepted.
        self.assertEqual(clean_name_prefix("Associate Professor"), "Associate Professor")

    def test_max_length_rejects_result_that_would_not_fit(self):
        with self.assertRaises(InvalidNamePrefixError) as ctx:
            clean_name_prefix("Associate Professor", max_length=10)
        self.assertEqual(ctx.exception.bad_words, [])
        self.assertIn("does not fit", str(ctx.exception))

    def test_max_length_allows_result_that_fits(self):
        self.assertEqual(clean_name_prefix("prof", max_length=4), "Prof")


class PersonSavePrefixTest(django.test.TestCase):
    """Person.save() normalises a good prefix and raises on a bad one."""

    def test_save_normalises_prefix(self):
        person = Person.objects.create_user(email="a@example.org", prefix="mrs.")
        person.refresh_from_db()
        self.assertEqual(person.prefix, "Mrs")

    def test_save_accepts_multi_word_prefix(self):
        person = Person.objects.create_user(email="b@example.org", prefix="hon dr")
        person.refresh_from_db()
        self.assertEqual(person.prefix, "Hon Dr")

    def test_blank_prefix_saved_as_none(self):
        person = Person.objects.create_user(email="c@example.org", prefix="  ")
        person.refresh_from_db()
        self.assertIsNone(person.prefix)

    def test_save_raises_on_non_title(self):
        with self.assertRaises(InvalidNamePrefixError):
            Person.objects.create_user(email="d@example.org", prefix="Citizen")

    def test_save_raises_when_prefix_would_not_fit_the_field(self):
        # Every word is a recognised title, but the normalised result is far
        # longer than Person.prefix — save() passes the field's own max_length
        # to the cleaner, so this fails loudly rather than at the database.
        with self.assertRaises(InvalidNamePrefixError):
            Person.objects.create_user(
                email="e@example.org", prefix="Professor Professor Professor"
            )

    def test_updating_existing_person_revalidates_prefix(self):
        person = Person.objects.create_user(email="f@example.org", prefix="Dr")
        person.prefix = "Nonsense"
        with self.assertRaises(InvalidNamePrefixError):
            person.save()


class ImportPeopleCsvPrefixTest(django.test.TestCase):
    """The CSV importer warns and drops an unrecognised title, keeping the person."""

    _LEGACY_ID = "91001"

    def _write_csv(self, **overrides) -> str:
        row = {
            "nationbuilder_id": self._LEGACY_ID,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.org",
        }
        row.update(overrides)
        fd, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(row.keys()))
            writer.writeheader()
            writer.writerow(row)
        self.addCleanup(os.remove, path)
        return path

    def test_unrecognised_title_is_dropped_with_a_warning(self):
        csv_path = self._write_csv(prefix="Citizen")

        with self.assertLogs(
            "underground_crm.management.commands.import_people_csv", level=logging.WARNING
        ) as captured:
            call_command("import_people_csv", csv_path)

        person = Person.objects.get(legacy_id=int(self._LEGACY_ID))
        self.assertIsNone(
            person.prefix, msg="An unrecognised title must not be stored on the person"
        )
        self.assertTrue(
            any("unrecognised name title" in line for line in captured.output),
            msg=captured.output,
        )

    def test_recognised_title_is_normalised_on_import(self):
        csv_path = self._write_csv(prefix="mrs.")

        call_command("import_people_csv", csv_path)

        person = Person.objects.get(legacy_id=int(self._LEGACY_ID))
        self.assertEqual(person.prefix, "Mrs")
