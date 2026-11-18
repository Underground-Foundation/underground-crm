"""Tests of how the choice input blocks read the choices an editor lists."""

import unittest

from underground_crm.blocks import choices_in


class ChoicesInTest(unittest.TestCase):
    def test_blank_lines_and_surrounding_spaces_are_ignored(self):
        typed = "  Wills\n\nCooper  \n   \nMelbourne\n"
        self.assertEqual(choices_in({"choices": typed}), ["Wills", "Cooper", "Melbourne"])

    def test_a_choice_listed_twice_is_offered_once(self):
        typed = "Phone call\nEmail\nPhone call"
        self.assertEqual(
            choices_in({"choices": typed}),
            ["Phone call", "Email"],
            msg="Duplicate choices would render two identical radio buttons for one answer",
        )
