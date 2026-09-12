"""Tests for the map view of a saved People filter.

The addresses below are real Melbourne landmarks with their real coordinates, so that
anybody reading the assertions can check for themselves that two people at Federation
Square share a marker and that the person in Fitzroy does not join them on it.
"""

import json
import logging
from decimal import Decimal

import django.test
from django.contrib.auth import get_user_model

from underground_crm.maps import (
    PRECISE_GEOCODE_MAX_RELIABILITY,
    build_map_data,
    has_precise_geocode,
)
from underground_crm.models import Address, PeopleFilter

logger = logging.getLogger(__name__)

Person = get_user_model()

# G-NAF grades a geocode from 1 (surveyed on the ground) to 6 (the postcode region only).
# These two sit either side of the boundary that underground_crm.maps draws between a
# point on the property and a point that merely names the area around it.
SURVEYED_RELIABILITY = 1
POSTCODE_REGION_RELIABILITY = 6

FEDERATION_SQUARE = {
    "line1": "Swanston St & Flinders St",
    "city": "Melbourne",
    "state": "VIC",
    "postcode": "3000",
    "latitude": Decimal("-37.817981"),
    "longitude": Decimal("144.969037"),
}
ROYAL_EXHIBITION_BUILDING = {
    "line1": "9 Nicholson St",
    "city": "Carlton",
    "state": "VIC",
    "postcode": "3053",
    "latitude": Decimal("-37.804722"),
    "longitude": Decimal("144.971667"),
}
# Fitzroy's postcode centroid, which is what a geocoder falls back to when it can place
# the suburb but not the street address.
FITZROY_POSTCODE_CENTROID = {
    "city": "Fitzroy",
    "state": "VIC",
    "postcode": "3065",
    "latitude": Decimal("-37.798889"),
    "longitude": Decimal("144.978611"),
}


def make_address(components: dict, reliability: int | None) -> Address:
    return Address.objects.create(geocode_reliability=reliability, **components)


def make_person(email: str, first_name: str, last_name: str, home_address=None) -> Person:
    return Person.objects.create_user(
        email=email,
        password="correct-horse-battery-staple",
        first_name=first_name,
        last_name=last_name,
        home_address=home_address,
    )


class MapDataTest(django.test.TestCase):
    """The grouping of people into markers, independent of how they are drawn."""

    def setUp(self):
        self.housemate = make_person(
            "rosa.kowalski@example.com",
            "Rosa",
            "Kowalski",
            make_address(FEDERATION_SQUARE, SURVEYED_RELIABILITY),
        )
        self.other_housemate = make_person(
            "daniel.osei@example.com",
            "Daniel",
            "Osei",
            make_address(FEDERATION_SQUARE, SURVEYED_RELIABILITY),
        )
        self.neighbor = make_person(
            "amrita.chandra@example.com",
            "Amrita",
            "Chandra",
            make_address(ROYAL_EXHIBITION_BUILDING, SURVEYED_RELIABILITY),
        )

    def test_people_at_the_same_coordinates_share_one_marker(self):
        map_data = build_map_data(Person.objects.all())

        expected_marker_count = 2  # Federation Square, plus the Royal Exhibition Building
        self.assertEqual(
            len(map_data.locations),
            expected_marker_count,
            msg=(
                "Two of the three people live at the same coordinates, so they belong on "
                "a single marker and the third on their own"
            ),
        )
        shared_marker = map_data.locations[0]
        self.assertEqual(shared_marker.latitude, FEDERATION_SQUARE["latitude"])
        self.assertEqual(shared_marker.longitude, FEDERATION_SQUARE["longitude"])
        self.assertEqual(
            {person.name for person in shared_marker.people},
            {self.housemate.name_or_email, self.other_housemate.name_or_email},
        )

    def test_everybody_given_is_counted_as_mapped(self):
        people = Person.objects.all()

        map_data = build_map_data(people)

        self.assertEqual(map_data.person_count, people.count())
        self.assertEqual(map_data.mapped_person_count, people.count())
        self.assertEqual(map_data.ungeocoded_person_count, 0)
        self.assertEqual(map_data.omitted_location_count, 0)

    def test_people_without_a_geocode_are_counted_but_not_plotted(self):
        # An address typed in but never resolved by Addressr, which is the state every
        # imported address starts in. _skip_geocoding is the flag the CSV and page
        # importers set for exactly this: store the address as given without queueing a
        # background geocode, which here would need a running Redis broker.
        ungeocoded_address = Address(
            line1="12 Smith St", city="Collingwood", state="VIC", postcode="3066"
        )
        ungeocoded_address._skip_geocoding = True
        ungeocoded_address.save()
        make_person("jun.takahashi@example.com", "Jun", "Takahashi", ungeocoded_address)
        make_person("priya.raman@example.com", "Priya", "Raman", home_address=None)
        people = Person.objects.all()
        people_with_coordinates = people.exclude(home_address__latitude=None).count()

        map_data = build_map_data(people)

        self.assertEqual(map_data.person_count, people.count())
        self.assertEqual(map_data.mapped_person_count, people_with_coordinates)
        self.assertEqual(
            map_data.ungeocoded_person_count,
            people.count() - people_with_coordinates,
            msg=(
                "One person's address was never geocoded and another has no address at "
                "all, so both should be reported as unmappable rather than dropped"
            ),
        )
        mapped_names = {
            person.name for location in map_data.locations for person in location.people
        }
        self.assertNotIn("Jun Takahashi", mapped_names)
        self.assertNotIn("Priya Raman", mapped_names)

    def test_the_limit_keeps_the_markers_carrying_the_most_people(self):
        single_marker_limit = 1

        map_data = build_map_data(Person.objects.all(), location_limit=single_marker_limit)

        self.assertEqual(len(map_data.locations), single_marker_limit)
        self.assertEqual(
            map_data.locations[0].latitude,
            FEDERATION_SQUARE["latitude"],
            msg=(
                "Federation Square holds two of the three people and the Royal Exhibition "
                "Building only one, so it is Federation Square that survives a limit of one"
            ),
        )
        self.assertEqual(
            map_data.omitted_location_count,
            1,
            msg="The Royal Exhibition Building is the one marker the limit left out",
        )
        self.assertEqual(
            map_data.mapped_person_count,
            Person.objects.count(),
            msg=(
                "The limit governs how many markers are drawn, not how many people were "
                "found to have a location, so the count of the latter is unaffected"
            ),
        )


class GeocodePrecisionTest(django.test.TestCase):
    """Which geocodes the map is willing to present as somebody's address."""

    def test_a_surveyed_geocode_is_precise(self):
        self.assertTrue(has_precise_geocode(SURVEYED_RELIABILITY))

    def test_a_postcode_region_geocode_is_not_precise(self):
        self.assertFalse(has_precise_geocode(POSTCODE_REGION_RELIABILITY))

    def test_the_boundary_grade_is_still_precise(self):
        self.assertTrue(
            has_precise_geocode(PRECISE_GEOCODE_MAX_RELIABILITY),
            msg="The named maximum is the last grade that still names the property itself",
        )
        self.assertFalse(has_precise_geocode(PRECISE_GEOCODE_MAX_RELIABILITY + 1))

    def test_a_geocode_without_a_recorded_grade_is_not_precise(self):
        self.assertFalse(
            has_precise_geocode(None),
            msg=(
                "A coordinate whose reliability was never recorded cannot claim to be a "
                "survey of the property, so the map marks it as approximate"
            ),
        )

    def test_a_marker_is_precise_only_when_everybody_on_it_is(self):
        make_person(
            "helena.varga@example.com",
            "Helena",
            "Varga",
            make_address(FITZROY_POSTCODE_CENTROID, SURVEYED_RELIABILITY),
        )
        make_person(
            "tomas.lindqvist@example.com",
            "Tomas",
            "Lindqvist",
            make_address(FITZROY_POSTCODE_CENTROID, POSTCODE_REGION_RELIABILITY),
        )

        map_data = build_map_data(Person.objects.all())

        self.assertFalse(
            map_data.locations[0].has_precise_geocode,
            msg=(
                "One of the two people at this point was only placed by postcode, so the "
                "marker as a whole cannot be presented as a property-level location"
            ),
        )


class PeopleFilterMapViewTest(django.test.TestCase):
    """The admin page itself: who may see it, and what it hands to the browser."""

    def setUp(self):
        self.staff_password = "correct-horse-battery-staple"
        self.staff_member = Person.objects.create_user(
            email="campaign.office@example.com",
            password=self.staff_password,
            first_name="Beatriz",
            last_name="Almeida",
            is_staff=True,
            is_superuser=True,
        )
        self.supporter = make_person(
            "rosa.kowalski@example.com",
            "Rosa",
            "Kowalski",
            make_address(FEDERATION_SQUARE, SURVEYED_RELIABILITY),
        )
        self.supporter.is_supporter = True
        self.supporter.save()
        self.people_filter = PeopleFilter.objects.create(
            name="Supporters in inner Melbourne",
            criteria={
                "logic": "AND",
                "rules": [{"field": "is_supporter", "operator": "true"}],
            },
        )
        self.client.login(email=self.staff_member.email, password=self.staff_password)

    def test_the_map_page_plots_the_matching_people(self):
        response = self.client.get(self.people_filter.map_url)

        self.assertEqual(response.status_code, 200)
        markers = json.loads(
            response.content.decode()
            .split('<script id="people-filter-map-data" type="application/json">')[1]
            .split("</script>")[0]
        )
        self.assertEqual(
            len(markers),
            1,
            msg="Only the supporter matches the filter, and she has one geocoded address",
        )
        self.assertAlmostEqual(markers[0]["latitude"], float(FEDERATION_SQUARE["latitude"]))
        self.assertAlmostEqual(markers[0]["longitude"], float(FEDERATION_SQUARE["longitude"]))
        self.assertFalse(markers[0]["is_approximate"])
        self.assertEqual(
            [person["name"] for person in markers[0]["people"]],
            [self.supporter.name_or_email],
        )
        self.assertEqual(
            markers[0]["people"][0]["admin_url"],
            f"/django-admin/underground_crm/person/{self.supporter.pk}/change/",
        )

    def test_the_map_page_links_back_to_the_filter_and_its_evaluation(self):
        response = self.client.get(self.people_filter.map_url)

        self.assertContains(response, self.people_filter.evaluation_url)
        self.assertContains(
            response, f"/django-admin/underground_crm/peoplefilter/{self.people_filter.pk}/change/"
        )

    def test_the_evaluation_page_links_to_the_map(self):
        response = self.client.get(self.people_filter.evaluation_url)

        self.assertContains(response, self.people_filter.map_url)

    def test_the_change_page_links_to_the_map(self):
        response = self.client.get(
            f"/django-admin/underground_crm/peoplefilter/{self.people_filter.pk}/change/"
        )

        self.assertContains(response, self.people_filter.map_url)

    def test_a_signed_out_visitor_is_sent_to_the_login_page(self):
        self.client.logout()

        response = self.client.get(self.people_filter.map_url)

        self.assertEqual(
            response.status_code,
            302,
            msg="The map exposes people's home addresses, so it is admin-only like every "
            "other page under /django-admin/",
        )
        self.assertIn("login", response.headers["Location"])
