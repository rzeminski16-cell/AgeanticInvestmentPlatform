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
 *   * A `<meta http-equiv="refresh">` inside `noscript` reloads the page on a timer when
 *     scripting is off.
 *
 * The fallback lives in `noscript` rather than being removed here, because a declarative
 * refresh is scheduled when the element is *parsed* and removing it afterwards does not
 * cancel it — a bare meta tag would keep reloading the page underneath the stream.
 *
 * **The elapsed clock is the answer to "has this stalled?".** A model call takes minutes and
 * changes nothing in the database while it runs, so the state frames stop and the page sits
 * still. A ticking counter, started from the server's own record of when the step began,
 * distinguishes a working run from a dead one without pretending to know which it is.
 */
(function () {
  "use strict";

  var root = document.getElementById("run-console");
  if (!root) {
    return;
  }

  /*
   * Terminal runs are not watched. The stream would open, immediately emit `done` and
   * close, having told the page nothing it does not already show.
   */
  if (root.dataset.terminal === "true") {
    return;
  }

  if (typeof window.EventSource === "undefined") {
    /*
     * No stream available, and the noscript fallback did not fire because scripting is
     * on. The page still shows the state it was requested with; refreshing is manual.
     * A scripted browser without EventSource is a museum piece, and museum pieces get
     * the page, not a polling loop maintained for them alone.
     */
    return;
  }

  var note = document.getElementById("stream-note");
  if (note) {
    note.textContent = "Live. This page updates as the run progresses.";
  }

  /*
   * Started before the stream opens, not after. Opening the connection and waiting for the
   * first frame takes a moment, and a page whose clock only starts once the server has
   * spoken looks frozen for exactly as long as the thing it exists to rule out.
   */
  tick();
  window.setInterval(tick, 1000);

  var source = new EventSource(root.dataset.eventsUrl);

  source.addEventListener("state", function (event) {
    applyState(JSON.parse(event.data));
    tick();
  });

  source.addEventListener("heartbeat", function (event) {
    /*
     * Says the web process is reading the database, and nothing more than that. Worded to
     * claim exactly that much: a heartbeat proves this end is alive, not that the worker is.
     */
    seen(JSON.parse(event.data).at);
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

    setField(root, "steps-done", state.steps_done);
    setField(root, "steps-total", state.steps_total);
    summarise(state);

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
      row.dataset.startedAt = step.started_at || "";
      setField(row, "status", step.status);
      setField(row, "cost", "£" + step.cost_gbp);
      paintDot(row, step.status);
      if (step.error) {
        // The sentence, not the payload. `String(anObject)` is "[object Object]", which is
        // the single least useful thing this line could say.
        setField(row, "error", step.error.message || JSON.stringify(step.error));
        setField(row, "error-code", step.error.code || "");
      }
    }
  }

  function summarise(state) {
    var summary = root.querySelector('[data-field="summary"]');
    if (!summary) {
      return;
    }
    if (state.current_step) {
      summary.textContent = "Working on " + state.current_step + ".";
    } else if (state.status === "AWAITING_APPROVAL" || state.status === "BUDGET_EXCEEDED") {
      summary.textContent = "Stopped for you. Nothing is being spent.";
    } else {
      summary.textContent = "Queued. The worker picks this up within a second or two.";
    }
  }

  /*
   * Re-reads every row's start time and repaints its clock. Driven off the DOM rather than
   * off a remembered state object so that the server-rendered page ticks correctly before
   * the first frame arrives.
   */
  function tick() {
    var rows = root.querySelectorAll("[data-step]");
    for (var i = 0; i < rows.length; i += 1) {
      var row = rows[i];
      var cell = row.querySelector('[data-field="elapsed"]');
      if (!cell) {
        continue;
      }
      var status = row.querySelector('[data-field="status"]');
      var running = status && status.textContent.trim() === "RUNNING";
      cell.textContent = running ? since(row.dataset.startedAt) : "";
    }
  }

  function since(iso) {
    if (!iso) {
      return "";
    }
    var started = Date.parse(iso);
    if (isNaN(started)) {
      return "";
    }
    // Clamped at zero: a browser clock a few seconds behind the server's would otherwise
    // count down from a negative number, which looks like a bug because it is one.
    var seconds = Math.max(0, Math.floor((Date.now() - started) / 1000));
    var minutes = Math.floor(seconds / 60);
    var hours = Math.floor(minutes / 60);
    var body = pad(minutes % 60) + ":" + pad(seconds % 60);
    return hours > 0 ? hours + ":" + body : body;
  }

  function seen(iso) {
    var cell = root.querySelector('[data-field="last-seen"]');
    if (!cell || !iso) {
      return;
    }
    var at = new Date(iso);
    if (isNaN(at.getTime())) {
      return;
    }
    cell.textContent = "Server last checked at " + at.toLocaleTimeString() + ".";
  }

  function paintDot(row, status) {
    var dot = row.querySelector('[data-field="dot"]');
    if (!dot) {
      return;
    }
    dot.className = "inline-block h-2 w-2 shrink-0 rounded-full " + dotColour(status);
  }

  function dotColour(status) {
    if (status === "RUNNING") {
      return "animate-pulse bg-sky-500";
    }
    if (status === "SUCCEEDED") {
      return "bg-emerald-500";
    }
    if (status === "FAILED") {
      return "bg-red-500";
    }
    return "bg-slate-300 dark:bg-slate-700";
  }

  function pad(value) {
    return value < 10 ? "0" + value : String(value);
  }

  function setField(scope, name, value) {
    var cell = scope.querySelector('[data-field="' + name + '"]');
    if (cell) {
      cell.textContent = value;
    }
  }
})();
