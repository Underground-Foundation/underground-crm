"""
Management command to export the people who answered "yes" to a given
InputField (e.g. "Calling members") on a FormPage, to a CSV file.

Usage:
    python manage.py get_volunteers_with_field --field "Calling members" --output callers.csv

Only authenticated submissions are considered: an anonymous submission is
never attached to a Person record, so it has no bearing on this export —
see FormSubmission's docstring for why.
"""

import csv

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from underground_crm.models import InputField

CSV_FIELDNAMES = ["email", "first_name", "last_name", "mobile_number", "phone_number"]


class Command(BaseCommand):
    help = "Export authenticated people who answered yes to a given input field on a form to a CSV file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--field",
            required=True,
            help='Exact English description of the input field to export, e.g. "Calling members".',
        )
        parser.add_argument(
            "--output",
            required=True,
            help="Path to write the CSV file to.",
        )

    def handle(self, *args, **options):
        field_name = options["field"]
        output_path = options["output"]

        try:
            input_field = InputField.objects.get(description_en=field_name)
        except InputField.DoesNotExist as exc:
            available = ", ".join(InputField.objects.values_list("description_en", flat=True))
            raise CommandError(
                f'No input field described as "{field_name}" exists. '
                f"Available input fields: {available or '(none defined)'}"
            ) from exc

        Person = get_user_model()
        people = (
            Person.objects.filter(
                form_submissions__is_authenticated=True,
                form_submissions__submitted_fields__input_field=input_field,
                form_submissions__submitted_fields__has_value=True,
            )
            .distinct()
            .order_by("last_name", "first_name")
        )

        with open(output_path, "w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            count = 0
            for person in people:
                writer.writerow(
                    {
                        "email": person.email,
                        "first_name": person.first_name or "",
                        "last_name": person.last_name or "",
                        "mobile_number": person.mobile_number or "",
                        "phone_number": person.phone_number or "",
                    }
                )
                count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Exported {count} volunteer(s) for "{field_name}" to {output_path}.'
            )
        )
