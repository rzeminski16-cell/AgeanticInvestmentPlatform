/*
 * The overlay chrome, written once and never per page (ADR 0073).
 *
 * This file owns focus, the Escape key, the background scroll lock and an overlay's
 * open-and-closed lifecycle. It owns no state: the panel's contents are server-rendered
 * HTML that htmx swapped in, and if a reload lost the fact that a drawer was open, nobody
 * would notice. That is the test ADR 0073 sets for what the client may have, and it is why
 * this is a chrome layer rather than an island — there is no JSON contract because there is
 * no data, only a node to put markup in and the browser behaviour a server cannot send.
 *
 * **It computes nothing and formats nothing.** JavaScript may own chrome, never a figure:
 * every number in the panel arrived as text the server had already decided.
 *
 * The contract with a page is four attributes and nothing else:
 *
 *   - `hx-target="#aer-drawer-body"` on the trigger, which is what opens the drawer. There
 *     is no `data-drawer-open`: the drawer opens because content arrived in it, so a
 *     trigger cannot open an empty one and cannot forget to fill one it opened.
 *   - `data-drawer-title` on the trigger, which names the panel for a screen reader.
 *   - `data-drawer-close` on anything inside it that should shut it.
 *   - a real `href` on the trigger, so the same link is a page when scripting is off.
 */
(function () {
  "use strict";

  var ROOT = "aer-drawer";
  var BODY = "aer-drawer-body";
  var TITLE = "aer-drawer-title";

  /*
   * What Tab may reach. Deliberately a list rather than `:focusable` — that pseudo-class
   * is not in every browser this has to work in, and a trap that silently matches nothing
   * is a trap that lets focus wander into a background the reader cannot see.
   */
  var FOCUSABLE = [
    "a[href]",
    "button:not([disabled])",
    "input:not([disabled])",
    "select:not([disabled])",
    "textarea:not([disabled])",
    '[tabindex]:not([tabindex="-1"])',
  ].join(",");

  // Where focus goes back to. A drawer that returned focus to the top of the document
  // would make a reader work back down to the row they were on, every time.
  var opener = null;

  function root() {
    return document.getElementById(ROOT);
  }

  function panel() {
    var found = root();
    return found ? found.querySelector('[role="dialog"]') : null;
  }

  function isOpen() {
    var found = root();
    return !!found && !found.hidden;
  }

  function open(trigger) {
    var found = root();
    var dialog = panel();
    if (!found || !dialog) {
      return;
    }

    // Only the first open remembers the opener. Clicking a second row while the drawer is
    // already open replaces its contents; closing should still land back at the row the
    // reader came in from.
    if (!isOpen()) {
      opener = trigger || document.activeElement;
    }

    var heading = document.getElementById(TITLE);
    if (heading && trigger) {
      heading.textContent = trigger.getAttribute("data-drawer-title") || "";
    }

    found.hidden = false;
    document.documentElement.classList.add("overflow-hidden");
    dialog.focus();
  }

  function close() {
    var found = root();
    if (!found || found.hidden) {
      return;
    }

    found.hidden = true;
    document.documentElement.classList.remove("overflow-hidden");

    // Emptied on close rather than on open. A panel that kept the last run's numbers would
    // show them for the instant before the next request landed, and a reader who opened
    // the wrong row would see the right-looking answer to the wrong question.
    var body = document.getElementById(BODY);
    if (body) {
      body.innerHTML = "";
    }

    if (opener && document.contains(opener)) {
      opener.focus();
    }
    opener = null;
  }

  function cycle(event) {
    var dialog = panel();
    if (!dialog) {
      return;
    }

    var stops = Array.prototype.filter.call(
      dialog.querySelectorAll(FOCUSABLE),
      function (node) {
        return node.offsetParent !== null || node === document.activeElement;
      }
    );
    if (stops.length === 0) {
      // Nothing to move to, so Tab must not leave. The panel itself is focusable
      // (`tabindex="-1"`), which is what makes holding focus here possible at all.
      event.preventDefault();
      dialog.focus();
      return;
    }

    var first = stops[0];
    var last = stops[stops.length - 1];
    var active = document.activeElement;

    if (event.shiftKey && (active === first || active === dialog)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  document.addEventListener("keydown", function (event) {
    if (!isOpen()) {
      return;
    }
    if (event.key === "Escape") {
      close();
    } else if (event.key === "Tab") {
      cycle(event);
    }
  });

  document.addEventListener("click", function (event) {
    if (!isOpen()) {
      return;
    }
    var target = event.target;
    // `closest`, so a click on an icon inside the close button still closes it.
    if (target && target.closest && target.closest("[data-drawer-close]")) {
      event.preventDefault();
      close();
    }
  });

  /*
   * The drawer opens because content arrived in it.
   *
   * htmx does the fetching; this only reacts to the swap landing. Binding to the swap
   * rather than to the click is what makes an empty drawer impossible: there is no moment
   * between "opened" and "filled" for a failed request to leave visible.
   */
  document.body.addEventListener("htmx:afterSwap", function (event) {
    var detail = event.detail;
    if (!detail || !detail.target || detail.target.id !== BODY) {
      return;
    }
    /*
     * `requestConfig.elt` is the element that asked; `detail.elt` on this event is the one
     * that was swapped, which is the drawer body itself. Reading the wrong one is how the
     * panel opened with an empty heading — visibly, and only in a browser.
     */
    var config = detail.requestConfig;
    open((config && config.elt) || detail.elt);
  });
})();
