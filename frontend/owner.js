// IONM Owner Console — control-plane UI.
// The owner submits provisioning intents; the root daemon executes them. This
// page only talks to /api/owner/* and reflects live progress.

const statusEl = document.getElementById("owner-status");
const createForm = document.getElementById("create-form");
const createOutput = document.getElementById("create-output");
const createSubmit = document.getElementById("create-submit");
const requestsList = document.getElementById("requests-list");
const tenantsList = document.getElementById("tenants-list");

const slugInput = document.getElementById("f-slug");
const companyInput = document.getElementById("f-company");

let slugTouched = false;
let pollTimer = null;

init().catch((error) => {
  // Not an owner (or not logged in): bounce to login and come back here.
  console.warn(error);
  window.location.assign(`/login.html?next=${encodeURIComponent("/owner.html")}`);
});

async function init() {
  const session = await api("/api/owner/session");
  const owner = session.owner || {};
  statusEl.textContent = owner.display_name ? `Owner: ${owner.display_name}` : "Owner";
  await Promise.all([loadTenants(), loadRequests()]);
  schedulePollIfActive();
}

// ---- API helper -------------------------------------------------------------
async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const detail = await extractError(response);
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  if (response.status === 204) return {};
  return response.json();
}

async function extractError(response) {
  try {
    const body = await response.json();
    return body.detail || response.statusText;
  } catch (error) {
    return response.statusText || "Σφάλμα αιτήματος";
  }
}

// ---- Auto-slug from company name -------------------------------------------
slugInput.addEventListener("input", () => { slugTouched = true; });
companyInput.addEventListener("input", () => {
  if (!slugTouched) slugInput.value = slugify(companyInput.value);
});

function slugify(text) {
  return (text || "")
    .toLowerCase()
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 32);
}

// ---- Create tenant ----------------------------------------------------------
createForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  createOutput.className = "form-output";
  createOutput.textContent = "Αποστολή αιτήματος...";
  createSubmit.disabled = true;
  const payload = {
    company: companyInput.value.trim(),
    slug: slugInput.value.trim(),
    lang: document.getElementById("f-lang").value,
    domain: document.getElementById("f-domain").value.trim() || null,
    admin_user: document.getElementById("f-admin-user").value.trim() || null,
    admin_password: document.getElementById("f-admin-pass").value,
    tls: document.getElementById("f-tls").checked,
  };
  try {
    const result = await api("/api/owner/tenants", { method: "POST", body: JSON.stringify(payload) });
    createOutput.className = "form-output ok";
    createOutput.textContent = `Το αίτημα για '${result.request.slug}' καταχωρήθηκε. Παρακολούθησε την πρόοδο δεξιά.`;
    document.getElementById("f-admin-pass").value = "";
    await loadRequests();
    startPolling();
  } catch (error) {
    createOutput.className = "form-output error";
    createOutput.textContent = error.message;
  } finally {
    createSubmit.disabled = false;
  }
});

// ---- Tenants list -----------------------------------------------------------
async function loadTenants() {
  const data = await api("/api/owner/tenants");
  renderTenants(data.items || []);
}

function renderTenants(items) {
  if (!items.length) {
    tenantsList.innerHTML = `<p class="hint">Καμία εταιρεία ακόμα.</p>`;
    return;
  }
  const rows = items.map((t) => `
    <tr>
      <td><strong>${escapeHtml(t.company || t.slug)}</strong><br><span class="hint">${escapeHtml(t.slug)}</span></td>
      <td>${t.login_url ? `<a href="${escapeHtml(t.login_url)}" target="_blank" rel="noopener">${escapeHtml(t.domain)}</a>` : escapeHtml(t.domain || "")}</td>
      <td>${escapeHtml(t.admin_user || "")}</td>
      <td>${(t.lang || "").toUpperCase()}</td>
      <td><span class="badge ${escapeHtml(t.status || "active")}">${escapeHtml(t.status || "active")}</span></td>
      <td>${t.status === "removed" ? "" : `<button class="link-btn" data-rotate="${escapeHtml(t.slug)}" style="color:#60a5fa">Νέα διαπιστευτήρια</button> · <button class="link-btn" data-deprovision="${escapeHtml(t.slug)}">Αφαίρεση</button>`}</td>
    </tr>`).join("");
  tenantsList.innerHTML = `
    <table class="tenant-table">
      <thead><tr><th>Εταιρεία</th><th>Domain</th><th>Admin</th><th>Γλ.</th><th>Κατάσταση</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  tenantsList.querySelectorAll("[data-deprovision]").forEach((btn) => {
    btn.addEventListener("click", () => deprovision(btn.getAttribute("data-deprovision")));
  });
  tenantsList.querySelectorAll("[data-rotate]").forEach((btn) => {
    btn.addEventListener("click", () => rotateAdmin(btn.getAttribute("data-rotate")));
  });
}

async function rotateAdmin(slug) {
  const password = window.prompt(
    `Νέος κωδικός admin για το tenant '${slug}'.\n` +
    `Τουλάχιστον 8 χαρακτήρες. Θα τον παραδώσεις στον πελάτη — ο πελάτης μπορεί να τον αλλάξει μετά μόνος του.`
  );
  if (password == null) return;
  const trimmed = password.trim();
  if (trimmed.length < 8) {
    window.alert("Ο κωδικός πρέπει να έχει τουλάχιστον 8 χαρακτήρες.");
    return;
  }
  const adminUser = window.prompt(
    `Νέο admin username για το '${slug}' (προαιρετικό).\nΆφησέ το κενό για να διατηρηθεί το τρέχον username.`
  );
  if (adminUser == null) return;
  const payload = { admin_password: trimmed };
  if (adminUser.trim()) payload.admin_user = adminUser.trim();
  try {
    await api(`/api/owner/tenants/${encodeURIComponent(slug)}/rotate-admin`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    await loadRequests();
    startPolling();
  } catch (error) {
    window.alert(error.message);
  }
}

async function deprovision(slug) {
  if (!window.confirm(`Αφαίρεση του tenant '${slug}';\nΗ βάση δεδομένων ΔΕΝ διαγράφεται (κρατιέται αντίγραφο).`)) return;
  const typed = window.prompt(`Γράψε ξανά το slug '${slug}' για επιβεβαίωση:`);
  if (typed == null) return;
  try {
    await api(`/api/owner/tenants/${encodeURIComponent(slug)}/deprovision`, {
      method: "POST",
      body: JSON.stringify({ confirm_slug: typed.trim(), drop_db: false }),
    });
    await loadRequests();
    startPolling();
  } catch (error) {
    window.alert(error.message);
  }
}

// ---- Requests list + live polling ------------------------------------------
async function loadRequests() {
  const data = await api("/api/owner/requests?limit=20");
  renderRequests(data.items || []);
  return data.items || [];
}

function renderRequests(items) {
  if (!items.length) {
    requestsList.innerHTML = `<p class="hint">Κανένα αίτημα ακόμα.</p>`;
    return;
  }
  requestsList.innerHTML = items.map((r) => {
    const logText = (r.log || []).join("\n");
    const result = r.status === "completed" && r.result && r.result.login_url
      ? `<div class="req-result">✅ Έτοιμο · <a href="${escapeHtml(r.result.login_url)}" target="_blank" rel="noopener">${escapeHtml(r.result.login_url)}</a></div>`
      : "";
    const errorLine = r.status === "failed" && r.error
      ? `<div class="req-result" style="color:#f87171">⚠ ${escapeHtml(r.error)}</div>`
      : "";
    return `
      <div class="req-item">
        <div class="req-head">
          <strong>${escapeHtml(r.company || r.slug)} <span class="hint">(${escapeHtml(r.action || "provision")})</span></strong>
          <span class="badge ${escapeHtml(r.status)}">${escapeHtml(r.status)}</span>
        </div>
        ${logText ? `<pre class="req-log">${escapeHtml(logText)}</pre>` : ""}
        ${result}${errorLine}
      </div>`;
  }).join("");
}

function hasActive(items) {
  return items.some((r) => r.status === "pending" || r.status === "running");
}

async function schedulePollIfActive() {
  const items = await loadRequests();
  if (hasActive(items)) startPolling();
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    try {
      const items = await loadRequests();
      if (!hasActive(items)) {
        clearInterval(pollTimer);
        pollTimer = null;
        await loadTenants();
      }
    } catch (error) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }, 2500);
}

// ---- Manual refresh + logout ------------------------------------------------
document.getElementById("refresh-requests").addEventListener("click", () => loadRequests());
document.getElementById("refresh-tenants").addEventListener("click", () => loadTenants());

document.querySelector("[data-logout]")?.addEventListener("click", async () => {
  try { await api("/api/admin/logout", { method: "POST" }); } catch (error) { /* ignore */ }
  window.location.assign("/login.html");
});

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);
}
