document.addEventListener("DOMContentLoaded", () => {
  const shell = document.getElementById("portal-shell");
  const toggle = document.getElementById("menu-toggle");
  if (shell && toggle) {
    toggle.addEventListener("click", () => {
      const mobile = window.matchMedia("(max-width: 760px)").matches;
      const className = mobile ? "mobile-sidebar-open" : "sidebar-collapsed";
      shell.classList.toggle(className);
      toggle.setAttribute("aria-expanded", String(
        mobile ? shell.classList.contains(className) : !shell.classList.contains(className)
      ));
    });
  }
  document.querySelectorAll("[data-password-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = button.parentElement.querySelector("input");
      const reveal = input.type === "password";
      input.type = reveal ? "text" : "password";
      button.textContent = reveal ? "Hide" : "Show";
      button.setAttribute("aria-label", reveal ? "Hide password" : "Show password");
    });
  });
});
