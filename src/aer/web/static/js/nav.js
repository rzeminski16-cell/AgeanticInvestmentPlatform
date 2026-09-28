/*
  Close the menu when the window is too narrow to hold it beside the content.

  The markup ships `<details open>` and this closes it (decision B7). The direction is the
  whole point: revealing a *closed* `<details>` from author CSS is not reliably specified
  across engines — Chromium's `::details-content` carries `content-visibility: hidden`, which
  a `display` override on the child does not defeat — so a shell that shipped closed would
  depend on a behaviour that differs per browser at exactly the width where the index is the
  only way to navigate. Shipping open needs no reveal: at wide widths it is already open.

  **Fail open.** If this file never loads, never parses, or throws, every link stays on the
  screen at every width. The cost is a panel above the content at 320px; the cost of failing
  the other way is a menu button that does not open.

  It also does not fight the operator. Once they have toggled the index themselves, the width
  no longer moves it — a control that reopened itself on the next resize would be a control
  that ignores the person using it.
*/
(() => {
  const nav = document.getElementById("aer-menu");
  if (!nav) return;

  // The rail breakpoint, in one place. 960px is where the design system's workbench width
  // starts and where the CSS switches the same node from a disclosure to a persistent index.
  const wide = window.matchMedia("(min-width: 60rem)");
  let operatorDecided = false;

  nav.addEventListener("toggle", () => {
    // `toggle` fires for our own writes too, so only a real interaction counts. A summary
    // click moves focus into the disclosure, which is what distinguishes the two.
    if (nav.contains(document.activeElement)) operatorDecided = true;
  });

  const apply = () => {
    if (operatorDecided) return;
    nav.open = wide.matches;
  };

  apply();
  wide.addEventListener("change", apply);
})();

/*
  `/` focuses the search bar, as its key hint says. Progressive: the bar is a plain GET form
  that works without this, and the key is left alone wherever the operator is typing, so a
  slash in a thesis or a mandate is a slash.
*/
(() => {
  const search = document.getElementById("aer-search");
  if (!search) return;

  const typing = (target) =>
    target instanceof HTMLElement &&
    (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));

  document.addEventListener("keydown", (event) => {
    if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.defaultPrevented || typing(event.target)) return;
    event.preventDefault();
    search.focus();
  });
})();
