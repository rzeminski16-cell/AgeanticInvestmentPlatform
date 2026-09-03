// A form whose choice leads to the fields it needs.
//
// A radio group that decides which of two branches counts — a premise defeated by a
// threshold or reviewed by a person, a transaction typed or documented — used to show both
// branches at once and leave the reader to work out which fields the choice made live. This
// hides the branch the choice did not make. Chrome and nothing else (ADR 0077): the radio's
// value is what the server reads, every branch is rendered and submitted exactly as before,
// and a reload loses nothing, because nothing here is state.
//
// Progressive enhancement, and the `hidden` attribute is the whole of it: with scripting off
// every branch stays on the screen, which is the page as it was, not a form with fields the
// reader cannot reach.
//
// The contract with a page is two attributes: `data-branches` on the element containing the
// radios, naming the radio group; `data-branch` on each branch, carrying the radio value that
// shows it. A radio value the branches never name hides them all, so a branch is never shown
// for a choice it does not belong to.

(function () {
  "use strict";

  var forms = document.querySelectorAll("[data-branches]");

  Array.prototype.forEach.call(forms, function (form) {
    var group = form.getAttribute("data-branches");
    var radios = Array.prototype.filter.call(
      form.querySelectorAll('input[type="radio"]'),
      function (radio) {
        return radio.name === group;
      }
    );
    var branches = form.querySelectorAll("[data-branch]");
    if (!radios.length || !branches.length) {
      return;
    }

    function reveal() {
      var chosen = null;
      radios.forEach(function (radio) {
        if (radio.checked) {
          chosen = radio.value;
        }
      });
      // No choice yet: every branch stays visible, exactly as with scripting off.
      Array.prototype.forEach.call(branches, function (branch) {
        branch.hidden = chosen !== null && branch.getAttribute("data-branch") !== chosen;
      });
    }

    radios.forEach(function (radio) {
      radio.addEventListener("change", reveal);
    });
    reveal();
  });
})();
