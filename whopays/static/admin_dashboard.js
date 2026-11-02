/* Admin dashboard (templates/admin/sunshine_index.html): copy links, and
   confirm before deleting a declaration. */
(function () {
  "use strict";

  document.addEventListener("click", async (event) => {
    const button = event.target.closest(".copy-link");
    if (!button) return;
    const input = document.getElementById(button.dataset.for);
    try {
      await navigator.clipboard.writeText(input.value);
    } catch (e) {
      // No clipboard access (e.g. not https): select it and copy the old way.
      input.select();
      document.execCommand("copy");
    }
    button.textContent = "Copied";
    clearTimeout(button.resetTimer);
    button.resetTimer = setTimeout(() => { button.textContent = "Copy"; }, 2000);
  });

  document.addEventListener("submit", (event) => {
    const question = event.target.dataset.confirm;
    if (question && !window.confirm(question)) event.preventDefault();
  });
})();
