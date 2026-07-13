/*
 * Address autocomplete for inputs rendered by AddressAutocompleteInput
 * (see underground_crm/widgets.py).
 *
 * A single delegated listener handles every matching input, including ones
 * added to the page after load (for example, blocks added dynamically in the
 * Wagtail editor). Suggestions come from the address-suggestion endpoint,
 * which proxies the local Addressr container, and are presented through a
 * native <datalist> so the browser supplies the dropdown, keyboard
 * navigation, and accessibility behavior.
 *
 * A standalone input simply keeps the picked suggestion as its text. Inside
 * a StructuredAddressWidget group (an ancestor carrying
 * data-structured-address), picking a suggestion instead spreads it across
 * the group's component inputs (address lines/suburb/state/postcode, found
 * by their data-address-component attributes) and records the suggestion's
 * G-NAF ID in the group's hidden input. Editing the first line, suburb,
 * state, or postcode by hand clears the ID again, so an edited address can
 * never travel with a stale ID; lines 2 and 3 are supplementary delivery
 * detail, so they leave the ID (and with it the suggestion's coordinates)
 * intact.
 * Everything here is a progressive enhancement: without JavaScript the
 * components are ordinary text inputs, and the server treats a submission
 * without a G-NAF ID as a manual entry to verify in the background.
 */
(function () {
  "use strict";

  var DEBOUNCE_MILLISECONDS = 250;
  var VISIBLE_COMPONENTS = ["line1", "line2", "line3", "city", "state", "postcode"];
  var debounceTimers = new WeakMap();
  var inflightControllers = new WeakMap();
  // Per autocomplete input: a plain object mapping each suggested single-line
  // address to its G-NAF ID, rebuilt from every suggestion response.
  var gnafIdsBySuggestionText = new WeakMap();

  function getDatalist(input) {
    var existingId = input.getAttribute("list");
    if (existingId) {
      var existing = document.getElementById(existingId);
      if (existing) {
        return existing;
      }
    }
    var datalist = document.createElement("datalist");
    datalist.id =
      (input.id || "address-input-" + Math.random().toString(36).slice(2)) + "-suggestions";
    input.insertAdjacentElement("afterend", datalist);
    input.setAttribute("list", datalist.id);
    return datalist;
  }

  function componentInput(group, componentName) {
    return group.querySelector('[data-address-component="' + componentName + '"]');
  }

  function setGnafId(group, gnafId) {
    var hidden = componentInput(group, "gnaf_id");
    if (hidden) {
      hidden.value = gnafId || "";
    }
  }

  /*
   * Split a suggestion like "UNIT 1, 10 DOWNING ST, BLACKBURN VIC 3130" into
   * component values: the final comma segment is "LOCALITY STATE POSTCODE"
   * (see addressr's single-line address format), and everything before it —
   * unit/level detail included — belongs on address line 1. If a suggestion
   * ever arrives in another shape, the whole string goes to line 1: the
   * server-side verification joins the components back together, so the
   * G-NAF ID still checks out.
   */
  function parseSuggestion(suggestionText) {
    var segments = suggestionText
      .split(",")
      .map(function (segment) {
        return segment.trim();
      })
      .filter(Boolean);
    var locality = segments.length >= 2 ? segments[segments.length - 1].split(/\s+/) : [];
    if (locality.length >= 3 && /^\d{4}$/.test(locality[locality.length - 1])) {
      return {
        line1: segments.slice(0, -1).join(", "),
        line2: "",
        line3: "",
        city: locality.slice(0, -2).join(" "),
        state: locality[locality.length - 2],
        postcode: locality[locality.length - 1],
      };
    }
    return { line1: suggestionText, line2: "", line3: "", city: "", state: "", postcode: "" };
  }

  function applySuggestion(input, group, suggestionText, gnafId) {
    var components = parseSuggestion(suggestionText);
    VISIBLE_COMPONENTS.forEach(function (componentName) {
      var target = componentInput(group, componentName);
      if (target) {
        target.value = components[componentName];
      }
    });
    setGnafId(group, gnafId);
    // Emptying the datalist stops the dropdown from immediately re-opening
    // over the freshly filled inputs (the new line1 text is a prefix of the
    // suggestion it came from).
    getDatalist(input).replaceChildren();
  }

  function fetchSuggestions(input) {
    var previousController = inflightControllers.get(input);
    if (previousController) {
      previousController.abort();
    }
    var controller = new AbortController();
    inflightControllers.set(input, controller);

    var url = new URL(input.dataset.suggestionUrl, window.location.origin);
    url.searchParams.set("q", input.value.trim());

    fetch(url, { signal: controller.signal, headers: { Accept: "application/json" } })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("Suggestion request failed with status " + response.status);
        }
        return response.json();
      })
      .then(function (payload) {
        var datalist = getDatalist(input);
        datalist.replaceChildren();
        var gnafIds = {};
        (payload.suggestions || []).forEach(function (suggestion) {
          var option = document.createElement("option");
          option.value = suggestion.sla;
          datalist.appendChild(option);
          if (suggestion.gnaf_id) {
            gnafIds[suggestion.sla] = suggestion.gnaf_id;
          }
        });
        gnafIdsBySuggestionText.set(input, gnafIds);
      })
      .catch(function (error) {
        if (error.name !== "AbortError") {
          // Autocomplete is a progressive enhancement: the visitor can still
          // type their address in full, so failures are only logged.
          console.warn("Address autocomplete unavailable:", error);
        }
      });
  }

  document.addEventListener("input", function (event) {
    var input = event.target;
    if (!(input instanceof HTMLInputElement)) {
      return;
    }
    var group = input.closest("[data-structured-address]");

    if (input.dataset.addressAutocomplete) {
      var typed = input.value.trim();
      var gnafIds = gnafIdsBySuggestionText.get(input) || {};
      if (group && Object.prototype.hasOwnProperty.call(gnafIds, typed)) {
        // The visitor picked a suggestion (or typed one out in full, which
        // amounts to the same thing).
        window.clearTimeout(debounceTimers.get(input));
        var inflight = inflightControllers.get(input);
        if (inflight) {
          inflight.abort();
        }
        applySuggestion(input, group, typed, gnafIds[typed]);
        return;
      }
      if (group) {
        // Whatever was picked before, the text no longer matches it.
        setGnafId(group, "");
      }
      window.clearTimeout(debounceTimers.get(input));
      var minimumLength = parseInt(input.dataset.minimumLength, 10) || 5;
      if (typed.length < minimumLength) {
        return;
      }
      debounceTimers.set(
        input,
        window.setTimeout(function () {
          fetchSuggestions(input);
        }, DEBOUNCE_MILLISECONDS)
      );
      return;
    }

    // A hand edit to the suburb, state, or postcode of a structured group
    // invalidates the picked suggestion's ID (programmatic fills fire no
    // input event, so applySuggestion itself never lands here). Lines 2 and
    // 3 are exempt: they hold supplementary delivery detail that does not
    // move the property, so the ID stays for the server to verify — it keeps
    // the suggestion's coordinates, storing the ID itself only while those
    // lines are empty (see Address.from_components).
    var component = input.dataset.addressComponent;
    if (group && component && component !== "line2" && component !== "line3") {
      setGnafId(group, "");
    }
  });
})();
