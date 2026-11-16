const reportRoot = document.getElementById("case-report");
const printButton = document.getElementById("print-report-btn");
const params = new URLSearchParams(window.location.search);
const caseId = params.get("case_id");

const t = (key, vars) => (window.I18N ? window.I18N.t(key, vars) : key);
let lastCaseData = null;

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

async function loadReport() {
  if (!caseId) throw new Error(t("report.missing_case"));
  const caseData = await api(`/api/cases/${encodeURIComponent(caseId)}`);
  lastCaseData = caseData;
  renderReport(caseData);
}

function renderReport(caseData) {
  const events = caseData.events || [];
  const debrief = caseData.debriefs?.[0]?.debrief || null;
  const evidence = caseData.debriefs?.[0]?.retrieval?.items || [];
  reportRoot.className = "case-report-document";
  reportRoot.innerHTML = `
    <header class="report-header-block">
      <div>
        <span>${t("report.title_label")}</span>
        <h2>${escapeHtml(caseData.title)}</h2>
        <p>${escapeHtml(caseData.non_identifying_ref || t("cases.no_ref"))} · ${escapeHtml(caseData.case_id)}</p>
      </div>
      <div class="report-meta">
        <strong>${escapeHtml(formatDateTime(caseData.updated_at || caseData.created_at))}</strong>
        <small>${escapeHtml(caseData.procedure_key || t("report.procedure_not_set"))}</small>
      </div>
    </header>

    <section class="report-section">
      <h3>${t("report.case_summary")}</h3>
      ${debrief ? `<p>${escapeHtml(debrief.case_summary)}</p>` : `<p>${t("report.no_debrief")}</p>`}
      <div class="report-stat-row">
        ${renderReportStat(t("report.stat_events"), events.length)}
        ${renderReportStat(t("report.stat_debriefs"), caseData.debriefs?.length || 0)}
        ${renderReportStat(t("report.stat_confidence"), debrief?.confidence || "n/a")}
      </div>
    </section>

    <section class="report-section">
      <h3>${t("report.timeline")}</h3>
      ${events.map(renderTimelineRow).join("") || `<p>${t("report.no_timeline")}</p>`}
    </section>

    <section class="report-section">
      <h3>${t("report.decision_points")}</h3>
      ${renderObjectRows(debrief?.decision_points || [], "moment", ["recommended_action", "rationale"])}
    </section>

    <section class="report-section">
      <h3>${t("report.doc_gaps")}</h3>
      ${renderStringRows(debrief?.documentation_gaps || [])}
    </section>

    <section class="report-section">
      <h3>${t("report.learning_points")}</h3>
      ${renderObjectRows(debrief?.academy_learning_points || [], "topic", ["why_it_matters", "practice_prompt"])}
    </section>

    <section class="report-section">
      <h3>${t("report.evidence_trace")}</h3>
      ${evidence.map(renderEvidenceRow).join("") || `<p>${t("report.no_evidence")}</p>`}
    </section>

    <section class="report-caution">
      <strong>${t("report.caution")}</strong>
      <p>${escapeHtml(debrief?.clinical_caution || t("report.caution_default"))}</p>
    </section>
  `;
}

function renderReportStat(label, value) {
  return `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function renderTimelineRow(event) {
  return `
    <div class="report-row">
      <strong>${escapeHtml(event.title)}</strong>
      <span>${escapeHtml(event.event_type)} · ${escapeHtml(formatDateTime(event.created_at))}</span>
      <p>${escapeHtml(event.description || "")}</p>
    </div>
  `;
}

function renderObjectRows(items, titleKey, detailKeys) {
  return items.map((item) => `
    <div class="report-row">
      <strong>${escapeHtml(item[titleKey])}</strong>
      ${detailKeys.map((key) => `<p>${escapeHtml(item[key])}</p>`).join("")}
    </div>
  `).join("") || `<p>${t("report.no_items")}</p>`;
}

function renderStringRows(items) {
  return items.map((item) => `<div class="report-row"><p>${escapeHtml(item)}</p></div>`).join("") || `<p>${t("report.no_items")}</p>`;
}

function renderEvidenceRow(item) {
  const source = [item.source_collection, item.source_key].filter(Boolean).join(" · ");
  return `
    <div class="report-row">
      <strong>${escapeHtml(item.title || item.source_key || t("report.knowledge_evidence"))}</strong>
      <span>${escapeHtml(source)}${item.score ? ` · ${escapeHtml(item.score)}` : ""}</span>
    </div>
  `;
}

function formatDateTime(value) {
  if (!value) return t("common.unknown");
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const locale = window.I18N && window.I18N.getLang() === "en" ? "en-GB" : "el-GR";
  return date.toLocaleString(locale, { dateStyle: "short", timeStyle: "short" });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

printButton.addEventListener("click", () => window.print());
document.addEventListener("ionm:langchange", () => {
  if (lastCaseData) renderReport(lastCaseData);
});
loadReport().catch((error) => {
  reportRoot.className = "case-report-document empty-state";
  reportRoot.textContent = error.message;
});