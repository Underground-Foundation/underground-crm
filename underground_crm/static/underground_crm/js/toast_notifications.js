/*
 * Behavior for the toast notifications rendered from Django's messages
 * framework (see the toast block in registration_page.html): each one
 * dismisses itself on a timer, or immediately if its close button is
 * clicked. Progressive enhancement only — without JavaScript the messages
 * stay on screen until the visitor navigates away, which is a harmless
 * fallback rather than a broken one.
 */
(function () {
  "use strict";

  var AUTO_DISMISS_MS = 6000;

  function dismiss(toast) {
    if (toast.parentNode) {
      toast.parentNode.removeChild(toast);
    }
  }

  function enhance(toast) {
    var closeButton = toast.querySelector(".toast-notification-close");
    if (closeButton) {
      closeButton.addEventListener("click", function () {
        dismiss(toast);
      });
    }
    window.setTimeout(function () {
      dismiss(toast);
    }, AUTO_DISMISS_MS);
  }

  function enhanceAll() {
    document.querySelectorAll(".toast-notification").forEach(enhance);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", enhanceAll);
  } else {
    enhanceAll();
  }
})();
