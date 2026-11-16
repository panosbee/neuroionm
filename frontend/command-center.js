const vitals = document.getElementById("command-vitals");
const procedureSelect = document.getElementById("command-procedure");
const procedureCount = document.getElementById("procedure-count");
const plannerPreview = document.getElementById("planner-preview");
const startCaseForm = document.getElementById("start-case-form");
const startCaseStatus = document.getElementById("start-case-status");
const startButton = document.getElementById("command-start-btn");
const caseTitleInput = document.getElementById("command-case-title");

const t = (key, vars) => (window.I18N ? window.I18N.t(key, vars) : key);
let lastHealth = null;
let lastHealthError = null;
let lastProcedureCount = 0;
let lastPlanner = null;

const preferredStartKeys = [
  "live.spine.cervical.disc.c4-c5",
  "live.spine.cervical.disc.c5-c6",
  "live.spine.cervical.disc.c6-c7",
  "live.spine.lumbar.disc.l4-l5",
  "live.spine.lumbar.disc.l5-s1",
  "live.cranial.templates.acoustic",
  "live.cranial.templates.posteriorFossa",
  "live.cranial.templates.skullBase",
];

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || response.statusText);
  }
  return response.json();
}

async function initCommandCenter() {
  try {
    const [health, tree] = await Promise.all([api("/api/health"), api("/api/knowledge/tree")]);
    renderVitals(health);
    const procedures = collectProcedures(tree);
    renderProcedureOptions(procedures);
    await refreshPlannerPreview();
  } catch (error) {
    renderVitals(null, error);
    startCaseStatus.textContent = error.message;
    procedureSelect.innerHTML = `<option value="">${escapeHtml(t("procedure.unavailable"))}</option>`;
    startButton.disabled = true;
  }
}

function renderVitals(health, error = null) {
  if (!vitals) return;
  lastHealth = error ? null : health;
  lastHealthError = error;
  if (error) {
    vitals.innerHTML = `<span class="vital-error">${escapeHtml(t("vitals.api_unavailable"))}</span><span>${escapeHtml(error.message)}</span>`;
    return;
  }
  vitals.innerHTML = `
    <span class="vital-ok">${escapeHtml(health.app || "NeuroIONM")}</span>
    <span class="${health.mongo_connected ? "vital-ok" : "vital-error"}">${escapeHtml(health.mongo_connected ? t("vitals.mongo_connected") : t("vitals.mongo_offline"))}</span>
    <span class="${health.deepseek_configured ? "vital-ok" : "vital-warn"}">${escapeHtml(health.deepseek_configured ? t("vitals.deepseek_ready") : t("vitals.deepseek_not"))}</span>
    <span class="${health.jina_configured ? "vital-ok" : "vital-warn"}">${escapeHtml(health.jina_configured ? t("vitals.jina_ready") : t("vitals.jina_not"))}</span>
  `;
}

function collectProcedures(tree) {
  const roots = Array.isArray(tree) ? tree : [tree];
  const liveRoot = findNode(roots, "live") || roots[0];
  const procedures = [];
  walkTree(liveRoot ? [liveRoot] : roots, [], procedures);
  return procedures.sort((a, b) => rankProcedure(a) - rankProcedure(b) || a.label.localeCompare(b.label, "el"));
}

function findNode(nodes, key) {
  for (const node of nodes) {
    if (node.key === key) return node;
    const match = findNode(node.children || [], key);
    if (match) return match;
  }
  return null;
}

function walkTree(nodes, trail, procedures) {
  for (const node of nodes) {
    const nextTrail = node.key === "live" ? [] : [...trail, node.title_el || node.key];
    if (isClinicalStartNode(node)) {
      procedures.push({
        key: node.key,
        label: nextTrail.join(" / "),
        title: node.title_el || node.key,
        type: node.node_type,
      });
    }
    walkTree(node.children || [], nextTrail, procedures);
  }
}

function isClinicalStartNode(node) {
  return ["disc_level", "nerve_root", "procedure_template"].includes(node.node_type);
}

function rankProcedure(item) {
  const preferredIndex = preferredStartKeys.indexOf(item.key);
  if (preferredIndex >= 0) return preferredIndex;
  if (item.type === "disc_level") return 100;
  if (item.type === "procedure_template") return 200;
  return 300;
}

function renderProcedureOptions(procedures) {
  lastProcedureCount = procedures.length;
  procedureCount.textContent = t("procedure.count", { n: procedures.length });
  procedureSelect.innerHTML = procedures.map((procedure) => `
    <option value="${escapeHtml(procedure.key)}" data-title="${escapeHtml(procedure.title)}">${escapeHtml(procedure.label)}</option>
  `).join("");
  const defaultKey = preferredStartKeys.find((key) => procedures.some((procedure) => procedure.key === key));
  if (defaultKey) procedureSelect.value = defaultKey;
  syncCaseTitle();
}

function syncCaseTitle() {
  if (caseTitleInput.dataset.userEdited === "true") return;
  const selected = procedureSelect.selectedOptions[0];
  const title = selected?.dataset.title || selected?.textContent?.split("/").at(-1)?.trim();
  if (title) caseTitleInput.value = `${title} IONM`;
}

async function refreshPlannerPreview() {
  const nodeKey = procedureSelect.value;
  if (!nodeKey) return;
  plannerPreview.innerHTML = `
    <div class="panel-title">${escapeHtml(t("command.preview.kicker"))}</div>
    <div class="empty-state compact">${escapeHtml(t("command.preview.loading"))}</div>
  `;
  const planner = await api(`/api/knowledge/planner?node_key=${encodeURIComponent(nodeKey)}`);
  lastPlanner = planner;
  plannerPreview.innerHTML = renderPlannerPreview(planner);
}

function renderPlannerPreview(planner) {
  const plan = planner.clinical_plan || {};
  const coverage = plan.coverage || {};
  const readiness = plan.readiness || {};
  const gaps = plan.gaps || [];
  const roots = [...(planner.roots || []), ...(planner.cranial_nerves || []).map((item) => `CN ${item}`)];
  return `
    <div class="panel-title">${escapeHtml(t("command.preview.kicker"))}</div>
    <div class="preview-heading">
      <strong>${escapeHtml(planner.node?.title_el || t("command.preview.selected_node"))}</strong>
      <span>${escapeHtml(readiness.detail || t("command.preview.ready"))}</span>
    </div>
    <div class="preview-metrics">
      <span><b>${escapeHtml(roots.join(" · ") || "domain")}</b> ${escapeHtml(t("command.preview.anatomy"))}</span>
      <span><b>${escapeHtml(coverage.muscle_count ?? 0)}</b> ${escapeHtml(t("command.preview.channels"))}</span>
      <span><b>${escapeHtml(coverage.pin_count ?? 0)}</b> ${escapeHtml(t("command.preview.pins"))}</span>
      <span><b>${escapeHtml(coverage.protocol_count ?? 0)}</b> ${escapeHtml(t("command.preview.protocols"))}</span>
    </div>
    <div class="preview-flags">
      ${gaps.slice(0, 4).map((gap) => `<span class="${escapeHtml(gap.severity || "review")}">${escapeHtml(gap.title || gap.detail)}</span>`).join("") || `<span>${escapeHtml(t("command.preview.no_flags"))}</span>`}
    </div>
  `;
}

async function startLiveCase(event) {
  event.preventDefault();
  const nodeKey = procedureSelect.value;
  const title = document.getElementById("command-case-title").value.trim();
  const nonIdentifyingRef = document.getElementById("command-case-ref").value.trim();
  const phase = document.getElementById("command-phase").value;
  const risk = document.getElementById("command-risk").value;
  const notes = document.getElementById("command-notes").value.trim();
  if (!nodeKey || !title) {
    startCaseStatus.textContent = t("command.need_title");
    return;
  }
  startButton.disabled = true;
  startCaseStatus.textContent = t("command.creating");
  try {
    const briefing = buildBriefingText(phase, risk, notes);
    const created = await api("/api/cases", {
      method: "POST",
      body: JSON.stringify({
        title,
        mode: "live",
        procedure_key: nodeKey,
        selected_node_keys: [nodeKey],
        non_identifying_ref: nonIdentifyingRef || null,
        notes: briefing,
      }),
    });
    await api(`/api/cases/${encodeURIComponent(created.case_id)}/events`, {
      method: "POST",
      body: JSON.stringify({
        event_type: "note",
        title: "Command Center briefing",
        description: briefing,
        node_key: nodeKey,
        metadata: { source: "command_center", phase, risk_focus: risk },
      }),
    });
    const params = new URLSearchParams({ node: nodeKey, case_id: created.case_id, started: "1" });
    window.location.assign(`/live.html?${params.toString()}`);
  } catch (error) {
    startButton.disabled = false;
    startCaseStatus.textContent = error.message;
  }
}

function buildBriefingText(phase, risk, notes) {
  const rows = [`Phase: ${phase}`, `Risk focus: ${risk}`];
  if (notes) rows.push(`Notes: ${notes}`);
  return rows.join("\n");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

procedureSelect.addEventListener("change", () => {
  syncCaseTitle();
  refreshPlannerPreview().catch((error) => {
    plannerPreview.innerHTML = `<div class="panel-title">${escapeHtml(t("command.preview.kicker"))}</div><div class="empty-state compact">${escapeHtml(error.message)}</div>`;
  });
});
caseTitleInput.addEventListener("input", () => {
  caseTitleInput.dataset.userEdited = caseTitleInput.value.trim() ? "true" : "false";
});
startCaseForm.addEventListener("submit", (event) => startLiveCase(event));

document.addEventListener("ionm:langchange", () => {
  if (lastHealth || lastHealthError) renderVitals(lastHealth, lastHealthError);
  if (lastProcedureCount) procedureCount.textContent = t("procedure.count", { n: lastProcedureCount });
  if (lastPlanner) plannerPreview.innerHTML = renderPlannerPreview(lastPlanner);
});

initCommandCenter();