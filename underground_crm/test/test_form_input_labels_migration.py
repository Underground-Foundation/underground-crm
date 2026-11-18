"""
Tests of the stream conversion in migration 0031, which gives each input block
on a FormPage a label of its own. The conversion is a pure function of a page
body's raw JSON, so it is tested here on the raw data alone, without a database.
"""

import importlib
import unittest
from typing import Any

migration = importlib.import_module("underground_crm.migrations.0031_form_input_labels")

INTRO_HTML = "<h2>Volunteer with us</h2><p>Tick everything you could help with.</p>"


def stream_child(block_type: str, value: Any, block_id: str) -> dict[str, Any]:
    return {"type": block_type, "value": value, "id": block_id}


class LabelFormInputsTest(unittest.TestCase):
    def test_imported_question_paragraph_becomes_the_label(self):
        # import_pages escaped each question into a paragraph of its own, so
        # an ampersand in the question arrives as &amp;.
        question = "Letterboxing & leafleting"
        converted = migration.label_form_inputs(
            [
                stream_child("html", INTRO_HTML, "intro"),
                stream_child("html", "<p>Letterboxing &amp; leafleting</p>", "question"),
                stream_child("checkbox", False, "letterboxing"),
            ]
        )
        self.assertEqual(
            converted,
            [
                stream_child("html", INTRO_HTML, "intro"),
                stream_child(
                    "checkbox",
                    {"label": question, "help_text": "", "required": False, "default": False},
                    "letterboxing",
                ),
            ],
            msg=(
                "The paragraph ahead of the input is its question, so it should move into "
                "the label and leave the body; the intro copy is not a question and stays"
            ),
        )

    def test_input_without_a_question_keeps_the_label_visitors_already_saw(self):
        prefilled_suburb = "Footscray"
        converted = migration.label_form_inputs(
            [
                stream_child("html", INTRO_HTML, "intro"),
                stream_child("text", prefilled_suburb, "suburb"),
            ]
        )
        self.assertEqual(
            converted[-1]["value"],
            {
                "label": migration.FALLBACK_LABELS["text"],
                "help_text": "",
                "required": False,
                "default": prefilled_suburb,
            },
            msg=(
                "Multi-paragraph intro copy is not an imported question, so the input is "
                "labelled with its type name (what visitors saw before) and keeps its pre-filled value"
            ),
        )
        self.assertEqual(converted[0], stream_child("html", INTRO_HTML, "intro"))

    def test_converting_twice_changes_nothing_more(self):
        # FormPage and EventPage rows are the same rows, so the migration's two
        # passes both meet every event's body.
        once = migration.label_form_inputs(
            [
                stream_child("html", "<p>I can drive volunteers to polling booths</p>", "question"),
                stream_child("checkbox", False, "driving"),
            ]
        )
        self.assertEqual(migration.label_form_inputs(once), once)

    def test_reversal_restores_the_imported_layout(self):
        original = [
            stream_child("html", INTRO_HTML, "intro"),
            stream_child("html", "<p>Letterboxing &amp; leafleting</p>", "question"),
            stream_child("checkbox", False, "letterboxing"),
            stream_child("text", "", "availability"),
        ]
        restored = migration.unlabel_form_inputs(migration.label_form_inputs(original))
        self.assertEqual(
            [(child["type"], child["value"]) for child in restored],
            [(child["type"], child["value"]) for child in original],
            msg=(
                "Reversing should put the question paragraph back ahead of its input, and "
                "add none ahead of an input that only ever had its type name as a label"
            ),
        )
