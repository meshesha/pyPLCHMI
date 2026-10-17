/**
 * Example pyPLCHMI UI template script – "example-plant".
 *
 * Template scripts run at the end of every page, after the pyPLCHMI scripts.
 * Keep them small and defensive: elements may be missing (login page, full-screen
 * frames), and never send data to other servers.
 *
 * This one adds a plant name under the logo of the sidebar and logs the alarm
 * count whenever pyPLCHMI reports new alarm data (event "hmi:alarms").
 */
(function () {
  "use strict";

  const head = document.querySelector(".hmi-sidebar-head");
  if (head) {
    const name = document.createElement("div");
    name.className = "example-plant-name";
    name.textContent = "Water treatment plant · Line 2";
    head.insertAdjacentElement("afterend", name);
  }

  document.addEventListener("hmi:alarms", (e) => {
    const unacked = e.detail.events.filter((ev) => !ev.acked_at).length;
    document.documentElement.dataset.examplePlantUnacked = String(unacked);
  });
})();
