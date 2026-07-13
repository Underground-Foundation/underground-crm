"""
Management command to export the people who answered "yes" to a given input
block (e.g. a "Calling members" checkbox) on a FormPage, to a CSV file.

Usage:
    python manage.py get_volunteers_with_field --field "Calling members" --output callers.csv

The --field value is matched against the label or name that SubmittedField
snapshotted at submission time (see that model's docstring).

Only authenticated submissions are considered: an anonymous submission is
never attached to a Person record, so it has no bearing on this export —
see FormSubmission's docstring for why.
"""

import csv

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from underground_crm.models import SubmittedField

CSV_FIELDNAMES = ["email", "first_name", "last_name", "mobile_number", "phone_number"]


class Command(BaseCommand):
    help = "Export authenticated people who answered yes to a given input field on a form to a CSV file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--field",
            required=True,
            help='Label or name of the input block to export, e.g. "Calling members".',
        )
        parser.add_argument(
            "--output",
            required=True,
            help="Path to write the CSV file to.",
        )

    def handle(self, *args, **options):
        field_name = options["field"]
        output_path = options["output"]

        matches_field = Q(label=field_name) | Q(name=field_name)
        if not SubmittedField.objects.filter(matches_field).exists():
            available = ", ".join(
                sorted(
                    set(SubmittedField.objects.values_list("label", flat=True))
                    | set(SubmittedField.objects.values_list("name", flat=True))
                )
            )
            raise CommandError(
                f'No submissions mention an input field labelled or named "{field_name}". '
                f"Available input fields: {available or '(none submitted yet)'}"
            )

        Person = get_user_model()
        people = (
            Person.objects.filter(
                Q(form_submissions__submitted_fields__label=field_name)
                | Q(form_submissions__submitted_fields__name=field_name),
                form_submissions__is_authenticated=True,
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
