/**
 * Tag forms: show "Who may write this value" only while "Operators may write
 * this value" is ticked, and the role list only for "Based on user roles".
 */
(function () {
  "use strict";

  const writable = document.getElementById("writable");
  const box = document.getElementById("write-access-box");
  const rolesBox = document.getElementById("write-roles-box");
  if (!writable || !box) return;

  /** Show or hide the parts that depend on the current choices. */
  function update() {
    box.hidden = !writable.checked;
    const mode = box.querySelector('input[name="write_access"]:checked');
    if (rolesBox) rolesBox.hidden = !mode || mode.value !== "roles";
  }

  writable.addEventListener("change", update);
  box.querySelectorAll('input[name="write_access"]').forEach((el) => el.addEventListener("change", update));
  update();
})();
