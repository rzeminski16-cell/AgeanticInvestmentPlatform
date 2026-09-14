/* Visual prototype only. Production theme state is server-stamped from a cookie. */
(function () {
  const root = document.documentElement;
  const themeButtons = Array.from(document.querySelectorAll("[data-theme-choice]"));
  const storedTheme = window.localStorage.getItem("tracework-prototype-theme") || "system";

  function setTheme(choice) {
    if (choice === "system") {
      root.removeAttribute("data-theme");
    } else {
      root.dataset.theme = choice;
    }
    themeButtons.forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.themeChoice === choice));
    });
    window.localStorage.setItem("tracework-prototype-theme", choice);
  }

  themeButtons.forEach((button) => {
    button.addEventListener("click", () => setTheme(button.dataset.themeChoice));
  });
  setTheme(storedTheme);

  const navShell = document.querySelector(".nav-shell");
  const wideNavigation = window.matchMedia("(min-width: 60.001rem)");
  function syncNavigation(event) {
    if (!navShell) return;
    navShell.open = event.matches;
  }
  syncNavigation(wideNavigation);
  wideNavigation.addEventListener("change", syncNavigation);

  const backdrop = document.querySelector("[data-drawer-backdrop]");
  const drawer = backdrop?.querySelector("[role='dialog']");
  const closeButton = backdrop?.querySelector("[data-drawer-close]");
  const drawerTitle = backdrop?.querySelector("[data-drawer-title]");
  const drawerBody = backdrop?.querySelector("[data-drawer-body]");
  let returnFocus = null;

  function focusableElements() {
    if (!drawer) return [];
    return Array.from(
      drawer.querySelectorAll("a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])")
    );
  }

  function closeDrawer() {
    if (!backdrop) return;
    backdrop.dataset.open = "false";
    backdrop.hidden = true;
    document.body.style.overflow = "";
    returnFocus?.focus();
  }

  document.querySelectorAll("[data-drawer-open]").forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      if (!backdrop || !drawer) return;
      event.preventDefault();
      returnFocus = trigger;
      drawerTitle.textContent = trigger.dataset.drawerTitle || "Preview";
      drawerBody.innerHTML = trigger.dataset.drawerContent || "<p>This full-page link becomes a preview drawer when scripting is available.</p>";
      backdrop.hidden = false;
      backdrop.dataset.open = "true";
      document.body.style.overflow = "hidden";
      closeButton?.focus();
    });
  });

  closeButton?.addEventListener("click", closeDrawer);
  backdrop?.addEventListener("mousedown", (event) => {
    if (event.target === backdrop) closeDrawer();
  });

  document.addEventListener("keydown", (event) => {
    if (!backdrop || backdrop.hidden) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeDrawer();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = focusableElements();
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
})();
