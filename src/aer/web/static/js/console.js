/*
 * Live run progress.
 *
 * The page arrives fully rendered. This script only keeps it current, which is the whole
 * design: an operator watching a run that is spending money must never be looking at a
 * blank page because a script failed to load.
 *
 * Two mechanisms, one of which is always active:
 *
 *   * `EventSource` subscribes to /api/runs/{id}/events and applies each state frame.
 *   * A `<meta http-equiv="refresh">` emitted by the server reloads the page on a timer.
 *
 * The meta refresh is the default, present in the markup before any script runs. This file
 * removes it and takes over — so a browser with no EventSource, or one where this file
 * never loaded, keeps the slower behaviour rather than none.
 */
(function () {
  "use strict";

  var root = document.getElementById("run-console");
  if (!root) {
    return;
  }

  /*
   * Terminal runs are not watched. The stream would open, immediately emit `done` and
   * close, and the meta refresh would reload a page that can no longer change.
   */
  if (root.dataset.terminal === "true") {
    dropFallback();
    return;
  }

  if (typeof window.EventSource === "undefined") {
    // No stream available. Leave the meta refresh in place — slower, same information.
    return;
  }

  dropFallback();

  var note = document.getElementById("stream-note");
  if (note) {
    note.textContent = "Live. This page updates as the run progresses.";
  }

  var source = new EventSource(root.dataset.eventsUrl);

  source.addEventListener("state", function (event) {
    applyState(JSON.parse(event.data));
  });

  source.addEventListener("done", function () {
    source.close();
    /*
     * Reloaded rather than patched. A finished run reveals things this script does not
     * render — the report link, the approval banner — and re-fetching the page is both
     * simpler and guaranteed to agree with what the server thinks.
     */
    window.location.reload();
  });

  source.addEventListener("error", function () {
    /*
     * EventSource reconnects by itself on a dropped connection, so this only reports.
     * Replacing it with a manual retry loop would fight the browser's own backoff.
     */
    if (note) {
      note.textContent = "Reconnecting to the run…";
    }
  });

  function applyState(state) {
    var status = document.getElementById("run-status");
    if (status && state.status) {
      status.textContent = state.status;
    }

    var spend = document.getElementById("run-spend");
    if (spend && state.spend_gbp) {
      spend.textContent = "£" + state.spend_gbp;
    }

    if (!state.steps) {
      return;
    }

    /*
     * A step the page has never seen means the server knows about work this markup does
     * not describe. Reloading is the honest response: inventing a row here would mean two
     * places rendering a step, and the copy that drifts is always the one in JavaScript.
     */
    for (var i = 0; i < state.steps.length; i += 1) {
      var step = state.steps[i];
      var row = root.querySelector('[data-step="' + step.key + '"]');
      if (!row) {
        window.location.reload();
        return;
      }
      setField(row, "status", step.status);
      setField(row, "cost", "£" + step.cost_gbp);
      if (step.error) {
        setField(row, "error", step.error);
      }
    }
  }

  function setField(row, name, value) {
    var cell = row.querySelector('[data-field="' + name + '"]');
    if (cell) {
      cell.textContent = value;
    }
  }

  function dropFallback() {
    var fallback = document.getElementById("poll-fallback");
    if (fallback && fallback.parentNode) {
      fallback.parentNode.removeChild(fallback);
    }
  }
})();
