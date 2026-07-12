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
 */
(function () {
  "use strict";

  var DEBOUNCE_MILLISECONDS = 250;
  var debounceTimers = new WeakMap();
  var inflightControllers = new WeakMap();

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
        (payload.suggestions || []).forEach(function (suggestion) {
          var option = document.createElement("option");
          option.value = suggestion;
          datalist.appendChild(option);
        });
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
    if (!(input instanceof HTMLInputElement) || !input.dataset.addressAutocomplete) {
      return;
    }
    window.clearTimeout(debounceTimers.get(input));
    var minimumLength = parseInt(input.dataset.minimumLength, 10) || 5;
    if (input.value.trim().length < minimumLength) {
      return;
    }
    debounceTimers.set(
      input,
      window.setTimeout(function () {
        fetchSuggestions(input);
      }, DEBOUNCE_MILLISECONDS)
    );
  });
})();
