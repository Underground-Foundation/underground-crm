/*
 * Collapse behavior for "Same as home address" checkboxes rendered by
 * SameAsHomeAddressCheckbox (see underground_crm/widgets.py).
 *
 * Each checkbox names the address input it governs through its
 * data-same-as-home-controls attribute. While the checkbox is ticked, that
 * input (together with its label, via the enclosing paragraph) stays
 * collapsed; unticking it expands the input so a differing address can be
 * entered. This is a progressive enhancement: without JavaScript every input
 * simply stays visible, and the server honors the checkbox state regardless.
 */
(function () {
  "use strict";

  function controlledContainer(checkbox) {
    var controlled = document.getElementById(checkbox.dataset.sameAsHomeControls);
    if (!controlled) {
      return null;
    }
    // form.as_p wraps each field, label included, in its own <p>.
    return controlled.closest("p") || controlled;
  }

  function applyState(checkbox) {
    var container = controlledContainer(checkbox);
    if (container) {
      container.hidden = checkbox.checked;
    }
  }

  document.addEventListener("change", function (event) {
    var checkbox = event.target;
    if (checkbox instanceof HTMLInputElement && checkbox.dataset.sameAsHomeControls) {
      applyState(checkbox);
    }
  });

  function applyAll() {
    document.querySelectorAll("input[data-same-as-home-controls]").forEach(applyState);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", applyAll);
  } else {
    applyAll();
  }
})();
