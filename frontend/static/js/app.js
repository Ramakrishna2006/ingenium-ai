/* Ingenium AI — progressive enhancement. Every feature here also works without JS
   (forms post normally); JS adds confirmations, loading states and live AI status. */
(function () {
  "use strict";
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";

  // ---- Sidebar toggle (mobile)
  document.querySelectorAll("[data-toggle-sidebar]").forEach((btn) => {
    btn.addEventListener("click", () => document.getElementById("sidebar")?.classList.toggle("open"));
  });

  // ---- Confirmation dialog for destructive / irreversible actions
  const modalEl = document.getElementById("confirmModal");
  let pendingForm = null;
  let pendingSubmitter = null;
  if (modalEl && window.bootstrap) {
    const modal = new bootstrap.Modal(modalEl);
    document.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-confirm]");
      if (!btn || btn.dataset.confirmed === "1") return;
      const form = btn.form || btn.closest("form");
      if (!form) return;
      if (!form.checkValidity()) return; // let the browser show validation errors first
      ev.preventDefault();
      pendingForm = form;
      pendingSubmitter = btn;
      document.getElementById("confirmBody").textContent = btn.dataset.confirm;
      modal.show();
    });
    document.getElementById("confirmOk").addEventListener("click", () => {
      modal.hide();
      if (pendingForm) {
        pendingSubmitter.dataset.confirmed = "1";
        if (pendingForm.requestSubmit) pendingForm.requestSubmit(pendingSubmitter);
        else pendingForm.submit();
      }
    });
  }

  // ---- Loading state on submit (prevents double submissions)
  document.addEventListener("submit", (ev) => {
    const form = ev.target;
    const btn = ev.submitter;
    if (!btn || !btn.hasAttribute("data-loading") || ev.defaultPrevented) return;
    // Disable on the next tick so the submitter's name/value is still included in the form data.
    void form;
    setTimeout(() => {
      btn.disabled = true;
      btn.dataset.label = btn.innerHTML;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span>' +
        (btn.dataset.loadingText || "Working…");
    }, 0);
  });
  window.addEventListener("pageshow", () => {
    document.querySelectorAll("button[data-loading][disabled]").forEach((b) => {
      b.disabled = false; if (b.dataset.label) b.innerHTML = b.dataset.label;
    });
  });

  // ---- Live AI status indicator (non-blocking)
  const pill = document.getElementById("ai-pill");
  if (pill) {
    const txt = pill.querySelector(".txt");
    const setState = (cls, label) => { pill.classList.remove("ok", "warn", "off"); pill.classList.add(cls); txt.textContent = label; };
    let cached = null;
    try { cached = JSON.parse(sessionStorage.getItem("tf-ai-status") || "null"); } catch (e) { cached = null; }
    const render = (s) => {
      if (!s.enabled) setState("off", "AI: disabled");
      else if (!s.reachable) setState("off", "AI: offline");
      else if (!s.model_available) setState("warn", "AI: model missing");
      else setState("ok", "AI: " + s.model);
    };
    if (cached && Date.now() - cached.t < 60000) render(cached.s);
    else fetch(pill.dataset.aiStatusUrl, { headers: { Accept: "application/json" } })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((s) => { render(s); try { sessionStorage.setItem("tf-ai-status", JSON.stringify({ t: Date.now(), s })); } catch (e) {} })
      .catch(() => setState("off", "AI: unknown"));
  }

  // ---- AI job description drafting (job form)
  const draftBtn = document.getElementById("ai-draft-btn");
  if (draftBtn) {
    draftBtn.addEventListener("click", async () => {
      const form = draftBtn.closest("form");
      const status = document.getElementById("ai-draft-status");
      const title = form.querySelector("[name=title]").value.trim();
      if (title.length < 2) { status.className = "small text-danger"; status.textContent = "Enter a job title first."; return; }
      const deptSel = form.querySelector("[name=department_id]");
      const payload = {
        title,
        department: deptSel.options[deptSel.selectedIndex]?.text || "",
        skills: form.querySelector("[name=required_skills]").value,
        notes: form.querySelector("[name=ai_notes]")?.value || "",
      };
      draftBtn.disabled = true;
      status.className = "small text-muted";
      status.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Drafting with the local AI model… this can take a minute.';
      try {
        const res = await fetch(draftBtn.dataset.url, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrf, Accept: "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data?.error?.message || "AI request failed");
        let text = data.description;
        if (data.responsibilities?.length) text += "\n\nResponsibilities:\n- " + data.responsibilities.join("\n- ");
        form.querySelector("[name=description]").value = text;
        if (data.qualifications?.length) form.querySelector("[name=qualifications]").value = data.qualifications.join("\n");
        const skills = form.querySelector("[name=required_skills]");
        if (!skills.value.trim() && data.suggested_skills?.length) skills.value = data.suggested_skills.join(", ");
        status.className = "small text-success";
        status.textContent = "Draft inserted. Review and edit before saving — AI text may contain mistakes.";
      } catch (err) {
        status.className = "small text-danger";
        status.textContent = err.message + " You can still write the description manually.";
      } finally {
        draftBtn.disabled = false;
      }
    });
  }

  // ---- Filters that apply immediately on change (a <noscript> button covers no-JS use)
  document.querySelectorAll("[data-autosubmit]").forEach((el) => {
    el.addEventListener("change", () => el.form && el.form.submit());
  });

  // ---- Toggle helpers: show/hide dependent fields (e.g. reason boxes)
  document.querySelectorAll("[data-show-when]").forEach((el) => {
    const [name, values] = el.dataset.showWhen.split("=");
    const form = el.closest("form");
    const ctrl = form?.querySelector(`[name="${name}"]`);
    if (!ctrl) return;
    const update = () => { el.hidden = !values.split(",").includes(ctrl.value); };
    ctrl.addEventListener("change", update); update();
  });
})();
