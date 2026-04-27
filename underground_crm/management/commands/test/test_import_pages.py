import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from underground_crm.management.commands.import_pages import parse_event_datetime


class TestParseEventDatetime(unittest.TestCase):
    def test_multi_state_label_uses_first_state(self):
        """NSW/ACT/VIC/TAS label resolves to Australia/Sydney via NSW."""
        start, end = parse_event_datetime(
            "        May 16, 2026 at 18:00 - 11pm (NSW/ACT/VIC/TAS timezone)"
        )
        nsw = ZoneInfo("Australia/Sydney")
        self.assertEqual(start, datetime(2026, 5, 16, 18, 0, tzinfo=nsw))
        self.assertEqual(end, datetime(2026, 5, 16, 23, 0, tzinfo=nsw))

    def test_single_state_vic(self):
        start, end = parse_event_datetime("June 1, 2026 at 09:00 - 5pm (VIC timezone)")
        vic = ZoneInfo("Australia/Melbourne")
        self.assertEqual(start, datetime(2026, 6, 1, 9, 0, tzinfo=vic))
        self.assertEqual(end, datetime(2026, 6, 1, 17, 0, tzinfo=vic))

    def test_qld_timezone(self):
        start, end = parse_event_datetime("July 4, 2026 at 14:00 - 6pm (QLD timezone)")
        qld = ZoneInfo("Australia/Brisbane")
        self.assertEqual(start, datetime(2026, 7, 4, 14, 0, tzinfo=qld))
        self.assertEqual(end, datetime(2026, 7, 4, 18, 0, tzinfo=qld))

    def test_wa_timezone(self):
        start, end = parse_event_datetime("March 15, 2026 at 10:00 - 2pm (WA timezone)")
        wa = ZoneInfo("Australia/Perth")
        self.assertEqual(start, datetime(2026, 3, 15, 10, 0, tzinfo=wa))
        self.assertEqual(end, datetime(2026, 3, 15, 14, 0, tzinfo=wa))

    def test_midnight_crossing_rolls_end_to_next_day(self):
        """When the end time is earlier in the day than the start time, one day is added."""
        start, end = parse_event_datetime("May 16, 2026 at 22:00 - 1am (NSW/ACT/VIC/TAS timezone)")
        nsw = ZoneInfo("Australia/Sydney")
        self.assertEqual(start, datetime(2026, 5, 16, 22, 0, tzinfo=nsw))
        self.assertEqual(end, datetime(2026, 5, 17, 1, 0, tzinfo=nsw))

    def test_unknown_state_falls_back_to_settings_timezone(self):
        with patch("underground_crm.management.commands.import_pages.settings") as mock_settings:
            mock_settings.TIME_ZONE = "UTC"
            start, _ = parse_event_datetime("January 1, 2026 at 10:00 - 2pm (ZZZ timezone)")
        self.assertEqual(start.utcoffset().total_seconds(), 0)

    def test_leading_whitespace_is_stripped(self):
        clean = parse_event_datetime("May 16, 2026 at 18:00 - 11pm (VIC timezone)")
        padded = parse_event_datetime("    May 16, 2026 at 18:00 - 11pm (VIC timezone)")
        self.assertEqual(clean, padded)
