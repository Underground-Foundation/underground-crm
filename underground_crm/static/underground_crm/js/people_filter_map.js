/*
 * Plots the people matched by a saved People filter on a Leaflet map.
 *
 * The markers and the choice of base maps each arrive as JSON in a
 * <script type="application/json"> element written by Django's json_script filter, so
 * that the base maps stay configurable from the MAP_TILE_LAYERS setting. See
 * underground_crm/maps.py for the shape of both.
 */
(function () {
    'use strict';

    // Two shades of the same idea: a marker whose coordinates name the property is drawn
    // small and solid, whereas one that names only a locality or postcode is drawn larger
    // and fainter, so that it reads as a region rather than as somebody's front door.
    // The legend beneath the map repeats these two colors in CSS, so a change here needs
    // the matching change to .people-filter-map-legend-key in the map template. The thick
    // white border keeps a precise marker distinct against busy streets and satellite imagery.
    var PRECISE_MARKER = { radius: 7, color: '#ffffff', opacity: 1, weight: 3, fillColor: '#2a6ba8', fillOpacity: 1 };
    var APPROXIMATE_MARKER = { radius: 10, color: '#b4700f', fillColor: '#b4700f', fillOpacity: 0.25, weight: 1, dashArray: '3' };

    // Zooming all the way in on a single marker would show a street with no context
    // around it, so a lone point is shown at a neighborhood scale instead.
    var MAX_FIT_ZOOM = 16;
    var FIT_PADDING_IN_PIXELS = 30;

    // Where the viewer's last choice of base map is remembered, by the layer's key.
    var TILE_LAYER_STORAGE_KEY = 'underground_crm.peopleFilterMap.tileLayer';

    function readJson(elementId, description) {
        var element = document.getElementById(elementId);
        if (element === null) {
            return [];
        }
        try {
            return JSON.parse(element.textContent);
        } catch (error) {
            console.error('People filter map: the ' + description + ' could not be read.', error);
            return [];
        }
    }

    // Storage can be unavailable altogether (in a private window, or with site data
    // blocked), in which case the map simply opens on the first layer every time.
    function readRememberedTileLayer() {
        try {
            return window.localStorage.getItem(TILE_LAYER_STORAGE_KEY);
        } catch (error) {
            return null;
        }
    }

    function rememberTileLayer(key) {
        try {
            window.localStorage.setItem(TILE_LAYER_STORAGE_KEY, key);
        } catch (error) {
            // Nothing to do: the choice just won't outlast this page.
        }
    }

    /*
     * Adds every configured base map to the layers control and shows one of them: the
     * viewer's previous choice if it is still on offer, and otherwise the first.
     */
    function addTileLayers(map, definitions) {
        if (definitions.length === 0) {
            return;
        }
        var rememberedKey = readRememberedTileLayer();
        var baseLayers = {};
        var keysByName = {};
        var initialLayer = null;

        definitions.forEach(function (definition, index) {
            // Django's default Referrer-Policy of "same-origin" strips the Referer header
            // from cross-origin requests, and OpenStreetMap's tile servers block tile
            // requests that arrive without one. Sending only the origin identifies the
            // site without leaking the page's path, which contains the filter's UUID.
            var options = Object.assign(
                { referrerPolicy: 'strict-origin-when-cross-origin' },
                definition.options
            );
            var layer = L.tileLayer(definition.url, options);
            baseLayers[definition.name] = layer;
            keysByName[definition.name] = definition.key;
            if (index === 0 || definition.key === rememberedKey) {
                initialLayer = layer;
            }
        });

        initialLayer.addTo(map);
        if (definitions.length > 1) {
            L.control.layers(baseLayers).addTo(map);
        }
        map.on('baselayerchange', function (event) {
            rememberTileLayer(keysByName[event.name]);
        });
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

        var markers = readJson('people-filter-map-data', 'marker data');
        if (markers.length === 0) {
            return;
        }

        var map = L.map(container);
        addTileLayers(map, readJson('people-filter-map-tile-layers', 'base map choices'));

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
