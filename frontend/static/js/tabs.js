/* Remember the active Bootstrap tab per page so it survives form submissions and reloads. */
(function () {
  "use strict";
  const key = "tf-tab:" + location.pathname;
  const fromHash = location.hash && document.querySelector(`[data-bs-target="${location.hash.replace("#", "#pane-")}"]`);
  let saved = null;
  try { saved = sessionStorage.getItem(key); } catch (e) { saved = null; }
  const btn = fromHash || (saved && document.querySelector(`[data-bs-target="${saved}"]`));
  if (btn && window.bootstrap) bootstrap.Tab.getOrCreateInstance(btn).show();
  document.querySelectorAll('[data-bs-toggle="tab"]').forEach((el) => {
    el.addEventListener("shown.bs.tab", () => { try { sessionStorage.setItem(key, el.dataset.bsTarget); } catch (e) {} });
  });
})();
