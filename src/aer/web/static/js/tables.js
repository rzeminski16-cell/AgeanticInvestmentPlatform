// Filtering a long table down to the rows somebody is actually looking for.
//
// Progressive enhancement, and the `hidden` attribute is the whole of it: every control
// this file drives is rendered hidden and revealed here, so a browser with scripting off
// shows a complete table and no search box rather than a search box that does nothing.
// The same rule the drawer follows, and the reason the menu is a `<details>`.
//
// Nothing here talks to the server. The rows are already on the page — they were rendered
// with it, inside the payload the operator is approving — and a filter that fetched would
// be a filter that could disagree with what was hashed.

(function () {
  "use strict";

  // Every filter input names the table it belongs to. Two filters on one page is the
  // ordinary case — the gate that confirms extracted financials has three — so nothing
  // here assumes there is only one.
  var inputs = document.querySelectorAll("[data-filters]");

  Array.prototype.forEach.call(inputs, function (input) {
    var table = document.querySelector(input.getAttribute("data-filters"));
    if (!table) {
      return;
    }

    var shell = input.closest("[hidden]");
    if (shell) {
      shell.hidden = false;
    }

    var count = input.getAttribute("data-count")
      ? document.querySelector(input.getAttribute("data-count"))
      : null;

    var rows = table.querySelectorAll("tbody tr[data-search]");

    var apply = function () {
      // Case-folded on both sides. An operator hunting `GrossProfit` should not have to
      // remember which way the taxonomy capitalised it.
      var needle = input.value.trim().toLowerCase();
      var shown = 0;

      Array.prototype.forEach.call(rows, function (row) {
        var haystack = (row.getAttribute("data-search") || "").toLowerCase();
        // Every space-separated word must appear, in any order: "margin 2026" finds the
        // 2026 margin without the operator having to type the fields in table order.
        var matches = needle === "" || needle.split(/\s+/).every(function (word) {
          return haystack.indexOf(word) !== -1;
        });
        row.hidden = !matches;
        if (matches) {
          shown += 1;
        }
      });

      if (count) {
        count.textContent =
          needle === ""
            ? ""
            : shown + " of " + rows.length + (shown === 1 ? " row matches" : " rows match");
      }
    };

    input.addEventListener("input", apply);
    // A browser that restored a value on reload should filter before the operator types.
    apply();
  });
})();
