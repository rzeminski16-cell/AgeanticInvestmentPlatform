/*
 * Application-wide HTMX configuration.
 *
 * A separate file rather than inline attributes: this is one decision that applies to
 * every form, and repeating it per form guarantees the one that gets forgotten is the one
 * that silently stops showing errors. It also keeps the pages free of inline script, so a
 * Content-Security-Policy can be tightened later without rewriting the templates.
 */
(function () {
  "use strict";

  if (typeof htmx === "undefined") {
    return;
  }

  /*
   * Swap the response body on a validation failure.
   *
   * By default HTMX only swaps 2xx responses; a 4xx is treated as an error and the
   * response body is discarded. That default is right for genuine errors and wrong for
   * this application: a rejected form submission returns 422 with the rendered error
   * list, and dropping it means the operator sees nothing happen at all.
   *
   * The alternative — returning 200 for a failed submission — would make the status line
   * lie, and this codebase relies on the status being meaningful in tests and in logs.
   * So the status stays honest and the client is told to render the body.
   *
   * 403 is included for the same reason: an expired CSRF token re-renders the form with
   * the operator's answers intact, and that page is worth showing.
   */
  htmx.config.responseHandling = [
    { code: "204", swap: false },
    { code: "[23]..", swap: true },
    { code: "(403|422)", swap: true, error: false },
    { code: "[45]..", swap: false, error: true },
  ];
})();
