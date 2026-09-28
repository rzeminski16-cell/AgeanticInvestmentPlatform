/*
 * The report reader's spine (page specification §8.3): which section is being read, and
 * which have been.
 *
 * Chrome, and nothing else (ADR 0073). The spine is a list of plain anchors that work with
 * scripting off; this marks the one whose section is at the top of the screen and ticks the
 * ones the reader has moved past. The counts it shows under "This section" are the section's
 * own line, copied as the server wrote it — this computes nothing and formats nothing.
 */
(function () {
  "use strict";

  var spine = document.getElementById("report-spine");
  if (!spine || !("IntersectionObserver" in window)) {
    return;
  }

  var links = {};
  Array.prototype.forEach.call(spine.querySelectorAll("a[data-section]"), function (link) {
    links[link.getAttribute("data-section")] = link;
  });

  var counts = document.getElementById("spine-counts");
  var current = null;

  function mark(key) {
    if (key === current || !links[key]) {
      return;
    }
    if (current && links[current]) {
      links[current].classList.remove("aer-spine-current");
      links[current].removeAttribute("aria-current");
      links[current].classList.add("aer-spine-read");
    }
    current = key;
    links[key].classList.add("aer-spine-current");
    links[key].setAttribute("aria-current", "true");

    var own = document.querySelector('[data-counts-for="' + key + '"]');
    if (counts && own) {
      counts.textContent = own.textContent.replace(/\s+/g, " ").trim();
    }
  }

  // A section is the one being read once its top crosses the upper third of the screen.
  var observer = new IntersectionObserver(
    function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          mark(entry.target.getAttribute("data-section"));
        }
      });
    },
    { rootMargin: "0px 0px -66% 0px" }
  );

  Array.prototype.forEach.call(
    document.querySelectorAll("#report-body section[data-section]"),
    function (section) {
      observer.observe(section);
    }
  );
})();
