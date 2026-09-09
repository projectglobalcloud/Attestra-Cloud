/* Attestra Cloud console application JS. Vanilla, no dependencies. */

"use strict";

/* ------------------------------ helpers ------------------------------ */

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || `Request failed (${res.status})`);
  }
  return data;
}

function toast(message, kind = "ok") {
  const root = document.getElementById("toast-root");
  const el = document.createElement("div");
  el.className = "toast" + (kind === "error" ? " toast-error" : " toast-ok");
  el.textContent = message;
  root.appendChild(el);
  setTimeout(() => el.remove(), 6500);
}

function busy(button, on, label) {
  if (!button) return;
  if (on) {
    button.dataset.label = button.innerHTML;
    button.innerHTML = `<span class="spinner"></span>${label || "Working…"}`;
    button.disabled = true;
  } else {
    button.innerHTML = button.dataset.label || button.innerHTML;
    button.disabled = false;
  }
}

async function pollJob(jobId, onTick, intervalMs = 1500) {
  for (;;) {
    const { job } = await api(`/api/jobs/${jobId}`);
    if (onTick) onTick(job);
    if (job.status !== "running") return job;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

function csv(value) {
  return value.split(",").map((s) => s.trim()).filter(Boolean);
}

/* Drawer factory: returns {open, close, body} */
function makeDrawer(title) {
  const root = document.getElementById("overlay-root");
  const backdrop = document.createElement("div");
  backdrop.className = "drawer-backdrop";
  backdrop.hidden = true;
  const drawer = document.createElement("aside");
  drawer.className = "drawer";
  drawer.hidden = true;
  drawer.innerHTML =
    `<div class="drawer-head"><h2>${title}</h2>` +
    `<button class="drawer-close" aria-label="Close">&times;</button></div>` +
    `<div class="drawer-body"></div>`;
  root.appendChild(backdrop);
  root.appendChild(drawer);
  const close = () => { backdrop.hidden = true; drawer.hidden = true; };
  backdrop.addEventListener("click", close);
  drawer.querySelector(".drawer-close").addEventListener("click", close);
  return {
    open: () => { backdrop.hidden = false; drawer.hidden = false; },
    close,
    body: drawer.querySelector(".drawer-body"),
    setTitle: (t) => { drawer.querySelector("h2").textContent = t; },
  };
}

/* ------------------------------ tabs --------------------------------- */

function initTabs(scope) {
  (scope || document).querySelectorAll("[data-tabs]").forEach((bar) => {
    const tabs = bar.querySelectorAll(".tab");
    tabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        tabs.forEach((t) => t.classList.remove("active"));
        tab.classList.add("active");
        const paneRoot = document.querySelector(bar.dataset.tabs);
        paneRoot.querySelectorAll(":scope > .tabpane").forEach((p) => p.classList.remove("active"));
        paneRoot.querySelector(tab.dataset.pane).classList.add("active");
        if (location.hash !== tab.dataset.pane) history.replaceState(null, "", tab.dataset.pane);
      });
    });
    if (location.hash) {
      const target = bar.querySelector(`[data-pane="${location.hash}"]`);
      if (target) target.click();
    }
  });
}

/* ------------------------- table kit (lists) -------------------------- */
/* A card marked data-table gets: client-side text filter via
   [data-table-search], pagination via data-page-size, a Total count in
   [data-table-total], pager buttons in [data-table-pager]. */

const TableKit = {
  init(scope) {
    (scope || document).querySelectorAll("[data-table]").forEach((wrap) => {
      const table = wrap.querySelector("table");
      if (!table || !table.tBodies.length) return;
      const rows = Array.from(table.tBodies[0].rows);
      const input = wrap.querySelector("[data-table-search]");
      const totalEl = wrap.querySelector("[data-table-total]");
      const pagerEl = wrap.querySelector("[data-table-pager]");
      const sizeSel = wrap.querySelector("[data-table-size]");
      let pageSize = parseInt(wrap.dataset.pageSize || "0", 10);
      let page = 1;

      const matches = () => {
        const q = (input ? input.value : "").trim().toLowerCase();
        return rows.filter((r) => !q || r.textContent.toLowerCase().includes(q));
      };

      const render = () => {
        const vis = matches();
        const pages = pageSize ? Math.max(1, Math.ceil(vis.length / pageSize)) : 1;
        page = Math.min(page, pages);
        rows.forEach((r) => { r.style.display = "none"; });
        const slice = pageSize ? vis.slice((page - 1) * pageSize, page * pageSize) : vis;
        slice.forEach((r) => { r.style.display = ""; });
        if (totalEl) totalEl.textContent = vis.length;
        if (pagerEl) {
          pagerEl.innerHTML = "";
          const btn = (label, p, opts = {}) => {
            const b = document.createElement("button");
            b.innerHTML = label;
            if (opts.cur) b.className = "cur";
            b.disabled = !!opts.dis;
            b.addEventListener("click", () => { page = p; render(); });
            pagerEl.appendChild(b);
          };
          btn("&lsaquo;", Math.max(1, page - 1), { dis: page === 1 });
          for (let p = 1; p <= pages; p++) {
            if (pages > 7 && p > 2 && p < pages - 1 && Math.abs(p - page) > 1) {
              if (pagerEl.lastChild && pagerEl.lastChild.textContent !== "…") {
                const dots = document.createElement("button");
                dots.textContent = "…"; dots.disabled = true;
                pagerEl.appendChild(dots);
              }
              continue;
            }
            btn(String(p), p, { cur: p === page });
          }
          btn("&rsaquo;", Math.min(pages, page + 1), { dis: page === pages });
        }
      };

      if (input) input.addEventListener("input", () => { page = 1; render(); });
      if (sizeSel) sizeSel.addEventListener("change", () => {
        pageSize = parseInt(sizeSel.value, 10); page = 1; render();
      });
      render();
    });

    (scope || document).querySelectorAll("[data-refresh]").forEach((b) =>
      b.addEventListener("click", () => location.reload()));
  },
};

/* --------------------------- app shell -------------------------------- */

const AppShell = {
  FAV_KEY: "attestra.favs",
  NAV_KEY: "attestra.nav.collapsed",

  init() {
    this.initProductDrawer();
    this.initDropdowns();
    this.initRegion();
    this.initSearch();
    this.initCollapse();
    this.initCopy();
    this.initFavStars();
    initTabs();
    TableKit.init();
  },

  /* products & services drawer */
  initProductDrawer() {
    const drawer = document.getElementById("prod-drawer");
    const backdrop = document.getElementById("prod-backdrop");
    const toggle = document.getElementById("menu-toggle");
    if (!drawer || !toggle) return;
    const close = () => { drawer.hidden = true; backdrop.hidden = true; };
    const open = () => {
      drawer.hidden = false; backdrop.hidden = false;
      const f = document.getElementById("prod-filter");
      if (f) { f.value = ""; this.filterProducts(""); setTimeout(() => f.focus(), 30); }
    };
    toggle.addEventListener("click", () => (drawer.hidden ? open() : close()));
    backdrop.addEventListener("click", close);
    document.getElementById("prod-close").addEventListener("click", close);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
    const filter = document.getElementById("prod-filter");
    if (filter) filter.addEventListener("input", () => this.filterProducts(filter.value));
  },

  filterProducts(q) {
    q = q.trim().toLowerCase();
    document.querySelectorAll(".prod-item").forEach((it) => {
      it.style.display = !q || (it.dataset.name || "").includes(q) ? "" : "none";
    });
    document.querySelectorAll(".prod-group").forEach((g) => {
      const any = Array.from(g.querySelectorAll(".prod-item")).some((i) => i.style.display !== "none");
      g.style.display = any ? "" : "none";
    });
  },

  /* generic top bar dropdowns */
  initDropdowns() {
    document.querySelectorAll("[data-drop]").forEach((drop) => {
      const btn = drop.querySelector("[data-drop-btn]");
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const was = drop.classList.contains("open");
        document.querySelectorAll("[data-drop].open").forEach((d) => d.classList.remove("open"));
        if (!was) drop.classList.add("open");
      });
    });
    document.addEventListener("click", () =>
      document.querySelectorAll("[data-drop].open").forEach((d) => d.classList.remove("open")));
  },

  initRegion() {
    document.querySelectorAll("[data-region]").forEach((b) =>
      b.addEventListener("click", async () => {
        try {
          await api("/api/region", { method: "POST", body: JSON.stringify({ region: b.dataset.region }) });
          location.reload();
        } catch (err) { toast(err.message, "error"); }
      }));
  },

  /* global search over services + datasets */
  initSearch() {
    const input = document.getElementById("tb-search-input");
    const box = document.getElementById("tb-search-results");
    if (!input || !box) return;
    let index = { pages: [], datasets: [] };
    try { index = JSON.parse(document.getElementById("search-index").textContent); } catch (e) { /* noop */ }
    const all = [...(index.pages || []), ...(index.datasets || [])];
    let sel = -1;

    const render = () => {
      const q = input.value.trim().toLowerCase();
      if (!q) { box.hidden = true; return; }
      const hits = all.filter((it) => it.label.toLowerCase().includes(q)).slice(0, 9);
      box.innerHTML = hits.length
        ? hits.map((h, i) =>
            `<a href="${h.href}" class="${i === sel ? "sel" : ""}"><span>${h.label}</span><span class="kind">${h.kind}</span></a>`).join("")
        : `<div class="none">No matches for “${input.value.trim()}”.</div>`;
      box.hidden = false;
    };

    input.addEventListener("input", () => { sel = -1; render(); });
    input.addEventListener("keydown", (e) => {
      const links = box.querySelectorAll("a");
      if (e.key === "ArrowDown") { sel = Math.min(sel + 1, links.length - 1); render(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { sel = Math.max(sel - 1, 0); render(); e.preventDefault(); }
      else if (e.key === "Enter" && links.length) { (links[Math.max(sel, 0)]).click(); }
      else if (e.key === "Escape") { box.hidden = true; input.blur(); }
    });
    input.addEventListener("blur", () => setTimeout(() => { box.hidden = true; }, 160));
    document.addEventListener("keydown", (e) => {
      if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
        e.preventDefault(); input.focus();
      }
    });
  },

  initCollapse() {
    const frame = document.getElementById("frame");
    const btn = document.getElementById("sn-collapse");
    if (!frame || !btn) return;
    if (localStorage.getItem(this.NAV_KEY) === "1") frame.classList.add("nav-collapsed");
    btn.addEventListener("click", () => {
      frame.classList.toggle("nav-collapsed");
      localStorage.setItem(this.NAV_KEY, frame.classList.contains("nav-collapsed") ? "1" : "0");
    });
  },

  initCopy() {
    document.addEventListener("click", (e) => {
      const b = e.target.closest("[data-copy]");
      if (!b) return;
      navigator.clipboard.writeText(b.dataset.copy).then(
        () => toast("Copied to clipboard."),
        () => toast("Could not copy.", "error"));
    });
  },

  favs() {
    try { return JSON.parse(localStorage.getItem(this.FAV_KEY)) || []; }
    catch (e) { return []; }
  },

  initFavStars() {
    const favs = this.favs();
    document.querySelectorAll(".prod-star").forEach((star) => {
      if (favs.some((f) => f.label === star.dataset.fav)) star.classList.add("faved");
      star.addEventListener("click", (e) => {
        e.preventDefault(); e.stopPropagation();
        let list = this.favs();
        if (list.some((f) => f.label === star.dataset.fav)) {
          list = list.filter((f) => f.label !== star.dataset.fav);
          star.classList.remove("faved");
        } else {
          list.push({ label: star.dataset.fav, href: star.dataset.href });
          star.classList.add("faved");
        }
        localStorage.setItem(this.FAV_KEY, JSON.stringify(list));
        this.renderShortcuts();
      });
    });
    this.renderShortcuts();
  },

  /* Frequently visited chips on the Workbench */
  renderShortcuts() {
    const holder = document.getElementById("wb-favs");
    if (!holder) return;
    const defaults = JSON.parse(holder.dataset.defaults || "[]");
    const favs = this.favs();
    const list = favs.length ? favs : defaults;
    holder.innerHTML = list.map((f) =>
      `<a class="wb-shortcut" href="${f.href}">` +
      `<svg viewBox="0 0 20 20"><path d="M10 2.8l2.2 4.6 5 .7-3.6 3.5.9 5-4.5-2.4-4.5 2.4.9-5L2.8 8.1l5-.7z"/></svg>` +
      `${f.label}</a>`).join("");
  },
};

/* --------------------------- storage list ---------------------------- */

const StoragePage = {
  init(opts) {
    const regions = (opts && opts.regions) || [];
    const currentRegion = (opts && opts.currentRegion) || "";
    this.parties = (opts && opts.parties) || [];
    this.regions = regions;
    this.currentRegion = currentRegion;
    const drawer = makeDrawer("Protect a dataset");
    this.drawer = drawer;
    let currentName = null;

    const regionOptions = regions.map(([code, label]) =>
      `<option value="${code}" ${code === currentRegion ? "selected" : ""}>${label} (${code})</option>`).join("");

    const formHTML = (name, owners) => `
      <p class="muted" style="margin-bottom:14px">Protecting <strong>${name}</strong> generates
      independent signing keys for every listed organization, seals each storage block with a
      combined integrity tag, and registers the verification parameters with the auditor.</p>
      <div class="form-stack">
        <label>Region
          <select id="ob-region">${regionOptions}</select>
        </label>
        <label>Co-owning organizations <span class="hint">(comma-separated)</span>
          <input type="text" id="ob-owners" value="${owners}">
        </label>
        <div class="form-row">
          <label>Blocks to protect <span class="hint">(4–200)</span>
            <input type="number" id="ob-blocks" value="24" min="4" max="200">
          </label>
          <label>Block size
            <select id="ob-blocksize">
              <option value="65536">64 KB</option>
              <option value="262144" selected>256 KB</option>
              <option value="1048576">1 MB</option>
            </select>
          </label>
        </div>
        <p class="field-note" id="ob-estimate"></p>
        <button class="btn btn-primary btn-block" id="ob-go">Protect dataset</button>
      </div>
      <div id="ob-progress" hidden>
        <div class="progress"><div class="progress-bar" id="ob-bar"></div></div>
        <p class="progress-note" id="ob-status">Starting…</p>
        <ul class="stepper" id="ob-steps">
          <li id="obs-1"><span class="step-dot">1</span><div class="step-body">
            <h4>Prepare storage blocks</h4><p>Segmenting the shard and fingerprinting each block.</p></div></li>
          <li id="obs-2"><span class="step-dot">2</span><div class="step-body">
            <h4>Issue signing keys</h4><p>Each organization receives an independent key pair.</p></div></li>
          <li id="obs-3"><span class="step-dot">3</span><div class="step-body">
            <h4>Seal blocks</h4><p>Combined integrity tags are computed for every block.</p></div></li>
          <li id="obs-4"><span class="step-dot">4</span><div class="step-body">
            <h4>Register with auditor</h4><p>Verification parameters are published for continuous auditing.</p></div></li>
        </ul>
      </div>`;

    const updateEstimate = () => {
      const owners = csv(document.getElementById("ob-owners").value).length || 1;
      const blocks = parseInt(document.getElementById("ob-blocks").value, 10) || 24;
      const est = Math.max(1, Math.round((owners * blocks * 55) / 1000));
      document.getElementById("ob-estimate").textContent =
        `Estimated provisioning time: about ${est}s for ${owners} organization(s) × ${blocks} blocks.`;
    };

    document.querySelectorAll("[data-onboard]").forEach((btn) => {
      btn.addEventListener("click", () => {
        currentName = btn.dataset.onboard;
        drawer.setTitle(`Protect ${currentName}`);
        drawer.body.innerHTML = formHTML(currentName, btn.dataset.owners);
        drawer.open();
        updateEstimate();
        ["ob-owners", "ob-blocks"].forEach((id) =>
          document.getElementById(id).addEventListener("input", updateEstimate));
        document.getElementById("ob-go").addEventListener("click", () => this.run(currentName, drawer));
      });
    });

    const uploadBtn = document.getElementById("upload-dataset");
    if (uploadBtn) uploadBtn.addEventListener("click", () => this.openUpload());

    const all = document.getElementById("protect-all");
    if (all) all.addEventListener("click", () => this.protectAll(all));

    document.querySelectorAll("[data-protect-dataset]").forEach((btn) => {
      btn.addEventListener("click", () => this.openProtect(btn.dataset.protectDataset, {
        name: btn.dataset.name,
        owners: btn.dataset.owners || "",
        blockSize: btn.dataset.blocksize || "262144",
      }));
    });

    document.querySelectorAll("[data-delete-dataset]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm("Remove this dataset and its history from the console?")) return;
        try {
          await api(`/api/datasets/${btn.dataset.deleteDataset}`, { method: "DELETE" });
          location.reload();
        } catch (err) { toast(err.message, "error"); }
      });
    });
  },

  /* ---------------- uploading a file of your own ---------------- */

  regionOptions() {
    return this.regions.map(([code, label]) =>
      `<option value="${code}"${code === this.currentRegion ? " selected" : ""}>${label} (${code})</option>`).join("");
  },

  partyOptions() {
    return ['<option value="">Not assigned yet</option>'].concat(
      this.parties.map((p) =>
        `<option value="${p.id}" data-owners="${(p.owner_ids || []).join(', ')}">` +
        `${p.name} — ${p.type_label}${p.verified ? " ✓" : ""}</option>`)).join("");
  },

  /* The protection steps, shared by upload-and-protect and protect-later.
     Every one of them is a real stage of the job the server is running. */
  stepsHTML(withTransfer) {
    const rows = [];
    if (withTransfer) rows.push(["Transfer the file to this server",
      "The bytes are written to local storage and fingerprinted with SHA-256."]);
    rows.push(["Prepare storage blocks",
      "The file is cut into fixed-size blocks and each one reduced to a field element."]);
    rows.push(["Issue signing keys",
      "Every named owner receives an independent key pair and a private mask."]);
    rows.push(["Seal blocks",
      "One aggregated integrity tag is computed for each block."]);
    rows.push(["Register with the auditor",
      "Verification parameters are published so the data can be challenged."]);
    return `<div class="progress"><div class="progress-bar" data-bar></div></div>
      <p class="progress-note" data-status>Starting…</p>
      <ul class="stepper" data-steps>` +
      rows.map(([h, note], i) =>
        `<li><span class="step-dot">${i + 1}</span><div class="step-body">
          <h4>${h}</h4><p>${note}</p></div></li>`).join("") +
      `</ul>
      <details class="techlog"><summary>Protocol log</summary>
        <pre class="joblog" data-log></pre></details>`;
  },

  /* Follow a protection job and drive the stepper from what it actually
     reports — the log lines are emitted by the protocol layer itself. */
  async trackProtection(jobId, root, offset, doneMessage) {
    const steps = [...root.querySelectorAll("[data-steps] li")];
    const bar = root.querySelector("[data-bar]");
    const statusEl = root.querySelector("[data-status]");
    const logEl = root.querySelector("[data-log]");
    const mark = (i, cls) => { if (steps[i]) steps[i].className = cls; };
    const started = Date.now();
    mark(offset, "running");
    const job = await pollJob(jobId, (j) => {
      const est = (j.result && j.result.estimate_s) || 30;
      const frac = Math.min(0.95, (Date.now() - started) / 1000 / est);
      bar.style.width = (j.status === "done" ? 100 : Math.max(6, frac * 100)) + "%";
      statusEl.textContent = j.message || "Working…";
      if (logEl) { logEl.textContent = j.log.join("\n"); logEl.scrollTop = logEl.scrollHeight; }
      const text = j.log.join(" ");
      if (text.includes("KeyGen")) { mark(offset, "done"); mark(offset + 1, "running"); }
      if (text.includes("TagGen: computing")) { mark(offset + 1, "done"); mark(offset + 2, "running"); }
      if (text.includes("Stored")) { mark(offset + 2, "done"); mark(offset + 3, "running"); }
    }, 1200);
    if (job.status === "error") throw new Error(job.error);
    steps.forEach((_, i) => mark(i, "done"));
    bar.style.width = "100%";
    statusEl.textContent = doneMessage;
    return job;
  },

  openUpload() {
    const d = this.drawer;
    d.setTitle("Upload a dataset");
    d.body.innerHTML = `
      <p class="muted" style="margin-bottom:14px">The file is stored on this server, beside the
      console's own database. Protecting it seals every block with the owners' keys — the file
      itself never leaves this machine.</p>
      <form class="form-stack" id="up-form">
        <label>File
          <input type="file" name="file" id="up-file" required>
          <span class="field-note" id="up-filenote">Any file. Only the first blocks are
            protected, so a large archive stays quick to demonstrate.</span>
        </label>
        <div class="form-row">
          <label>Dataset name
            <input type="text" name="name" id="up-name" placeholder="radiology-archive-2026" required>
          </label>
          <label>Dataset ID <span class="hint">(optional)</span>
            <input type="text" name="custom_ref" id="up-ref" placeholder="auto-generated">
          </label>
        </div>
        <div class="form-row">
          <label>Region
            <select name="region" id="up-region">${this.regionOptions()}</select>
          </label>
          <label>Monitoring
            <select name="audit_every_min" id="up-sched">
              <option value="">Off</option>
              <option value="5">Verify every 5 minutes</option>
              <option value="15">Verify every 15 minutes</option>
              <option value="30">Verify every 30 minutes</option>
              <option value="60">Verify every hour</option>
              <option value="240">Verify every 4 hours</option>
            </select>
          </label>
        </div>
        <label>Ownership — held by
          <select name="holder_party_id" id="up-holder">${this.partyOptions()}</select>
          <span class="field-note">The party on the ownership register that owns this data.
            A group of individuals brings all of its members as co-owners.</span>
        </label>
        <label>Key holders <span class="hint">(comma-separated)</span>
          <input type="text" name="owners" id="up-owners" placeholder="one identifier per owner">
        </label>
        <label class="check"><input type="checkbox" id="up-protect" checked>
          Protect immediately after upload</label>
        <div class="form-row" id="up-advanced">
          <label>Blocks to protect <span class="hint">(4–200)</span>
            <input type="number" id="up-blocks" value="24" min="4" max="200">
          </label>
          <label>Block size
            <select id="up-blocksize">
              <option value="65536">64 KB</option>
              <option value="262144" selected>256 KB</option>
              <option value="1048576">1 MB</option>
            </select>
          </label>
        </div>
        <p class="field-note" id="up-estimate"></p>
        <button class="btn btn-primary btn-block" type="submit" id="up-go">Upload dataset</button>
      </form>
      <div id="up-progress" hidden>${this.stepsHTML(true)}</div>`;
    d.open();

    const file = d.body.querySelector("#up-file");
    const name = d.body.querySelector("#up-name");
    const owners = d.body.querySelector("#up-owners");
    const holder = d.body.querySelector("#up-holder");
    const blocks = d.body.querySelector("#up-blocks");
    const protectNow = d.body.querySelector("#up-protect");
    const advanced = d.body.querySelector("#up-advanced");
    const estimate = d.body.querySelector("#up-estimate");

    const refreshEstimate = () => {
      const n = csv(owners.value).length || 1;
      const b = parseInt(blocks.value, 10) || 24;
      estimate.textContent = protectNow.checked
        ? `Estimated protection time: about ${Math.max(1, Math.round((n * b * 55) / 1000))}s `
          + `for ${n} owner(s) × ${b} blocks.`
        : "The file will be stored without protection. You can protect it later from this page.";
    };
    file.addEventListener("change", () => {
      const f = file.files[0];
      if (!f) return;
      if (!name.value) name.value = f.name.replace(/\.[^.]+$/, "").slice(0, 120);
      d.body.querySelector("#up-filenote").textContent =
        `${f.name} — ${(f.size / 1048576).toFixed(2)} MB`;
    });
    holder.addEventListener("change", () => {
      const opt = holder.selectedOptions[0];
      owners.value = (opt && opt.dataset.owners) || "";
      refreshEstimate();
    });
    protectNow.addEventListener("change", () => {
      advanced.hidden = !protectNow.checked;
      refreshEstimate();
    });
    [owners, blocks].forEach((el) => el.addEventListener("input", refreshEstimate));
    refreshEstimate();

    d.body.querySelector("#up-form").addEventListener("submit", (e) => {
      e.preventDefault();
      this.runUpload(d);
    });
  },

  runUpload(d) {
    const file = d.body.querySelector("#up-file").files[0];
    const goBtn = d.body.querySelector("#up-go");
    if (!file) { toast("Choose a file to upload.", "error"); return; }

    const protectNow = d.body.querySelector("#up-protect").checked;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", d.body.querySelector("#up-name").value.trim());
    fd.append("custom_ref", d.body.querySelector("#up-ref").value.trim());
    fd.append("region", d.body.querySelector("#up-region").value);
    fd.append("audit_every_min", d.body.querySelector("#up-sched").value);
    fd.append("holder_party_id", d.body.querySelector("#up-holder").value);
    fd.append("owners", d.body.querySelector("#up-owners").value);
    fd.append("protect_now", protectNow ? "1" : "0");
    fd.append("max_blocks", d.body.querySelector("#up-blocks").value);
    fd.append("block_size", d.body.querySelector("#up-blocksize").value);

    const progress = d.body.querySelector("#up-progress");
    progress.hidden = false;
    goBtn.hidden = true;
    const steps = [...progress.querySelectorAll("[data-steps] li")];
    const bar = progress.querySelector("[data-bar]");
    const statusEl = progress.querySelector("[data-status]");
    steps[0].className = "running";

    const fail = (message) => {
      toast(message, "error");
      steps.forEach((li) => { if (li.className === "running") li.className = "failed"; });
      statusEl.textContent = message;
      goBtn.hidden = false;
      busy(goBtn, false);
    };

    // XHR rather than fetch: it reports real upload progress for a large file.
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/datasets/upload");
    xhr.upload.addEventListener("progress", (ev) => {
      if (!ev.lengthComputable) return;
      const pct = (ev.loaded / ev.total) * 100;
      bar.style.width = (protectNow ? pct * 0.25 : pct) + "%";
      statusEl.textContent =
        `Transferring ${(ev.loaded / 1048576).toFixed(1)} of ${(ev.total / 1048576).toFixed(1)} MB…`;
    });
    xhr.addEventListener("error", () => fail("The upload could not reach the server."));
    xhr.addEventListener("load", async () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch (_) { /* handled below */ }
      if (xhr.status !== 200 || data.ok === false) {
        fail(data.error || `The upload failed (${xhr.status}).`);
        return;
      }
      steps[0].className = "done";
      statusEl.textContent = `Stored ${(data.bytes / 1048576).toFixed(2)} MB · SHA-256 ${data.sha256.slice(0, 16)}…`;
      if (!data.job_id) {
        bar.style.width = "100%";
        steps.slice(1).forEach((li) => { li.className = ""; });
        toast(`${data.ref} uploaded and held on this server.`);
        setTimeout(() => location.reload(), 1200);
        return;
      }
      try {
        const job = await this.trackProtection(data.job_id, progress, 1,
          "Dataset protected. Continuous verification is available.");
        toast(`Dataset protected — ${job.result.n_blocks} blocks sealed.`);
        setTimeout(() => location.reload(), 1200);
      } catch (err) {
        fail(err.message);
      }
    });
    xhr.send(fd);
  },

  /* Seal everything that needs it, in turn. The server does them one at a
     time and names each one as it goes, so this just follows along. */
  async protectAll(button) {
    const count = button.dataset.count;
    if (!confirm(`Protect ${count} dataset(s) again?\n\n`
               + "Each one is sealed with the same owners it had before. This takes a "
               + "few seconds per dataset.")) return;
    const panel = document.getElementById("protect-all-panel");
    const bar = document.getElementById("pa-bar");
    const statusEl = document.getElementById("pa-status");
    const stepsEl = document.getElementById("pa-steps");
    const pill = document.getElementById("pa-pill");
    const pillText = document.getElementById("pa-pill-text");
    busy(button, true, "Protecting…");
    panel.hidden = false;
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    try {
      const started = await api("/api/datasets/protect-all", { method: "POST" });
      const job = await pollJob(started.job_id, (j) => {
        const stages = (j.result && j.result.stages) || [];
        const done = stages.filter((st) => st.state === "done").length;
        const failed = stages.filter((st) => st.state === "failed").length;
        bar.style.width = Math.max(4, ((done + failed) / stages.length) * 100) + "%";
        statusEl.textContent = j.message || "Working…";
        stepsEl.innerHTML = stages.map((st, i) => `
          <li class="${st.state === "done" ? "done" : (st.state === "running" ? "running"
              : (st.state === "failed" ? "failed" : ""))}">
            <span class="step-dot">${i + 1}</span>
            <div class="step-body">
              <h4>${st.label}</h4>
              <p>${st.detail ? st.detail + (st.ms ? ` — ${st.ms} ms` : "")
                : (st.state === "running" ? "sealing now…" : "waiting")}</p>
            </div>
          </li>`).join("");
      }, 900);
      if (job.status === "error") throw new Error(job.error || "the run failed");
      const r = job.result || {};
      bar.style.width = "100%";
      pill.className = "live-pill" + (r.failed ? " is-err" : "");
      pillText.textContent = r.failed ? "Finished with problems" : "Done";
      document.getElementById("pa-heading").textContent = "Protection complete";
      toast(r.failed
        ? `${r.sealed} dataset(s) protected, ${r.failed} could not be — see the list.`
        : `All ${r.sealed} dataset(s) are protected again.`,
        r.failed ? "error" : "ok");
      setTimeout(() => location.reload(), r.failed ? 3500 : 1600);
    } catch (err) {
      pill.className = "live-pill is-err";
      pillText.textContent = "Failed";
      statusEl.textContent = err.message;
      toast(err.message, "error");
      busy(button, false);
    }
  },

  openProtect(datasetId, info) {
    const d = this.drawer;
    d.setTitle(`Protect ${info.name}`);
    d.body.innerHTML = `
      <p class="muted" style="margin-bottom:14px">This file is already held on this server.
      Protecting it issues a key pair to every named owner, seals each block with a combined
      integrity tag and publishes the verification parameters to the auditor.</p>
      <form class="form-stack" id="pr-form">
        <label>Key holders <span class="hint">(comma-separated)</span>
          <input type="text" id="pr-owners" value="${info.owners}" required>
        </label>
        <div class="form-row">
          <label>Blocks to protect <span class="hint">(4–200)</span>
            <input type="number" id="pr-blocks" value="24" min="4" max="200">
          </label>
          <label>Block size
            <select id="pr-blocksize">
              <option value="65536"${info.blockSize === "65536" ? " selected" : ""}>64 KB</option>
              <option value="262144"${info.blockSize === "262144" ? " selected" : ""}>256 KB</option>
              <option value="1048576"${info.blockSize === "1048576" ? " selected" : ""}>1 MB</option>
            </select>
          </label>
        </div>
        <button class="btn btn-primary btn-block" type="submit" id="pr-go">Protect dataset</button>
      </form>
      <div id="pr-progress" hidden>${this.stepsHTML(false)}</div>`;
    d.open();
    d.body.querySelector("#pr-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const goBtn = d.body.querySelector("#pr-go");
      const progress = d.body.querySelector("#pr-progress");
      busy(goBtn, true, "Protecting…");
      try {
        const { job_id } = await api(`/api/datasets/${datasetId}/protect`, {
          method: "POST",
          body: JSON.stringify({
            owners: d.body.querySelector("#pr-owners").value,
            max_blocks: parseInt(d.body.querySelector("#pr-blocks").value, 10),
            block_size: parseInt(d.body.querySelector("#pr-blocksize").value, 10),
          }),
        });
        progress.hidden = false;
        goBtn.hidden = true;
        const job = await this.trackProtection(job_id, progress, 0,
          "Dataset protected. Continuous verification is available.");
        toast(`Dataset protected — ${job.result.n_blocks} blocks sealed.`);
        setTimeout(() => location.reload(), 1200);
      } catch (err) {
        toast(err.message, "error");
        busy(goBtn, false);
        goBtn.hidden = false;
      }
    });
  },

  async run(name, drawer) {
    const owners = csv(document.getElementById("ob-owners").value);
    const goBtn = document.getElementById("ob-go");
    const steps = ["obs-1", "obs-2", "obs-3", "obs-4"].map((id) => document.getElementById(id));
    const mark = (i, cls) => { steps[i].className = cls; };
    try {
      busy(goBtn, true, "Provisioning…");
      const { job_id } = await api("/api/datasets/onboard", {
        method: "POST",
        body: JSON.stringify({
          name, owners,
          region: document.getElementById("ob-region").value,
          max_blocks: parseInt(document.getElementById("ob-blocks").value, 10),
          block_size: parseInt(document.getElementById("ob-blocksize").value, 10),
        }),
      });
      document.getElementById("ob-progress").hidden = false;
      goBtn.hidden = true;
      const bar = document.getElementById("ob-bar");
      const statusEl = document.getElementById("ob-status");
      const started = Date.now();
      mark(0, "running");
      const job = await pollJob(job_id, (j) => {
        const est = (j.result && j.result.estimate_s) || 30;
        const frac = Math.min(0.95, (Date.now() - started) / 1000 / est);
        bar.style.width = (j.status === "done" ? 100 : frac * 100) + "%";
        statusEl.textContent = j.message || "Working…";
        const logText = j.log.join(" ");
        if (logText.includes("KeyGen")) { mark(0, "done"); mark(1, "running"); }
        if (logText.includes("TagGen: computing")) { mark(1, "done"); mark(2, "running"); }
        if (logText.includes("Stored")) { mark(2, "done"); mark(3, "running"); }
      });
      if (job.status === "error") throw new Error(job.error);
      steps.forEach((s, i) => mark(i, "done"));
      statusEl.textContent = "Dataset protected. Continuous auditing is available.";
      toast(`${name} is now protected (${job.result.n_blocks} blocks).`);
      setTimeout(() => location.reload(), 1100);
    } catch (err) {
      toast(err.message, "error");
      busy(goBtn, false);
      goBtn.hidden = false;
    }
  },
};

/* ------------------------- the workbench feed ------------------------- */
/* The console narrates its own work. Everything here is read from the jobs
   the server is actually running — the stage names, their timings and the
   BLS values are the operation's own, not a script of what usually happens. */

const WorkbenchPage = {
  init() {
    this.list = document.getElementById("pf-list");
    this.empty = document.getElementById("pf-empty");
    this.pill = document.getElementById("pf-pill");
    this.pillText = document.getElementById("pf-pill-text");
    this.summary = document.getElementById("pf-summary");
    if (!this.list) return;
    this.open = new Set();          // which rows the reader has expanded
    this.tick();
    this.timer = setInterval(() => this.tick(), 1500);
    document.addEventListener("visibilitychange", () => {
      // Polling a hidden tab is just noise on somebody's laptop battery.
      if (document.hidden) {
        clearInterval(this.timer);
        this.pill.className = "live-pill is-off";
        this.pillText.textContent = "Paused";
      } else {
        this.timer = setInterval(() => this.tick(), 1500);
        this.pill.className = "live-pill";
        this.pillText.textContent = "Live";
        this.tick();
      }
    });
  },

  async tick() {
    let data;
    try {
      data = await api("/api/processes");
    } catch (err) {
      this.pill.className = "live-pill is-err";
      this.pillText.textContent = "Reconnecting";
      return;
    }
    if (!document.hidden) {
      this.pill.className = "live-pill";
      this.pillText.textContent = data.running ? `${data.running} running` : "Live";
    }
    const rows = (data.processes || []).slice(0, 6);
    this.empty.hidden = rows.length > 0;
    this.summary.textContent = this.headline(data.processes || [], data.running);
    this.list.innerHTML = rows.map((p) => this.row(p)).join("");
    this.list.querySelectorAll("[data-open]").forEach((el) => {
      el.addEventListener("click", () => {
        const id = el.dataset.open;
        if (this.open.has(id)) this.open.delete(id); else this.open.add(id);
        this.tick();
      });
    });
  },

  /* One sentence for the whole panel: what is happening right now, or what
     just finished. This is the line somebody reads first. */
  headline(all, running) {
    const live = all.filter((p) => p.status === "running");
    if (live.length) {
      const p = live[0];
      const step = (p.stages || []).find((st) => st.state === "running");
      const what = step ? step.label.toLowerCase() : (p.message || "working");
      return `${p.title} — ${what}${live.length > 1
        ? `, and ${live.length - 1} other operation${live.length > 2 ? "s" : ""}` : ""}.`;
    }
    if (!all.length) return "";
    const done = all[0];
    const cert = done.result && done.result.certificate_id;
    return `Last: ${done.title.toLowerCase()} — ${done.status === "error"
      ? "did not complete" : "finished"} in ${done.elapsed_s}s${cert
      ? `, deed ${cert} issued` : ""}.`;
  },

  /* One compact line per operation. The steps are there for anyone who wants
     them, but folded away — the point of this panel is the summary. */
  row(p) {
    const state = p.status === "running" ? "run"
      : (p.status === "error" ? "err" : "ok");
    const label = { run: "Running", err: "Failed", ok: "Done" }[state];
    const stages = p.stages || [];
    const done = stages.filter((st) => st.state === "done").length;
    const current = stages.find((st) => st.state === "running");
    const pct = p.status === "done" ? 100
      : (stages.length ? Math.round((done / stages.length) * 100)
        : Math.round((p.progress || 0) * 100));
    const line = p.status === "running"
      ? (current ? current.label : (p.message || "working"))
      : (p.status === "error"
        ? (p.error || "did not complete")
        : (p.result && p.result.certificate_id
          ? `deed ${p.result.certificate_id} issued`
          : `${stages.length || "all"} steps completed in ${p.elapsed_s}s`));
    const expanded = this.open.has(p.id);

    const detail = expanded && stages.length ? `
      <ul class="stepper pf-steps">
        ${stages.map((st, i) => `
          <li class="${st.state === "done" ? "done" : (st.state === "running" ? "running"
              : (st.state === "failed" ? "failed" : ""))}">
            <span class="step-dot">${i + 1}</span>
            <div class="step-body"><h4>${this.escape(st.label)}</h4>
              <p>${st.detail ? this.escape(st.detail) + (st.ms ? ` — ${st.ms} ms` : "")
                : "&nbsp;"}</p></div>
          </li>`).join("")}
      </ul>` : "";

    return `
      <article class="pf-line pf-${state}">
        <button class="pf-toggle" data-open="${p.id}" aria-expanded="${expanded}">
          <span class="pf-caret">${expanded ? "▾" : "▸"}</span>
          <span class="pf-title">${this.escape(p.title)}</span>
          <span class="pf-now">${this.escape(line)}</span>
          <span class="pf-meter"><i style="width:${pct}%"></i></span>
          <span class="pf-state pf-state-${state}">${label}</span>
        </button>
        ${detail}
      </article>`;
  },

  escape(text) {
    const d = document.createElement("div");
    d.textContent = text == null ? "" : String(text);
    return d.innerHTML;
  },
};

/* --------------------------- storage detail --------------------------- */

const StorageDetailPage = {
  init(datasetId) {
    this.id = datasetId;

    document.querySelectorAll("form[data-action]").forEach((form) => {
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        this.submit(form);
      });
    });

    // continuous verification schedule
    const schedToggle = document.getElementById("sched-toggle");
    const schedSel = document.getElementById("sched-interval");
    if (schedToggle) {
      const push = async () => {
        const every = schedToggle.checked ? parseInt(schedSel.value, 10) : null;
        try {
          await api(`/api/datasets/${this.id}/schedule`, {
            method: "POST", body: JSON.stringify({ every_min: every }),
          });
          toast(every
            ? `Continuous verification enabled — every ${every} minute(s).`
            : "Continuous verification disabled.");
          setTimeout(() => location.reload(), 800);
        } catch (err) { toast(err.message, "error"); }
      };
      schedToggle.addEventListener("change", push);
      schedSel.addEventListener("change", () => { if (schedToggle.checked) push(); });
    }
  },

  async submit(form) {
    const action = form.dataset.action;
    const button = form.querySelector("button[type=submit], button:not([type])");
    const payload = {};
    const get = (n) => form.querySelector(`[name=${n}]`);
    if (get("c")) payload.c = parseInt(get("c").value, 10);
    if (get("stale")) payload.stale = get("stale").checked;
    if (get("index")) payload.index = parseInt(get("index").value, 10);
    if (get("owners")) payload.owners = csv(get("owners").value);
    if (get("buyer_group")) payload.buyer_group = get("buyer_group").value.trim();
    if (get("buyer_owners")) payload.buyer_owners = csv(get("buyer_owners").value);

    const labels = {
      audit: "Verifying…", corrupt: "Applying…", restore: "Restoring…",
      "owners/add": "Adding…", "owners/revoke": "Removing…", transfer: "Transferring…",
    };
    busy(button, true, labels[action]);
    try {
      const { result } = await api(`/api/datasets/${this.id}/${action}`, {
        method: "POST", body: JSON.stringify(payload),
      });
      if (action === "audit") {
        this.showAuditResult(form, result);
      } else if (action === "transfer") {
        toast(`Ownership transferred to ${result.buyer}.`);
        setTimeout(() => location.reload(), 900);
      } else if (action === "owners/add" || action === "owners/revoke") {
        toast(`Ownership group updated (${result.owners.length} members).`);
        setTimeout(() => location.reload(), 900);
      } else if (action === "corrupt") {
        toast(`Block ${payload.index} was modified at the storage layer. Run a verification to see it detected.`, "error");
        setTimeout(() => location.reload(), 1200);
      } else {
        toast("Storage restored to a healthy state.");
        setTimeout(() => location.reload(), 900);
      }
    } catch (err) {
      toast(err.message, "error");
    } finally {
      busy(button, false);
    }
  },

  showAuditResult(form, r) {
    const box = document.getElementById("audit-result");
    if (!box) return;
    const cls = r.passed ? "verdict-good" : "verdict-bad";
    const title = r.passed ? "Verification passed" : "Verification failed";
    const detail = r.passed
      ? "The storage provider proved possession of every challenged block."
      : (r.stale
        ? "These verification parameters belong to the previous owner and are no longer valid."
        : "The storage provider could not prove possession — data has been altered or lost.");
    box.innerHTML =
      `<div class="verdict ${cls}" style="margin-top:12px">
        <h4>${title}</h4>
        <p>${r.challenged} of ${r.total_blocks} blocks challenged</p>
        <small>${detail} Completed in ${Math.round(r.duration_ms)} ms with a fixed-size proof.</small>
      </div>`;
    if (!r.passed) toast("Verification failed — see details on the Integrity tab.", "error");
    else toast("Verification passed.");
  },
};

/* ----------------------- looking inside a dataset --------------------- */
/* Renders whatever the file turns out to be: a table for delimited data, the
   text for text, the media itself for anything a browser can play, and a hex
   window for the rest. The server decides which; this only draws it. */

const DatasetContents = {
  init(datasetId) {
    this.id = datasetId;
    this.body = document.getElementById("contents-body");
    if (!this.body) return;
    const load = () => { if (!this.loaded) { this.loaded = true; this.load(); } };
    // On a protected dataset the card lives behind a tab, so it is read the
    // first time that tab is opened. On one that is not protected there are no
    // tabs — the card stands alone and is read straight away.
    const tab = document.querySelector('[data-pane="#tab-contents"]');
    if (!tab) { load(); return; }
    tab.addEventListener("click", load);
    if (location.hash === "#tab-contents") load();
  },

  async load() {
    try {
      const { preview } = await api(`/api/datasets/${this.id}/preview`);
      this.body.innerHTML = this.render(preview);
    } catch (err) {
      this.body.innerHTML = `<div class="empty"><p>${WorkbenchPage.escape(err.message)}</p></div>`;
    }
  },

  facts(p) {
    const rows = [
      ["File", p.filename],
      ["Type", `${p.mime}${p.kind === "binary" ? " — not text" : ""}`],
      ["Size", p.size_display],
      ["Source", p.source],
    ];
    if (p.sha256) rows.push(["SHA-256 at upload", p.sha256]);
    return `<dl class="resource-meta" style="margin:0 0 14px">
      ${rows.map(([k, v]) => `<div><dt>${k}</dt><dd class="${k.startsWith("SHA") ? "mono" : ""}"
        style="${k.startsWith("SHA") ? "font-size:11px;word-break:break-all" : ""}">${WorkbenchPage.escape(v)}</dd></div>`).join("")}
    </dl>`;
  },

  render(p) {
    if (p.kind === "missing") {
      return `<div class="empty"><p>${WorkbenchPage.escape(p.note)}</p></div>`;
    }
    const url = `/api/datasets/${this.id}/file`;
    let view = "";

    if (p.kind === "table") {
      view = `
        <div class="table-scroll">
          <table class="table">
            <thead><tr>${p.columns.map((c) =>
              `<th>${WorkbenchPage.escape(c)}</th>`).join("")}</tr></thead>
            <tbody>${p.rows.map((row) => `<tr>${row.map((cell) =>
              `<td>${WorkbenchPage.escape(cell)}</td>`).join("")}</tr>`).join("")}</tbody>
          </table>
        </div>
        <p class="field-note">First ${p.shown_rows} row(s) of ${p.columns.length} column(s)${
          p.truncated ? ", read from the front of the file" : ""}.</p>`;
    } else if (p.kind === "text") {
      view = `<pre class="joblog" style="max-height:460px">${WorkbenchPage.escape(p.text)}</pre>
        <p class="field-note">${p.shown_lines
          ? `First ${p.shown_lines} line(s)` : "The start of the file"}${
          p.truncated ? ", read from the front of the file" : ""}.</p>`;
    } else if (p.kind === "image") {
      view = `<img src="${url}" alt="${WorkbenchPage.escape(p.filename)}"
        style="max-width:100%;border:1px solid var(--line);border-radius:var(--r)">`;
    } else if (p.kind === "video") {
      view = `<video src="${url}" controls style="max-width:100%;border-radius:var(--r)"></video>`;
    } else if (p.kind === "audio") {
      view = `<audio src="${url}" controls style="width:100%"></audio>`;
    } else if (p.kind === "pdf") {
      view = `<iframe src="${url}" title="${WorkbenchPage.escape(p.filename)}"
        style="width:100%;height:620px;border:1px solid var(--line);border-radius:var(--r)"></iframe>`;
    } else {
      view = `<pre class="joblog" style="max-height:420px">${WorkbenchPage.escape(p.text || "")}</pre>
        <p class="field-note">${WorkbenchPage.escape(p.note || "")}</p>`;
    }
    return this.facts(p) + view;
  },
};

/* --------------------------- integrity page --------------------------- */

const IntegrityPage = {
  init() {
    this.startCountdowns();
    const form = document.getElementById("run-audit-form");
    if (!form) return;
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      this.run(form);
    });
  },

  /* Scheduled verifications tick down to their next automatic run. The
     starting value comes from the scheduler itself, not from the page load. */
  startCountdowns() {
    const cells = [...document.querySelectorAll("[data-countdown]")];
    if (!cells.length) return;
    const left = cells.map((cell) => ({ cell, s: parseInt(cell.dataset.countdown, 10) || 0 }));
    const tick = () => {
      left.forEach((row) => {
        if (row.s <= 0) { row.cell.textContent = "due now"; return; }
        row.s -= 1;
        const m = Math.floor(row.s / 60);
        row.cell.textContent = `${m}:${String(row.s % 60).padStart(2, "0")}`;
      });
    };
    tick();
    setInterval(tick, 1000);
  },

  /* Run one verification and show the protocol doing it. Every number on the
     panel is measured server-side by the step it belongs to. */
  async run(form) {
    const btn = form.querySelector("button");
    const select = form.querySelector("[name=dataset_id]");
    const id = select.value;
    const label = select.selectedOptions[0].textContent.split("·")[0].trim();
    const c = parseInt(form.querySelector("[name=c]").value, 10);

    const panel = document.getElementById("live-verify");
    const steps = [...document.querySelectorAll("#lv-steps li")];
    const bar = document.getElementById("lv-bar");
    const statusEl = document.getElementById("lv-status");
    const pill = document.getElementById("lv-pill");
    const pillText = document.getElementById("lv-pill-text");
    const logEl = document.getElementById("lv-log");
    const verdict = document.getElementById("lv-verdict");
    const meta = document.getElementById("lv-meta");

    panel.hidden = false;
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    document.getElementById("lv-subject").textContent =
      `${label} · challenging ${c} block(s)`;
    steps.forEach((li, i) => {
      li.className = "";
      const note = document.getElementById(`lv-d${i}`);
      if (note && note.dataset.original) note.textContent = note.dataset.original;
    });
    verdict.innerHTML = "";
    meta.hidden = true;
    logEl.textContent = "";
    bar.style.width = "0%";
    pill.className = "live-pill";
    pillText.textContent = "Running";
    document.getElementById("lv-heading").textContent = "Verification in progress";
    statusEl.textContent = "Starting…";
    busy(btn, true, "Verifying…");

    try {
      const { job_id } = await api(`/api/datasets/${id}/verify-run`, {
        method: "POST", body: JSON.stringify({ c }),
      });
      // Polled quickly: the whole exchange takes well under a second on demo
      // sizes, and the point of the panel is to show it happening.
      const job = await pollJob(job_id, (j) => {
        statusEl.textContent = j.message || "Working…";
        logEl.textContent = j.log.join("\n");
        const stages = (j.result && j.result.stages) || [];
        stages.forEach((st, i) => {
          if (!steps[i]) return;
          steps[i].className = st.state === "done" ? "done"
            : (st.state === "running" ? "running" : "");
          if (st.detail) {
            const note = document.getElementById(`lv-d${i}`);
            if (note) {
              if (!note.dataset.original) note.dataset.original = note.textContent;
              note.textContent = `${st.detail} — ${st.ms} ms`;
            }
          }
        });
        const done = stages.filter((st) => st.state === "done").length;
        bar.style.width = Math.max(8, (done / 4) * 100) + "%";
      }, 250);

      if (job.status === "error") throw new Error(job.error);
      const r = job.result;
      bar.style.width = "100%";
      (r.stages || []).forEach((st, i) => {
        if (!steps[i]) return;
        steps[i].className = st.state === "done" ? "done" : steps[i].className;
        if (st.detail) {
          const note = document.getElementById(`lv-d${i}`);
          if (note) note.textContent = `${st.detail} — ${st.ms} ms`;
        }
      });
      // The pairing check is the step that decides it, so a failure is shown
      // there rather than as a general error.
      if (!r.passed) steps[2].className = "failed";
      pill.className = "live-pill" + (r.passed ? "" : " is-err");
      pillText.textContent = r.passed ? "Passed" : "Failed";
      document.getElementById("lv-heading").textContent = "Verification complete";
      statusEl.textContent = `Completed in ${Math.round(r.duration_ms)} ms · recorded as entry #${r.audit_id}`;
      verdict.innerHTML =
        `<div class="verdict ${r.passed ? "verdict-good" : "verdict-bad"}">
          <h4>${r.passed ? "Verification passed" : "Verification failed"}</h4>
          <p>${r.challenged} of ${r.total_blocks} blocks challenged</p>
          <small>${r.passed
            ? "The storage provider proved possession of every challenged block without sending any data."
            : "The provider could not prove possession — the data has been altered, lost, or these parameters are no longer valid."}</small>
        </div>`;
      document.getElementById("lv-cov").textContent =
        `${r.challenged} / ${r.total_blocks} blocks`;
      // The same figure the log column prints — the conservative PDP bound —
      // so one run never appears to have two different confidences.
      document.getElementById("lv-conf").textContent =
        `${r.detection_pct.toFixed(1)}% for a 1% corruption`;
      document.getElementById("lv-proof").textContent = `${r.proof_bytes} bytes (constant)`;
      document.getElementById("lv-latency").textContent =
        `${Math.round(r.duration_ms)} ms — challenge ${r.timings.challenge_ms}, ` +
        `proof ${r.timings.proof_ms}, verify ${r.timings.verify_ms}`;
      meta.hidden = false;
      this.prependLogRow(r, label);
      toast(r.passed
        ? `Verification passed — ${r.challenged}/${r.total_blocks} blocks in ${Math.round(r.duration_ms)} ms.`
        : "Verification FAILED — the provider could not prove possession.",
        r.passed ? "ok" : "error");
    } catch (err) {
      pill.className = "live-pill is-err";
      pillText.textContent = "Error";
      document.getElementById("lv-heading").textContent = "Verification could not run";
      statusEl.textContent = err.message;
      steps.forEach((li) => { if (li.className === "running") li.className = "failed"; });
      toast(err.message, "error");
    } finally {
      busy(btn, false);
    }
  },

  /* Put the run that just finished at the top of the log, so the page tells
     the truth without a reload throwing the panel away. */
  prependLogRow(r, label) {
    const body = document.getElementById("audit-log-body");
    if (!body) return;
    const now = new Date();
    const when = `${String(now.getDate()).padStart(2, "0")} ` +
      `${now.toLocaleString("en-GB", { month: "short" }).slice(0, 3)} ${now.getFullYear()}, ` +
      `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td class="muted">${when}</td><td>${label}</td>` +
      `<td class="num">${r.challenged}/${r.total_blocks}</td>` +
      `<td class="num">${r.detection_pct.toFixed(1)}%</td>` +
      `<td class="num">${Math.round(r.duration_ms)} ms</td>` +
      `<td><span class="st st-${r.passed ? "ok" : "err"}">${r.passed ? "Passed" : "Failed"}</span></td>`;
    body.prepend(tr);
    const total = document.querySelector("[data-table-total]");
    if (total) total.textContent = String(parseInt(total.textContent, 10) + 1);
    const stats = document.querySelectorAll(".stat-value");
    if (stats[0]) stats[0].textContent = String(parseInt(stats[0].textContent, 10) + 1);
    const counter = r.passed ? stats[1] : stats[2];
    if (counter) {
      counter.innerHTML = `<span class="${r.passed ? "up" : "down"}">` +
        `${parseInt(counter.textContent, 10) + 1}</span>`;
    }
  },
};

/* --------------------------- transfers page --------------------------- */

const TransfersPage = {
  init(buyers) {
    const drawer = makeDrawer("Transfer ownership");
    const btn = document.getElementById("new-transfer");
    if (!btn) return;
    btn.addEventListener("click", () => {
      const options = JSON.parse(document.getElementById("transfer-datasets").textContent);
      if (!options.length) { toast("No active datasets available to transfer.", "error"); return; }
      drawer.body.innerHTML = `
        <p class="muted" style="margin-bottom:14px">Ownership transfer hands the acquiring
        organization the ability to verify the data — and permanently removes it from the
        current owners. The data itself never moves or re-uploads.</p>
        <div class="form-stack">
          <label>Dataset
            <select id="tr-dataset">
              ${options.map((o) => `<option value="${o.id}">${o.name} — currently ${o.group}</option>`).join("")}
            </select>
          </label>
          <label>Acquiring organization (group name)
            <input type="text" id="tr-buyer" value="">
          </label>
          <label>Member organizations <span class="hint">(comma-separated)</span>
            <input type="text" id="tr-owners" value="">
          </label>
          <button class="btn btn-primary btn-block" id="tr-go">Execute transfer</button>
          <p class="field-note">Both parties confirm the transaction; the storage layer applies a
          fixed-size ownership update to every block. Typical completion: under 5 seconds
          at demonstration sizes.</p>
        </div>`;
      const fill = () => {
        const sel = document.getElementById("tr-dataset");
        const ds = options.find((o) => String(o.id) === sel.value) || options[0];
        const suggestion = buyers[ds.name] || ["acquiring-organization", ["acquiring-organization"]];
        document.getElementById("tr-buyer").value = suggestion[0];
        document.getElementById("tr-owners").value = suggestion[1].join(", ");
      };
      fill();
      document.getElementById("tr-dataset").addEventListener("change", fill);
      document.getElementById("tr-go").addEventListener("click", async () => {
        const go = document.getElementById("tr-go");
        busy(go, true, "Transferring…");
        try {
          const id = document.getElementById("tr-dataset").value;
          const { result } = await api(`/api/datasets/${id}/transfer`, {
            method: "POST",
            body: JSON.stringify({
              buyer_group: document.getElementById("tr-buyer").value.trim(),
              buyer_owners: csv(document.getElementById("tr-owners").value),
            }),
          });
          toast(`Ownership transferred to ${result.buyer} in ${Math.round(result.duration_ms)} ms.`);
          setTimeout(() => location.reload(), 1000);
        } catch (err) {
          toast(err.message, "error");
          busy(go, false);
        }
      });
      drawer.open();
    });
  },
};

/* --------------------------- security page ---------------------------- */

const SecurityPage = {
  PLANS: {
    collusion: {
      title: "Collusion resistance",
      steps: [
        { key: "env", label: "Provision isolated test environment", note: "A reference deployment with a legacy-scheme baseline and the production protocol." },
        { key: "legacy", label: "Execute forgery against legacy scheme", note: "The simulated adversary holds a leaked verification secret and cooperates with the storage layer.", marker: /prior|legacy|scheme \[7\]|\[8\]/i },
        { key: "ours", label: "Execute the same forgery against Attestra", note: "Identical adversary, identical knowledge, against the production protocol.", marker: /proposed|RESISTED/i },
        { key: "post", label: "Confirm legitimate operations unaffected", note: "Honest verifications must continue to pass.", marker: /honest|collateral/i },
      ],
      verdictNote: "An acquiring party cooperating with the storage provider cannot fabricate proofs for data that was never part of the transaction.",
    },
    rogue_key: {
      title: "Key-substitution resistance",
      steps: [
        { key: "env", label: "Provision isolated test environment", note: "Honest organizations publish keys; the adversary registers a crafted key." },
        { key: "legacy", label: "Execute impersonation against naive aggregation", note: "The crafted key lets the adversary claim joint ownership under legacy aggregation.", marker: /plain|naive|aggregate/i },
        { key: "ours", label: "Execute the same impersonation against Attestra", note: "Key registration is bound to proof-of-possession-style key hashing.", marker: /proposed|RESISTED/i },
        { key: "post", label: "Confirm genuine joint signatures still verify", note: "The defence must not break the legitimate multi-party case.", marker: /genuine|still/i },
      ],
      verdictNote: "No party can claim co-ownership of a dataset that its organizations did not actually sign.",
    },
    tampering: {
      title: "Tamper detection",
      steps: [
        { key: "env", label: "Provision isolated test environment", note: "A protected dataset is stored, then modified at the storage layer." },
        { key: "legacy", label: "Corrupt and delete storage blocks", note: "Single-byte modifications and full block deletions are applied.", marker: /corrupt|delet/i },
        { key: "ours", label: "Run verifications across corruption scenarios", note: "Every challenged corruption must be detected.", marker: /detect|caught|CORRECT/i },
        { key: "post", label: "Measure statistical detection coverage", note: "Hundreds of sampling trials, compared against the theoretical model.", marker: /sampl|trial|theor/i },
      ],
      verdictNote: "Unauthorized modification or deletion of stored data is detected by routine verification.",
    },
  },

  init() {
    document.querySelectorAll("[data-validation]").forEach((btn) => {
      btn.addEventListener("click", () => this.run(btn.dataset.validation));
    });
  },

  async run(name) {
    const plan = this.PLANS[name];
    const panel = document.getElementById("validation-run");
    panel.hidden = false;
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
    document.getElementById("vr-title").textContent = plan.title;
    document.getElementById("vr-status").innerHTML = `<span class="st st-info">In progress</span>`;
    const stepsEl = document.getElementById("vr-steps");
    stepsEl.innerHTML = plan.steps.map((s, i) =>
      `<li id="vs-${i}"><span class="step-dot">${i + 1}</span>
        <div class="step-body"><h4>${s.label}</h4><p>${s.note}</p></div></li>`).join("");
    document.getElementById("vr-verdicts").innerHTML = "";
    const logEl = document.getElementById("vr-log");
    logEl.textContent = "";
    document.querySelectorAll("[data-validation]").forEach((b) => (b.disabled = true));

    const mark = (i, cls) => { const el = document.getElementById(`vs-${i}`); if (el) el.className = cls; };
    mark(0, "running");

    try {
      const { job_id } = await api(`/api/attacks/${name}`, { method: "POST" });
      let phase = 0;
      const job = await pollJob(job_id, (j) => {
        logEl.textContent = j.log.join("\n");
        const text = j.log.join("\n");
        if (phase === 0 && j.log.length > 3) { mark(0, "done"); mark(1, "running"); phase = 1; }
        for (let i = phase; i < plan.steps.length - 1; i++) {
          const m = plan.steps[i].marker;
          if (m && m.test(text)) { mark(i, "done"); mark(i + 1, "running"); phase = i + 1; }
        }
      }, 2000);
      if (job.status === "error") throw new Error(job.error);
      plan.steps.forEach((_, i) => mark(i, "done"));

      const text = job.log.join("\n");
      const legacyBroken = /BROKEN/i.test(text);
      const oursResisted = /RESISTED|CORRECT|CONFIRMED/i.test(text);
      document.getElementById("vr-status").innerHTML =
        `<span class="st st-ok">Completed · ${job.elapsed_s}s</span>`;
      document.getElementById("vr-verdicts").innerHTML = `
        <div class="verdict-row">
          <div class="verdict verdict-bad">
            <h4>Legacy schemes</h4>
            <p>${name === "tampering" ? "Exposure confirmed" : legacyBroken ? "Compromised" : "See report"}</p>
            <small>${name === "tampering" ? "Unprotected storage offers no detection of modification." : "The simulated adversary succeeded against the prior-generation design."}</small>
          </div>
          <div class="verdict verdict-good">
            <h4>Attestra protocol</h4>
            <p>${oursResisted ? "Protected" : "See report"}</p>
            <small>${plan.verdictNote}</small>
          </div>
        </div>`;
      toast(`${plan.title} validation completed.`);
    } catch (err) {
      document.getElementById("vr-status").innerHTML = `<span class="st st-err">Failed</span>`;
      toast(err.message, "error");
    } finally {
      document.querySelectorAll("[data-validation]").forEach((b) => (b.disabled = false));
    }
  },
};

/* --------------------------- monitor page ----------------------------- */

const MonitorPage = {
  POLL_MS: 5000,

  async init() {
    this.lastAt = null;
    this.seen = 0;

    // Benchmark series come from a completed measurement run, so they are
    // fetched once. Operations come from this deployment's own audit history
    // and are polled, because new verifications land while the page is open.
    try {
      const bench = await api("/api/analytics/benchmarks");
      this.renderBenchmarks(bench.data);
    } catch (err) {
      const note = document.getElementById("bench-note");
      if (note) note.textContent = "Measurement data is unavailable on this deployment.";
    }

    await this.tick(true);
    this.timer = setInterval(() => this.tick(), this.POLL_MS);
    this.ago = setInterval(() => this.stampAge(), 1000);

    // Don't poll a tab nobody is looking at; catch up on return.
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        clearInterval(this.timer);
        this.timer = null;
        this.setLive(false);
      } else if (!this.timer) {
        this.tick();
        this.timer = setInterval(() => this.tick(), this.POLL_MS);
      }
    });

    const btn = document.getElementById("mon-refresh");
    if (btn) btn.addEventListener("click", () => this.tick(true));
  },

  setLive(on, failed) {
    const pill = document.getElementById("live-pill");
    const text = document.getElementById("live-text");
    if (!pill) return;
    pill.classList.toggle("is-off", !on);
    pill.classList.toggle("is-err", !!failed);
    if (text) text.textContent = failed ? "Reconnecting" : (on ? "Live" : "Paused");
  },

  stampAge() {
    const el = document.getElementById("ops-updated");
    if (!el || !this.lastAt) return;
    const s = Math.round((Date.now() - this.lastAt) / 1000);
    el.textContent = s < 2 ? "updated just now" : `updated ${s}s ago`;
  },

  async tick(force) {
    try {
      const { data } = await api("/api/analytics/operations");
      this.lastAt = Date.now();
      this.setLive(true);
      // Redraw only when something actually changed, so the chart does not
      // flicker every five seconds for no reason.
      if (force || data.length !== this.seen) {
        this.renderOps(data);
        if (!force && data.length > this.seen && this.seen > 0) {
          const n = data.length - this.seen;
          toast(`${n} new verification${n > 1 ? "s" : ""} recorded.`);
        }
        this.seen = data.length;
      }
      this.stampAge();
    } catch (err) {
      this.setLive(false, true);
    }
  },

  renderOps(ops) {
    const $ = (id) => document.getElementById(id);
    if (!ops.length) {
      $("ch-ops").innerHTML = '<div class="chart-empty">No verifications recorded yet.</div>';
      $("ch-passrate").innerHTML = '<div class="chart-empty">No verifications recorded yet.</div>';
      return;
    }
    AttestraCharts.lineChart($("ch-ops"), {
      series: [{
        name: "Verification latency",
        data: ops.map((o, i) => ({ x: i + 1, y: o.duration_ms })),
        color: "#1a7f4e",
      }],
      xLabel: "Verification # (this deployment)",
      yLabel: "Latency (ms)", yUnit: "ms",
    });
    const passed = ops.filter((o) => o.passed).length;
    AttestraCharts.donut($("ch-passrate"),
      [
        { label: "Passed", value: passed, color: "#1a7f4e" },
        { label: "Failed", value: ops.length - passed, color: "#c23934" },
      ],
      `${Math.round((passed / ops.length) * 100)}%`, "verifications passed");
    $("ops-count").textContent = ops.length;
    const avg = ops.reduce((a, o) => a + o.duration_ms, 0) / ops.length;
    $("ops-avg").textContent = `${Math.round(avg)} ms`;
  },

  renderBenchmarks(b) {
    const $ = (id) => document.getElementById(id);

    AttestraCharts.lineChart($("ch-keygen"), {
      series: [
        { name: "Attestra", data: b.keygen.proposed },
        { name: "Previous-generation design", data: b.keygen.baseline, dashed: true },
      ],
      xLabel: "Organizations in the ownership group",
      yLabel: "Setup time (ms)", yUnit: "ms",
    });

    AttestraCharts.lineChart($("ch-audit"), {
      series: [
        { name: "Proof generation", data: b.audit.proofgen },
        { name: "Verification", data: b.audit.verify },
        ...(b.audit.verify_s10.length ? [{ name: "Verification (10 orgs)", data: b.audit.verify_s10, dashed: true }] : []),
      ],
      xLabel: "Blocks challenged per verification",
      yLabel: "Time (ms)", yUnit: "ms",
    });

    AttestraCharts.lineChart($("ch-modify"), {
      series: [
        { name: "Add members — Attestra", data: b.owner_modify.add_proposed },
        { name: "Add members — previous gen.", data: b.owner_modify.add_baseline, dashed: true },
        { name: "Remove members — Attestra", data: b.owner_modify.revoke_proposed },
        { name: "Remove members — previous gen.", data: b.owner_modify.revoke_baseline, dashed: true },
      ],
      xLabel: "Organizations joining or leaving",
      yLabel: "Time (ms, log scale)", yUnit: "ms", logY: true,
    });

    AttestraCharts.lineChart($("ch-transfer"), {
      series: [{ name: "Ownership transfer", data: b.transfer }],
      xLabel: "Organizations per group",
      yLabel: "Time (ms)", yUnit: "ms",
    });

    const note = document.getElementById("bench-note");
    if (note) {
      note.textContent = `Recorded on this deployment's hardware (${b.meta.machine}, `
        + `median of ${b.meta.repeats} runs per point, ${b.meta.mode} sweep). These are `
        + `results of a completed benchmark run, so they change only when the suite is `
        + `run again — not second to second.`;
    }
  },
};
const AnalyticsPage = MonitorPage; // legacy alias

/* --------------------------- access keys ------------------------------ */

const KeysPage = {
  init() {
    const createBtn = document.getElementById("ak-create");
    if (createBtn) createBtn.addEventListener("click", async () => {
      const desc = prompt("Short description for this key (optional):", "") || "";
      busy(createBtn, true, "Creating…");
      try {
        const r = await api("/api/keys", {
          method: "POST", body: JSON.stringify({ description: desc }),
        });
        sessionStorage.setItem("attestra.newkey",
          JSON.stringify({ key_id: r.key_id, secret: r.secret }));
        location.reload();
      } catch (err) { toast(err.message, "error"); busy(createBtn, false); }
    });

    // show a freshly created secret exactly once
    const raw = sessionStorage.getItem("attestra.newkey");
    if (raw) {
      sessionStorage.removeItem("attestra.newkey");
      const { key_id, secret } = JSON.parse(raw);
      const holder = document.getElementById("ak-reveal");
      if (holder) {
        holder.hidden = false;
        holder.innerHTML = `
          <strong>Access key created.</strong> This is the only time the secret is shown —
          store it now. Attestra keeps only a hash.
          <div class="pair">
            <code>AccessKey ID: &nbsp;${key_id}</code>
            <code>Secret: &nbsp;${secret}</code>
          </div>`;
      }
    }

    document.querySelectorAll("[data-key-toggle]").forEach((b) =>
      b.addEventListener("click", async () => {
        try {
          await api(`/api/keys/${b.dataset.keyToggle}/toggle`, { method: "POST" });
          location.reload();
        } catch (err) { toast(err.message, "error"); }
      }));
    document.querySelectorAll("[data-key-delete]").forEach((b) =>
      b.addEventListener("click", async () => {
        if (!confirm("Delete this access key? Applications using it will stop authenticating.")) return;
        try {
          await api(`/api/keys/${b.dataset.keyDelete}`, { method: "DELETE" });
          location.reload();
        } catch (err) { toast(err.message, "error"); }
      }));
  },
};

/* ----------------------------- support -------------------------------- */

const SupportPage = {
  init() {
    const form = document.getElementById("ticket-form");
    if (form) form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = form.querySelector("button[type=submit]");
      busy(btn, true, "Submitting…");
      try {
        const r = await api("/api/tickets", {
          method: "POST",
          body: JSON.stringify({
            subject: form.querySelector("[name=subject]").value,
            category: form.querySelector("[name=category]").value,
            severity: form.querySelector("[name=severity]").value,
            body: form.querySelector("[name=body]").value,
          }),
        });
        toast(`Ticket ${r.ticket_no} submitted. Our engineers will respond shortly.`);
        setTimeout(() => location.reload(), 900);
      } catch (err) { toast(err.message, "error"); busy(btn, false); }
    });

    document.querySelectorAll("[data-ticket-resolve]").forEach((b) =>
      b.addEventListener("click", async () => {
        try {
          await api(`/api/tickets/${b.dataset.ticketResolve}/resolve`, { method: "POST" });
          location.reload();
        } catch (err) { toast(err.message, "error"); }
      }));
  },
};

/* ---------------------------- settings page ---------------------------- */

const SettingsPage = {
  init() {
    const pf = document.getElementById("profile-form");
    if (pf) pf.addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = pf.querySelector("button");
      busy(btn, true, "Saving…");
      try {
        await api("/api/settings/profile", {
          method: "POST",
          body: JSON.stringify({
            name: pf.querySelector("[name=name]").value,
            organization: pf.querySelector("[name=organization]").value,
          }),
        });
        toast("Profile updated.");
        setTimeout(() => location.reload(), 800);
      } catch (err) { toast(err.message, "error"); }
      finally { busy(btn, false); }
    });

    const pw = document.getElementById("password-form");
    if (pw) pw.addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = pw.querySelector("button");
      busy(btn, true, "Updating…");
      try {
        await api("/api/settings/password", {
          method: "POST",
          body: JSON.stringify({
            current: pw.querySelector("[name=current]").value,
            new: pw.querySelector("[name=new]").value,
          }),
        });
        toast("Password updated.");
        pw.reset();
      } catch (err) { toast(err.message, "error"); }
      finally { busy(btn, false); }
    });
  },
};

/* =====================================================================
   Ownership registry: parties, agreements, certificates, listings
   ===================================================================== */

const PartiesPage = {
  init(types, membershipTypes) {
    this.types = types;
    this.membershipTypes = membershipTypes || [];
    const drawer = makeDrawer("Register a party");
    this.drawer = drawer;

    const btn = document.getElementById("add-party");
    if (btn) btn.addEventListener("click", () => {
      drawer.setTitle("Register a party");
      drawer.body.innerHTML = `
        <form class="form-stack" id="party-form">
          <label>Party type
            <select name="party_type" id="pt-type">
              ${this.types.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}
            </select>
            <span class="field-note" id="pt-typenote"></span>
          </label>
          <label>Display name
            <input type="text" name="display_name" id="pt-name" placeholder="Apollo Hospital" required>
          </label>
          <label>Registered legal name
            <input type="text" name="legal_name" placeholder="Apollo Hospitals Enterprise Limited">
            <span class="field-note">Left blank, the display name is used on certificates.</span>
          </label>
          <div class="form-row">
            <label>Registration number
              <input type="text" name="registration_id" placeholder="CIN / GSTIN / Reg. no.">
            </label>
            <label>Jurisdiction
              <input type="text" name="jurisdiction" placeholder="Karnataka, India">
            </label>
          </div>
          <label id="pt-emailwrap">Contact email
            <input type="email" name="contact_email" id="pt-email" placeholder="records@example.org">
            <span class="field-note" id="pt-emailnote">Optional for an organization.</span>
          </label>
          <label id="pt-ownername" hidden>Owner's name
            <input type="text" name="owner_name" placeholder="Adithyan Murthy">
          </label>
          <div id="pt-members" hidden>
            <label>Collaborators <span class="hint">(added by email)</span></label>
            <div class="member-rows" id="pt-memberrows"></div>
            <div style="margin-top:10px">
              <button type="button" class="btn btn-ghost btn-sm" id="pt-addrow">Add another person</button>
            </div>
            <p class="field-note">Everyone listed here becomes a co-owner of anything the
              group holds, with their own key. You can add or remove people later.</p>
          </div>
          <span class="field-note">A signing key pair is generated for this party and held in
            custody by the registry. It is used to sign transfer agreements.</span>
          <button class="btn btn-primary" type="submit">Add to register</button>
        </form>`;
      drawer.open();

      const typeSel = drawer.body.querySelector("#pt-type");
      const emailInput = drawer.body.querySelector("#pt-email");
      const emailNote = drawer.body.querySelector("#pt-emailnote");
      const members = drawer.body.querySelector("#pt-members");
      const ownerName = drawer.body.querySelector("#pt-ownername");
      const rows = drawer.body.querySelector("#pt-memberrows");

      const addRow = () => {
        const row = document.createElement("div");
        row.className = "form-row member-row";
        row.innerHTML =
          `<label>Email<input type="email" class="m-email" placeholder="person@example.org"></label>
           <label>Name <span class="hint">(optional)</span><input type="text" class="m-name"></label>
           <button type="button" class="icon-btn m-remove" title="Remove">&times;</button>`;
        row.querySelector(".m-remove").addEventListener("click", () => row.remove());
        rows.appendChild(row);
      };
      drawer.body.querySelector("#pt-addrow").addEventListener("click", addRow);

      const applyType = () => {
        const t = typeSel.value;
        const isGroup = this.membershipTypes.includes(t);
        const needsEmail = t === "individual" || isGroup;
        members.hidden = !isGroup;
        ownerName.hidden = !isGroup;
        emailInput.required = needsEmail;
        emailNote.textContent = isGroup
          ? "The address of whoever owns this group. They are recorded as its owner."
          : (t === "individual"
            ? "Required — this is how the person is identified on the register."
            : "Optional for an organization; its registration number identifies it.");
        drawer.body.querySelector("#pt-typenote").textContent = isGroup
          ? "Several people holding data together, each with their own key — the way a shared repository works."
          : (t === "individual" ? "One named person."
            : "One legal entity that signs as one.");
        const nameField = drawer.body.querySelector("#pt-name");
        nameField.placeholder = isGroup ? "Bengaluru Imaging Collective"
          : (t === "individual" ? "Dr. Ananya Rao" : "Apollo Hospital");
        drawer.body.querySelector("[name=legal_name]").placeholder = isGroup
          ? "Bengaluru Imaging Collective (unincorporated association)"
          : (t === "individual" ? "Ananya Rao" : "Apollo Hospitals Enterprise Limited");
        if (isGroup && !rows.children.length) addRow();
      };
      typeSel.addEventListener("change", applyType);
      applyType();

      drawer.body.querySelector("#party-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const f = e.target;
        const b = f.querySelector("button[type=submit]");
        busy(b, true, "Registering…");
        try {
          const body = {};
          new FormData(f).forEach((v, k) => { body[k] = v; });
          body.members = [...rows.querySelectorAll(".member-row")].map((row) => ({
            email: row.querySelector(".m-email").value.trim(),
            name: row.querySelector(".m-name").value.trim(),
          })).filter((m) => m.email);
          const r = await api("/api/parties", { method: "POST", body: JSON.stringify(body) });
          toast(`Registered as ${r.ref}.`);
          setTimeout(() => location.reload(), 700);
        } catch (err) { toast(err.message, "error"); busy(b, false); }
      });
    });

    /* A party that cannot sign in cannot sign anything, and the registry must
       not sign in its place — so it issues the party its own credentials. */
    document.querySelectorAll("[data-issue-login]").forEach((a) => {
      a.addEventListener("click", (e) => {
        e.preventDefault();
        const id = a.dataset.issueLogin;
        drawer.setTitle(`Issue a login to ${a.dataset.name}`);
        drawer.body.innerHTML = `
          <p class="muted" style="margin-bottom:14px">${a.dataset.name} will sign in with
          these credentials and sign its own agreements. You will not be able to sign on its
          behalf — as the registry you approve transfers in your own name instead, and the
          deed records which of the two happened.</p>
          <form class="form-stack" id="login-form">
            <label>Contact name
              <input type="text" name="name" value="${a.dataset.name}" required>
            </label>
            <label>Email
              <input type="email" name="email" value="${a.dataset.email || ""}" required>
            </label>
            <label>Temporary password <span class="hint">(at least 8 characters)</span>
              <input type="text" name="password" required minlength="8"
                     value="attestra2026">
              <span class="field-note">Share it with the party; they can change it under
                Account settings once they sign in.</span>
            </label>
            <button class="btn btn-primary" type="submit">Issue credentials</button>
          </form>`;
        drawer.open();
        drawer.body.querySelector("#login-form").addEventListener("submit", async (ev) => {
          ev.preventDefault();
          const f = ev.target;
          const b = f.querySelector("button");
          busy(b, true, "Issuing…");
          try {
            const body = {};
            new FormData(f).forEach((v, k) => { body[k] = v; });
            const r = await api(`/api/parties/${id}/account`, {
              method: "POST", body: JSON.stringify(body),
            });
            toast(`${a.dataset.name} can now sign in as ${r.email}.`);
            setTimeout(() => location.reload(), 900);
          } catch (err) { toast(err.message, "error"); busy(b, false); }
        });
      });
    });

    const fields = (v) => `
      <label>Display name
        <input type="text" name="display_name" value="${v.display_name || ""}" required>
      </label>
      <label>Registered legal name
        <input type="text" name="legal_name" value="${v.legal_name || ""}">
      </label>
      <div class="form-row">
        <label>Party type
          <select name="party_type">
            ${this.types.map(([tv, tl]) =>
              `<option value="${tv}"${tv === v.party_type ? " selected" : ""}>${tl}</option>`).join("")}
          </select>
        </label>
        <label>Registration number
          <input type="text" name="registration_id" value="${v.registration_id || ""}">
        </label>
      </div>
      <div class="form-row">
        <label>Jurisdiction
          <input type="text" name="jurisdiction" value="${v.jurisdiction || ""}">
        </label>
        <label>Contact email
          <input type="email" name="contact_email" value="${v.contact_email || ""}">
        </label>
      </div>`;

    document.querySelectorAll("[data-edit-party]").forEach((a) => {
      a.addEventListener("click", (e) => {
        e.preventDefault();
        const id = a.dataset.editParty;
        drawer.setTitle("Amend register entry");
        drawer.body.innerHTML = `<form class="form-stack" id="party-edit">
          ${fields(JSON.parse(a.dataset.party))}
          <span class="field-note">The party's protocol identifier is not changed —
            datasets and certificates already name it.</span>
          <button class="btn btn-primary" type="submit">Save changes</button>
        </form>`;
        drawer.open();
        drawer.body.querySelector("#party-edit").addEventListener("submit", async (ev) => {
          ev.preventDefault();
          const f = ev.target;
          const b = f.querySelector("button");
          busy(b, true, "Saving…");
          try {
            const body = {};
            new FormData(f).forEach((v, k) => { body[k] = v; });
            await api(`/api/parties/${id}`, { method: "POST", body: JSON.stringify(body) });
            toast("Register entry updated.");
            setTimeout(() => location.reload(), 600);
          } catch (err) { toast(err.message, "error"); busy(b, false); }
        });
      });
    });

    document.querySelectorAll("[data-verify-party]").forEach((a) => {
      a.addEventListener("click", async (e) => {
        e.preventDefault();
        const method = prompt(
          "Record how this party's identity was checked:",
          "Registration document review");
        if (!method) return;
        try {
          await api(`/api/parties/${a.dataset.verifyParty}/verify`, {
            method: "POST", body: JSON.stringify({ method }),
          });
          toast("Identity recorded as verified.");
          setTimeout(() => location.reload(), 600);
        } catch (err) { toast(err.message, "error"); }
      });
    });

    document.querySelectorAll("[data-delete-party]").forEach((a) => {
      a.addEventListener("click", async (e) => {
        e.preventDefault();
        if (!confirm("Remove this party from the register?")) return;
        try {
          await api(`/api/parties/${a.dataset.deleteParty}`, { method: "DELETE" });
          toast("Removed from the register.");
          setTimeout(() => location.reload(), 600);
        } catch (err) { toast(err.message, "error"); }
      });
    });
  },
};

/* ------------------- a deed being issued, watched ------------------- */
/* The Certificates page shows the transfer that is producing the next deed:
   the same stages the agreement page runs, with the BLS12-381 signing named
   as it happens, and the certificate number the moment it exists. */

const CertificatesPage = {
  init() {
    this.panel = document.getElementById("issuing");
    this.body = document.getElementById("iss-body");
    if (!this.panel) return;
    this.pill = document.getElementById("iss-pill");
    this.pillText = document.getElementById("iss-pill-text");
    this.heading = document.getElementById("iss-heading");
    this.announced = new Set();
    this.firstTick = true;      // history is not news
    this.tick();
    setInterval(() => this.tick(), 1200);
  },

  async tick() {
    if (document.hidden) return;
    let data;
    try {
      data = await api("/api/processes");
    } catch (err) { return; }
    const transfers = (data.processes || [])
      .filter((p) => p.kind === "transfer")
      .slice(0, 3);
    if (this.firstTick) {
      // Deeds issued before this page was opened belong in the table below,
      // not in a "just issued" banner with a toast.
      transfers.filter((p) => p.status !== "running")
        .forEach((p) => this.announced.add(p.id));
      this.firstTick = false;
    }
    const live = transfers.filter((p) => p.status === "running");
    // Once nothing is running, keep the last one on screen only until its
    // certificate has been announced; after that the table below is the truth.
    const show = live.length ? live
      : transfers.filter((p) => p.status !== "running"
                          && p.result && p.result.certificate_id
                          && !this.announced.has(p.id));
    if (!show.length) {
      this.panel.hidden = true;
      return;
    }
    this.panel.hidden = false;
    const running = live.length > 0;
    this.pill.className = "live-pill" + (running ? "" : " is-off");
    this.pillText.textContent = running ? "In process" : "Issued";
    this.heading.textContent = running ? "A deed is being issued" : "Deed issued";
    this.body.innerHTML = show.map((p) => this.card(p)).join("");

    show.filter((p) => p.status !== "running").forEach((p) => {
      if (this.announced.has(p.id)) return;
      this.announced.add(p.id);
      toast(`Deed ${p.result.certificate_id} issued.`);
      // The table below does not have it yet.
      setTimeout(() => location.reload(), 2500);
    });
  },

  card(p) {
    const stages = p.stages || [];
    const done = stages.filter((st) => st.state === "done").length;
    const pct = p.status === "running"
      ? Math.max(6, Math.round((done / (stages.length || 5)) * 100)) : 100;
    const current = stages.find((st) => st.state === "running");
    const bls = stages.filter((st) => st.bls && st.bls.group_public_key).pop();
    const cert = p.result && p.result.certificate_id;

    return `
      <p class="iss-title">${WorkbenchPage.escape(p.title)}</p>
      <p class="progress-note">${WorkbenchPage.escape(p.subject)}</p>
      <div class="progress"><div class="progress-bar" style="width:${pct}%"></div></div>
      <p class="progress-note">${p.status === "running"
        ? `Step ${Math.min(done + 1, stages.length || 5)} of ${stages.length || 5} — `
          + WorkbenchPage.escape(current ? current.label : (p.message || "working"))
        : `Completed in ${p.elapsed_s}s.`}</p>
      <ul class="stepper pf-steps">
        ${stages.map((st, i) => `
          <li class="${st.state === "done" ? "done" : (st.state === "running" ? "running"
              : (st.state === "failed" ? "failed" : ""))}">
            <span class="step-dot">${i + 1}</span>
            <div class="step-body"><h4>${WorkbenchPage.escape(st.label)}</h4>
              <p>${st.detail ? WorkbenchPage.escape(st.detail) + (st.ms ? ` — ${st.ms} ms` : "")
                : (st.state === "running" ? "running now…" : "&nbsp;")}</p></div>
          </li>`).join("")}
      </ul>
      ${bls ? `<div class="bls-block">
        <h4>BLS12-381 signature being applied</h4>
        <div class="bls-row"><span class="bls-label">Group public key in use</span>
          <code class="bls-value">${bls.bls.group_public_key.slice(0, 96)}…</code></div>
        ${bls.bls.proof_tp ? `<div class="bls-row">
          <span class="bls-label">Aggregated tag proving possession</span>
          <code class="bls-value">${bls.bls.proof_tp.slice(0, 96)}…</code></div>` : ""}
        <p class="field-note">The full values are printed on the deed once it is issued.</p>
      </div>` : ""}
      ${cert ? `<p class="iss-done">Deed <a class="link mono"
        href="/certificate/${cert}" target="_blank">${cert}</a> issued and signed with the
        registry's Ed25519 key.</p>` : ""}`;
  },
};

/* ---------------------- a party, and its people ---------------------- */

const PartyDetailPage = {
  init(partyId) {
    this.id = partyId;
    const drawer = makeDrawer("Add a collaborator");

    const add = document.getElementById("add-member");
    if (add) add.addEventListener("click", () => {
      drawer.body.innerHTML = `
        <p class="muted" style="margin-bottom:14px">A collaborator is admitted by email
        address and given a protocol identifier of their own. Adding somebody here does not
        put their key on any dataset — do that from the table below, one dataset at a
        time.</p>
        <form class="form-stack" id="member-form">
          <div class="form-row">
            <label>Email
              <input type="email" id="mf-email" placeholder="person@example.org" required>
            </label>
            <label>Name <span class="hint">(optional)</span>
              <input type="text" id="mf-name">
            </label>
          </div>
          <button class="btn btn-primary" type="submit">Add collaborator</button>
        </form>`;
      drawer.open();
      drawer.body.querySelector("#member-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const b = e.target.querySelector("button");
        busy(b, true, "Adding…");
        try {
          const r = await api(`/api/parties/${partyId}/members`, {
            method: "POST",
            body: JSON.stringify({
              email: drawer.body.querySelector("#mf-email").value.trim(),
              name: drawer.body.querySelector("#mf-name").value.trim(),
            }),
          });
          toast(`${r.added} added — protocol id ${r.slug}.`);
          setTimeout(() => location.reload(), 800);
        } catch (err) { toast(err.message, "error"); busy(b, false); }
      });
    });

    document.querySelectorAll("[data-remove-member]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const holds = parseInt(btn.dataset.holds, 10) || 0;
        const warning = holds
          ? `\n\n${btn.dataset.name} still holds keys on ${holds} dataset(s). Revoke those `
            + "first — the register will refuse the removal until you do."
          : "";
        if (!confirm(`Remove ${btn.dataset.name} from this group?${warning}`)) return;
        try {
          await api(`/api/parties/${btn.dataset.party}/members/${btn.dataset.removeMember}`,
                    { method: "DELETE" });
          toast("Removed from the group.");
          setTimeout(() => location.reload(), 700);
        } catch (err) { toast(err.message, "error"); }
      });
    });

    /* Admitting or revoking one member's key on one dataset. Both are real
       protocol operations, so both take a moment and both are confirmed. */
    document.querySelectorAll("[data-add-owner]").forEach((btn) => {
      btn.addEventListener("click", () => this.owners(btn, "add",
        `Admit ${btn.dataset.name} to the ownership group of ${btn.dataset.dsName}?\n\n`
        + "Their key joins the group's aggregate. Nobody else is re-keyed.",
        btn.dataset.addOwner));
    });
    document.querySelectorAll("[data-revoke-owner]").forEach((btn) => {
      btn.addEventListener("click", () => this.owners(btn, "revoke",
        `Revoke ${btn.dataset.name}'s key on ${btn.dataset.dsName}?\n\n`
        + "After this they can no longer prove possession of it. This cannot be undone "
        + "without admitting them again.",
        btn.dataset.revokeOwner));
    });
  },

  async owners(btn, action, question, slug) {
    if (!confirm(question)) return;
    busy(btn, true, action === "add" ? "Admitting…" : "Revoking…");
    try {
      const { result } = await api(
        `/api/datasets/${btn.dataset.dataset}/owners/${action}`,
        { method: "POST", body: JSON.stringify({ owners: [slug] }) });
      toast(action === "add"
        ? `${btn.dataset.name} admitted — ${result.owners.length} key holder(s), `
          + `${Math.round(result.duration_ms)} ms, no re-key of existing members.`
        : `${btn.dataset.name}'s key revoked — ${result.owners.length} key holder(s) remain.`);
      setTimeout(() => location.reload(), 1100);
    } catch (err) {
      toast(err.message, "error");
      busy(btn, false);
    }
  },
};

/* ------------------- competing offers on one asset ------------------- */
/* A holder decides between offers here rather than opening each one. Both
   actions are the same endpoints the agreement page uses. */

const OffersPanel = {
  init() {
    document.querySelectorAll("[data-accept-offer]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm(`Accept ${btn.dataset.name}'s offer of ${btn.dataset.amount} `
                   + `for ${btn.dataset.ds}?\n\nOwnership transfers immediately, a deed is `
                   + "issued, and every other offer on this asset is closed.")) return;
        busy(btn, true, "Transferring…");
        try {
          const started = await api(`/api/agreements/${btn.dataset.acceptOffer}/accept`,
                                    { method: "POST" });
          if (started.job_id) {
            const job = await pollJob(started.job_id, null, 800);
            if (job.status === "error") throw new Error(job.error || "the transfer failed");
            toast(`Transfer complete. Deed ${job.result.certificate_id} issued.`);
          } else {
            toast("Signed.");
          }
          setTimeout(() => location.reload(), 1400);
        } catch (err) { toast(err.message, "error"); busy(btn, false); }
      });
    });

    document.querySelectorAll("[data-decline-offer]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const reason = prompt(`Why are you declining ${btn.dataset.name}'s offer?`,
                              "Offer below the asking price");
        if (reason === null) return;
        try {
          await api(`/api/agreements/${btn.dataset.declineOffer}/decline`, {
            method: "POST", body: JSON.stringify({ reason }),
          });
          toast("Offer declined. The dataset is unchanged and still yours.");
          setTimeout(() => location.reload(), 700);
        } catch (err) { toast(err.message, "error"); }
      });
    });
  },
};

const AgreementsPage = {
  init(datasets, parties, myPartyId) {
    const drawer = makeDrawer("Raise a transfer agreement");
    const btn = document.getElementById("new-agreement");
    if (!btn) return;

    const partyOptions = (selected) => parties
      .map((p) => `<option value="${p.id}"${p.id === selected ? " selected" : ""}>` +
                  `${p.name} — ${p.type}</option>`).join("");
    // You can only offer what you hold, so the transferor side is your own
    // entry unless you are the registry, which may raise one for any party.
    const sellerField = myPartyId
      ? `<input type="hidden" name="seller_party_id" value="${myPartyId}">
         <label>Transferor (you)
           <select disabled>${partyOptions(myPartyId)}</select>
         </label>`
      : `<label>Transferor (current holder)
           <select name="seller_party_id">${partyOptions(null)}</select>
         </label>`;

    btn.addEventListener("click", () => {
      if (!datasets.length) {
        toast("Protect a dataset before transferring it.", "error");
        return;
      }
      if (parties.length < 2) {
        toast("Register at least two parties first.", "error");
        return;
      }
      drawer.body.innerHTML = `
        <form class="form-stack" id="agreement-form">
          <label>Dataset
            <select name="dataset_id">
              ${datasets.map((d) => `<option value="${d.id}">${d.name}</option>`).join("")}
            </select>
          </label>
          <div class="form-row">
            ${sellerField}
            <label>Transferee (acquiring party)
              <select name="buyer_party_id">${partyOptions(null)}</select>
            </label>
          </div>
          <div class="form-row">
            <label>Consideration
              <input type="number" name="consideration" min="0" step="1000" value="0" required>
            </label>
            <label>Currency
              <select name="currency"><option>INR</option><option>USD</option><option>EUR</option></select>
            </label>
          </div>
          <label>Payment reference
            <input type="text" name="payment_reference" placeholder="Bank reference, invoice number or escrow id">
            <span class="field-note">Recorded on the certificate. The registry records the
              reference; it does not settle funds.</span>
          </label>
          <label>Conditions
            <textarea name="terms" rows="4" placeholder="Licence scope, permitted use, retention obligations…"></textarea>
          </label>
          <span class="field-note">Nothing moves when this is raised. The transferor signs
            first, and ownership transfers only when the transferee countersigns.</span>
          <button class="btn btn-primary" type="submit">Raise agreement</button>
        </form>`;
      drawer.open();
      drawer.body.querySelector("#agreement-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const f = e.target;
        const b = f.querySelector("button");
        busy(b, true, "Raising…");
        try {
          const body = {};
          new FormData(f).forEach((v, k) => { body[k] = v; });
          const r = await api("/api/agreements", { method: "POST", body: JSON.stringify(body) });
          toast(`${r.ref} raised.`);
          setTimeout(() => { location.href = `/console/agreements/${r.agreement_id}`; }, 600);
        } catch (err) { toast(err.message, "error"); busy(b, false); }
      });
    });
  },
};

const AgreementDetailPage = {
  init(id, datasetId) {
    this.id = id;
    this.datasetId = datasetId;

    const seller = document.getElementById("sign-seller");
    if (seller) seller.addEventListener("click", () => {
      const isBid = seller.textContent.indexOf("Accept") === 0;
      const question = isBid
        ? "Accept this offer?\n\nOwnership of the dataset transfers to the buyer "
          + "immediately and a deed is issued. This cannot be undone."
        : "Sign this agreement as the transferor?\n\nOwnership does not move yet — "
          + "the transferee must countersign.";
      if (!confirm(question)) return;
      this.run(seller, `/api/agreements/${id}/accept`, isBid ? "Transferring…" : "Signing…");
    });

    const buyer = document.getElementById("sign-buyer");
    if (buyer) buyer.addEventListener("click", () => {
      if (!confirm("Countersign and complete this transfer?\n\nThis moves ownership of the "
                 + "dataset and issues a certificate. It cannot be undone.")) return;
      this.run(buyer, `/api/agreements/${id}/sign-buyer`, "Transferring…");
    });

    const approve = document.getElementById("approve");
    if (approve) approve.addEventListener("click", () => {
      const note = prompt(
        "Approve this transfer as the registry?\n\nThe deed will record that the registry "
        + "effected it on the applicant's request, naming you — not that the other party "
        + "signed. Note for the record:",
        "Approved on the transferee's application");
      if (note === null) return;
      this.run(approve, `/api/agreements/${id}/approve`, "Approving…", { note });
    });

    const revise = document.getElementById("revise");
    if (revise) revise.addEventListener("click", async () => {
      const amount = prompt("Revise your offer to (amount, same currency):", "");
      if (amount === null || !amount.trim()) return;
      try {
        await api("/api/offers", {
          method: "POST",
          body: JSON.stringify({ dataset_id: this.datasetId,
                                 amount: parseFloat(amount) }),
        });
        toast("Offer revised and signed again.");
        setTimeout(() => location.reload(), 700);
      } catch (err) { toast(err.message, "error"); }
    });

    const withdraw = document.getElementById("withdraw");
    if (withdraw) withdraw.addEventListener("click", async () => {
      if (!confirm("Withdraw this offer?\n\nThe holder will no longer see it. "
                 + "You can make another at any time while they still hold the asset.")) return;
      try {
        await api(`/api/agreements/${id}/cancel`, { method: "POST" });
        toast("Withdrawn.");
        setTimeout(() => location.reload(), 700);
      } catch (err) { toast(err.message, "error"); }
    });

    const decline = document.getElementById("decline");
    if (decline) decline.addEventListener("click", async () => {
      const reason = prompt("Why are you declining?", "Terms not agreed");
      if (reason === null) return;
      try {
        await api(`/api/agreements/${id}/decline`, {
          method: "POST", body: JSON.stringify({ reason }),
        });
        toast("Declined. The dataset is unchanged.");
        setTimeout(() => location.reload(), 700);
      } catch (err) { toast(err.message, "error"); }
    });
  },

  /* Every path that can complete a transfer runs through here, so the panel
     shows the same five steps whoever set them off. */
  async run(button, url, label, body) {
    const panel = document.getElementById("transfer-live");
    const steps = [...document.querySelectorAll("#tl-steps li")];
    const bar = document.getElementById("tl-bar");
    const statusEl = document.getElementById("tl-status");
    const pill = document.getElementById("tl-pill");
    const pillText = document.getElementById("tl-pill-text");
    const logEl = document.getElementById("tl-log");
    const blsEl = document.getElementById("tl-bls");
    busy(button, true, label);
    if (panel) {
      panel.hidden = false;
      panel.scrollIntoView({ behavior: "smooth", block: "start" });
      steps.forEach((li) => { li.className = ""; });
      bar.style.width = "0%";
      logEl.textContent = "";
      blsEl.hidden = true;
      blsEl.innerHTML = "";
      pill.className = "live-pill";
      pillText.textContent = "Running";
      document.getElementById("tl-heading").textContent = "Executing the transfer";
    }
    try {
      const started = await api(url, {
        method: "POST", body: JSON.stringify(body || {}),
      });
      if (!started.job_id) {                    // signed, but not completed yet
        toast(started.status_label
          ? `Signed. ${started.status_label}.`
          : "Signed.");
        setTimeout(() => location.reload(), 800);
        return;
      }
      const job = await pollJob(started.job_id, (j) => {
        if (!panel) return;
        statusEl.textContent = j.message || "Working…";
        logEl.textContent = (j.log || []).join("\n");
        logEl.scrollTop = logEl.scrollHeight;
        const stages = (j.result && j.result.stages) || [];
        stages.forEach((st, i) => {
          if (!steps[i]) return;
          steps[i].className = st.state === "done" ? "done"
            : (st.state === "failed" ? "failed"
              : (st.state === "running" ? "running" : ""));
          if (st.detail) {
            const note = steps[i].querySelector("p");
            if (!note.dataset.original) note.dataset.original = note.textContent;
            note.textContent = st.ms ? `${st.detail} — ${st.ms} ms` : st.detail;
          }
        });
        const done = stages.filter((st) => st.state === "done").length;
        bar.style.width = Math.max(6, (done / 5) * 100) + "%";
        this.renderBls(blsEl, stages);
      }, 400);
      if (job.status === "error") throw new Error(job.error || "the transfer failed");
      if (panel) {
        bar.style.width = "100%";
        pillText.textContent = "Completed";
        document.getElementById("tl-heading").textContent = "Transfer complete";
        statusEl.textContent = `Deed ${job.result.certificate_id} issued.`;
      }
      toast(`Transfer complete. Certificate ${job.result.certificate_id} issued.`);
      setTimeout(() => location.reload(), 2200);
    } catch (err) {
      if (panel) {
        pill.className = "live-pill is-err";
        pillText.textContent = "Failed";
        document.getElementById("tl-heading").textContent = "Transfer did not complete";
        statusEl.textContent = err.message;
        steps.forEach((li) => { if (li.className === "running") li.className = "failed"; });
      }
      toast(err.message, "error");
      busy(button, false);
    }
  },

  /* The BLS12-381 values the steps actually produced. These are the
     signatures the deed will carry, shown as they are made. */
  renderBls(box, stages) {
    if (!box) return;
    const rows = [];
    stages.forEach((st) => {
      if (!st.bls) return;
      if (st.key === "possession" && st.bls.group_public_key) {
        rows.push(["Group public key before the sale", st.bls.group_public_key]);
        if (st.bls.proof_tp) rows.push(["Aggregated tag proving possession", st.bls.proof_tp]);
      }
      if (st.key === "accept" && st.bls.group_public_key) {
        rows.push(["Group public key after the sale", st.bls.group_public_key]);
      }
      if (st.key === "revocation" && st.bls.proof_tp) {
        rows.push(["Aggregated tag under the transferor's retired key", st.bls.proof_tp]);
      }
    });
    if (!rows.length) return;
    box.hidden = false;
    box.innerHTML =
      `<h4>BLS12-381 signatures produced by this transfer</h4>` +
      rows.map(([label, value]) =>
        `<div class="bls-row"><span class="bls-label">${label}</span>` +
        `<code class="bls-value" title="${value}">${value.slice(0, 64)}…</code></div>`).join("") +
      `<p class="field-note">Uncompressed affine encoding, hex. The full values are printed
        on the deed, where anyone can check that the key controlling this dataset after the
        sale is not the key that controlled it before.</p>`;
  },
};

const ListingsPage = {
  init(datasets, parties) {
    const drawer = makeDrawer("List a dataset for sale");
    const btn = document.getElementById("new-listing");

    if (btn) btn.addEventListener("click", () => {
      if (!datasets.length) {
        toast("Protect a dataset before listing it.", "error");
        return;
      }
      if (!parties.length) {
        toast("Register the holding party first.", "error");
        return;
      }
      drawer.body.innerHTML = `
        <form class="form-stack" id="listing-form">
          <label>Dataset
            <select name="dataset_id">
              ${datasets.map((d) => `<option value="${d.id}">${d.name} (${d.region})</option>`).join("")}
            </select>
          </label>
          <label>Selling party
            <select name="seller_party_id">
              ${parties.map((p) => `<option value="${p.id}">${p.name} — ${p.type}</option>`).join("")}
            </select>
          </label>
          <label>Listing title
            <input type="text" name="title" placeholder="De-identified chest radiograph archive" required>
          </label>
          <label>Summary
            <textarea name="summary" rows="3" placeholder="What the dataset contains, how it was collected, what it is useful for."></textarea>
          </label>
          <div class="form-row">
            <label>Category
              <input type="text" name="category" placeholder="Healthcare" value="General">
            </label>
            <label>Price
              <input type="number" name="price" min="0" step="1000" value="0" required>
            </label>
          </div>
          <label>Currency
            <select name="currency"><option>INR</option><option>USD</option><option>EUR</option></select>
          </label>
          <label>Licence terms
            <textarea name="licence_terms" rows="3" placeholder="Permitted use, redistribution limits, retention obligations…"></textarea>
          </label>
          <button class="btn btn-primary" type="submit">Publish listing</button>
        </form>`;
      drawer.open();
      drawer.body.querySelector("#listing-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const f = e.target;
        const b = f.querySelector("button");
        busy(b, true, "Publishing…");
        try {
          const body = {};
          new FormData(f).forEach((v, k) => { body[k] = v; });
          await api("/api/listings", { method: "POST", body: JSON.stringify(body) });
          toast("Listing published to the marketplace.");
          setTimeout(() => location.reload(), 700);
        } catch (err) { toast(err.message, "error"); busy(b, false); }
      });
    });

    document.querySelectorAll("[data-withdraw]").forEach((a) => {
      a.addEventListener("click", async (e) => {
        e.preventDefault();
        if (!confirm("Withdraw this listing from the marketplace?")) return;
        try {
          await api(`/api/listings/${a.dataset.withdraw}/withdraw`, { method: "POST" });
          toast("Listing withdrawn.");
          setTimeout(() => location.reload(), 600);
        } catch (err) { toast(err.message, "error"); }
      });
    });
  },
};
