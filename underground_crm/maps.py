"""Turning a set of people into the points that the admin map view plots.

Coordinates come from ``Address.latitude`` and ``Address.longitude``, which the
Addressr geocoding flow fills in once an address has been verified (see
``underground_crm.addressr``). A person whose address has never been geocoded
has nowhere to be drawn, so every map also reports how many of the people it was
given were left off it.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Iterable

from django.urls import reverse

if TYPE_CHECKING:
    from underground_crm.models.person import Person

# G-NAF grades each geocode from 1 (surveyed on the ground) to 6 (no better than
# the postcode region). Grades 1 to 3 put the point on the property itself, while
# from 4 upwards the coordinate is only the center of a locality or a postcode —
# which lands unrelated people on exactly the same spot. The map distinguishes
# the two so that a cluster of coarse geocodes is not mistaken for a street full
# of supporters.
PRECISE_GEOCODE_MAX_RELIABILITY = 3

# An upper bound on the markers handed to the browser. Leaflet gives every marker
# its own element, so a filter that matches a national membership list would stop
# the tab responding rather than draw a usable map.
DEFAULT_LOCATION_LIMIT = 2000


@dataclasses.dataclass(frozen=True)
class MappedPerson:
    """One person as they appear in a marker's popup."""

    name: str
    address: str
    admin_url: str
    has_precise_geocode: bool


@dataclasses.dataclass(frozen=True)
class MapLocation:
    """A single marker: everybody whose address geocoded to the same point.

    Households share a marker for the obvious reason, but so does everybody whose
    address could only be resolved to a locality or postcode centroid, which is
    why ``has_precise_geocode`` is answered for the group rather than assumed.
    """

    latitude: Decimal
    longitude: Decimal
    people: tuple[MappedPerson, ...]

    @property
    def has_precise_geocode(self) -> bool:
        """True only when every person here was placed on their own property."""
        return all(person.has_precise_geocode for person in self.people)


@dataclasses.dataclass(frozen=True)
class MapData:
    """Everything the map template needs, including what it could not show."""

    locations: tuple[MapLocation, ...]
    person_count: int
    mapped_person_count: int
    ungeocoded_person_count: int
    omitted_location_count: int

    def as_marker_payload(self) -> list[dict[str, Any]]:
        """The locations in the plain-data shape that the page's script reads.

        Decimals become floats here because that is what Leaflet expects, and six
        decimal places of latitude — roughly a tenth of a metre — survive the
        conversion intact.
        """
        return [
            {
                "latitude": float(location.latitude),
                "longitude": float(location.longitude),
                "is_approximate": not location.has_precise_geocode,
                "people": [
                    {
                        "name": person.name,
                        "address": person.address,
                        "admin_url": person.admin_url,
                    }
                    for person in location.people
                ],
            }
            for location in self.locations
        ]


def has_precise_geocode(reliability: int | None) -> bool:
    """Whether a geocode names the property itself rather than a region around it.

    A geocode recorded without any reliability grade cannot claim to be precise,
    so it is reported the same way as an explicitly coarse one.
    """
    return reliability is not None and reliability <= PRECISE_GEOCODE_MAX_RELIABILITY


def build_map_data(
    people: Iterable["Person"], *, location_limit: int = DEFAULT_LOCATION_LIMIT
) -> MapData:
    """Group ``people`` into map markers by the coordinates of their location.

    ``people`` is any iterable of Person records; the caller is responsible for
    selecting the related address rows, because this walks ``Person.location``
    for each one. Pass ``location_limit`` to change how many markers the result
    may carry.
    """
    grouped: dict[tuple[Decimal, Decimal], list[MappedPerson]] = {}
    person_count = 0
    ungeocoded_person_count = 0

    for person in people:
        person_count += 1
        address = person.location
        if address is None or address.latitude is None or address.longitude is None:
            ungeocoded_person_count += 1
            continue
        entry = MappedPerson(
            name=person.name_or_email,
            address=address.one_line,
            admin_url=reverse("admin:underground_crm_person_change", args=[person.pk]),
            has_precise_geocode=has_precise_geocode(address.geocode_reliability),
        )
        grouped.setdefault((address.latitude, address.longitude), []).append(entry)

    locations = [
        MapLocation(latitude=latitude, longitude=longitude, people=tuple(entries))
        for (latitude, longitude), entries in grouped.items()
    ]
    # The busiest points say the most about where a filter's people are, so those
    # are the ones kept when there are more markers than the browser should draw.
    # Latitude and longitude break ties, so the same filter always maps the same way.
    locations.sort(
        key=lambda location: (-len(location.people), location.latitude, location.longitude)
    )

    return MapData(
        locations=tuple(locations[:location_limit]),
        person_count=person_count,
        mapped_person_count=person_count - ungeocoded_person_count,
        ungeocoded_person_count=ungeocoded_person_count,
        omitted_location_count=max(len(locations) - location_limit, 0),
    )
