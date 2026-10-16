/**
 * Information library page: upload (with the SHA-1 duplicate check of
 * library_upload.js), and rename / describe a file.
 */
(function () {
  "use strict";

  const input = document.getElementById("library-file");

  /**
   * Highlight a row of the table (existing file found by the duplicate check).
   * @param {number} id File id.
   * @returns {boolean} true if the row is on this page.
   */
  function highlight(id) {
    const row = document.querySelector('#library-table tr[data-file="' + id + '"]');
    if (!row) return false;
    row.classList.add("table-active", "library-highlight");
    row.scrollIntoView({ block: "center", behavior: "smooth" });
    return true;
  }

  if (input) {
    input.addEventListener("change", async () => {
      const files = Array.from(input.files || []);
      input.value = "";
      let added = 0;
      let show = null;
      const errors = [];
      for (const file of files) {
        const res = await window.HMILibrary.upload(file, { useText: T("Show the existing file") });
        if (res.error) errors.push(file.name + ": " + res.error);
        else if (res.reused) show = res.file;
        else if (res.file) added += 1;
      }
      if (errors.length) await window.HMIDialog.alert(errors.join("\n"), { icon: "error", title: T("Upload failed") });
      if (added) {
        window.location.reload();
        return;
      }
      if (show && !highlight(show.id)) {
        window.location.href = window.location.pathname + "?q=" + encodeURIComponent(show.sha1);
      }
    });
  }

  // Rename / description
  const form = document.getElementById("library-edit-form");
  document.querySelectorAll(".library-edit").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const name = await window.HMIDialog.prompt({ title: T("Name"), value: btn.dataset.name, confirmText: T("Next"),
        validate: (v) => (String(v).trim() ? undefined : T("Enter a name.")) });
      if (name === undefined) return;
      const description = await window.HMIDialog.prompt({ title: T("Description"), value: btn.dataset.description,
        confirmText: T("Save") });
      if (description === undefined) return;
      form.action = btn.dataset.url;
      form.elements.name.value = name;
      form.elements.description.value = description;
      form.submit();
    });
  });
})();
