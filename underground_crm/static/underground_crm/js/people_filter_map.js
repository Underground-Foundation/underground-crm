/*
 * Plots the people matched by a saved People filter on a Leaflet map.
 *
 * The markers arrive as JSON in a <script type="application/json"> element written by
 * Django's json_script filter, and the tile server and its attribution arrive as data
 * attributes on the map container, so that both stay configurable from Django settings.
 * See underground_crm/maps.py for the shape of the marker data.
 */
(function () {
    'use strict';

    // Two shades of the same idea: a marker whose coordinates name the property is drawn
    // small and solid, whereas one that names only a locality or postcode is drawn larger
    // and fainter, so that it reads as a region rather than as somebody's front door.
    // The legend beneath the map repeats these two colors in CSS, so a change here needs
    // the matching change to .people-filter-map-legend-key in the map template.
    var PRECISE_MARKER = { radius: 6, color: '#2a6ba8', fillColor: '#2a6ba8', fillOpacity: 0.8, weight: 1 };
    var APPROXIMATE_MARKER = { radius: 10, color: '#b4700f', fillColor: '#b4700f', fillOpacity: 0.25, weight: 1, dashArray: '3' };

    // Zooming all the way in on a single marker would show a street with no context
    // around it, so a lone point is shown at a neighborhood scale instead.
    var MAX_FIT_ZOOM = 16;
    var FIT_PADDING_IN_PIXELS = 30;

    function readMarkers() {
        var element = document.getElementById('people-filter-map-data');
        if (element === null) {
            return [];
        }
        try {
            return JSON.parse(element.textContent);
        } catch (error) {
            console.error('People filter map: the marker data could not be read.', error);
            return [];
        }
    }

    /*
     * The popup for one marker: everybody at this point, each linked to their admin page.
     * It is assembled as DOM nodes rather than as an HTML string so that a person's name
     * or address cannot be mistaken for markup.
     */
    function buildPopup(marker, approximateLabel) {
        var container = document.createElement('div');
        var list = document.createElement('ul');

        marker.people.forEach(function (person) {
            var item = document.createElement('li');
            var link = document.createElement('a');
            link.href = person.admin_url;
            link.textContent = person.name;
            item.appendChild(link);

            if (person.address) {
                var address = document.createElement('span');
                address.className = 'people-filter-map-address';
                address.textContent = person.address;
                item.appendChild(address);
            }

            list.appendChild(item);
        });

        container.appendChild(list);

        if (marker.is_approximate && approximateLabel) {
            var note = document.createElement('p');
            note.textContent = approximateLabel;
            container.appendChild(note);
        }

        return container;
    }

    function drawMap() {
        var container = document.getElementById('people-filter-map');
        if (container === null) {
            return;
        }
        if (typeof L === 'undefined') {
            container.textContent = container.dataset.libraryMissingLabel;
            return;
        }

        var markers = readMarkers();
        if (markers.length === 0) {
            return;
        }

        var map = L.map(container);
        L.tileLayer(container.dataset.tileUrl, {
            attribution: container.dataset.tileAttribution
        }).addTo(map);

        var approximateLabel = container.dataset.approximateLabel;
        var coordinates = markers.map(function (marker) {
            var style = marker.is_approximate ? APPROXIMATE_MARKER : PRECISE_MARKER;
            var position = [marker.latitude, marker.longitude];
            L.circleMarker(position, style)
                .bindPopup(buildPopup(marker, approximateLabel))
                .addTo(map);
            return position;
        });

        map.fitBounds(L.latLngBounds(coordinates), {
            padding: [FIT_PADDING_IN_PIXELS, FIT_PADDING_IN_PIXELS],
            maxZoom: MAX_FIT_ZOOM
        });
    }

    // Both this file and Leaflet are deferred, so the document is already parsed by the
    // time either runs; the readyState check covers the script being loaded some other way.
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', drawMap);
    } else {
        drawMap();
    }
}());
