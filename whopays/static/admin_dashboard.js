/* Admin dashboard (templates/admin/sunshine_index.html): copy links, and
   confirm before deleting a declaration. */
(function () {
  "use strict";

  // The links all start the same and differ at the end, so the boxes show
  // the end. The table makes a box narrower to keep Copy on its line, so a
  // box is put back at the end whenever it is resized, unless someone is
  // using it.
  const showEnd = (input) => {
    if (document.activeElement !== input) input.scrollLeft = input.scrollWidth;
  };
  document.querySelectorAll("input.link-url").forEach((input) => {
    showEnd(input);
    if (window.ResizeObserver) new ResizeObserver(() => showEnd(input)).observe(input);
  });

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
