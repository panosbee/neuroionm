let cases = [];
let selectedCaseId = null;

const caseList = document.getElementById("case-list");
const caseDetail = document.getElementById("case-detail");
const caseMetrics = document.getElementById("case-metrics");
const caseCount = document.getElementById("case-count");
const caseSearch = document.getElementById("case-search");

const t = (key, vars) => (window.I18N ? window.I18N.t(key, vars) : key);

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

async function loadCases() {
  cases = await api("/api/cases");
  renderMetrics(cases);
  renderCases();
  if (cases[0]) await selectCase(cases[0].case_id);
}

function renderMetrics(items) {
  const eventTotal = items.reduce((sum, item) => sum + Number(item.event_count || 0), 0);
  const debriefTotal = items.reduce((sum, item) => sum + Number(item.debrief_count || 0), 0);
  caseMetrics.innerHTML = `
    <span>${t("cases.n_cases", { n: items.length })}</span>
    <span>${t("cases.n_events", { n: eventTotal })}</span>
    <span>${t("cases.n_debriefs", { n: debriefTotal })}</span>
  `;
}

function renderCases() {
  const query = caseSearch.value.trim().toLowerCase();
  const filtered = cases.filter((item) => searchableCaseText(item).includes(query));
  caseCount.textContent = `${filtered.length}/${cases.length}`;
  caseList.className = `case-list${filtered.length ? "" : " empty-state"}`;
  caseList.innerHTML = filtered.map(renderCaseItem).join("") || emptyCaseListText(query);
  caseList.querySelectorAll(".case-item-main").forEach((button) => {
    button.addEventListener("click", () => selectCase(button.dataset.caseId).catch(showDetailError));
  });
}

function emptyCaseListText(query) {
  return query ? t("cases.no_match") : t("cases.none_recorded");
}

function searchableCaseText(item) {
  const latest = item.latest_debrief?.debrief || {};
  return [
    item.title,
    item.non_identifying_ref,
    item.procedure_key,
    item.status,
    latest.case_summary,
    latest.confidence,
  ].filter(Boolean).join(" ").toLowerCase();
}

function renderCaseItem(item) {
  const latest = item.latest_debrief?.debrief || null;
  const active = item.case_id === selectedCaseId ? " active" : "";
  return `
    <article class="case-item${active}">
      <button class="case-item-main" type="button" data-case-id="${escapeHtml(item.case_id)}">
        <span>${escapeHtml(formatDateTime(item.updated_at || item.created_at))}</span>
        <strong>${escapeHtml(item.title)}</strong>
        <small>${escapeHtml(item.non_identifying_ref || t("cases.no_reference"))}</small>
        <div class="case-badges">
          <em>${t("cases.item_events", { n: escapeHtml(item.event_count || 0) })}</em>
          <em>${t("cases.item_debriefs", { n: escapeHtml(item.debrief_count || 0) })}</em>
          <em>${escapeHtml(latest?.confidence || t("cases.no_ai_brief"))}</em>
        </div>
        ${latest ? `<p>${escapeHtml(truncate(latest.case_summary, 150))}</p>` : `<p>${t("cases.no_saved_debrief")}</p>`}
      </button>
      <div class="case-item-actions">
        <a href="${escapeHtml(liveUrl(item))}">${t("cases.open_live")}</a>
        <a href="${escapeHtml(reportUrl(item.case_id))}">${t("cases.report")}</a>
      </div>
    </article>
  `;
}

async function selectCase(caseId) {
  selectedCaseId = caseId;
  renderCases();
  caseDetail.className = "case-detail empty-state";
  caseDetail.textContent = t("cases.loading_intel");
  const fullCase = await api(`/api/cases/${encodeURIComponent(caseId)}`);
  cases = cases.map((item) => item.case_id === caseId ? { ...item, ...fullCase } : item);
  renderCaseDetail(fullCase);
}

function renderCaseDetail(item) {
  const debriefs = item.debriefs || [];
  const events = item.events || [];
  const latest = debriefs[0]?.debrief || item.latest_debrief?.debrief || null;
  caseDetail.className = "case-detail";
  caseDetail.innerHTML = `
    <div class="case-detail-head">
      <div>
        <div class="panel-title" data-i18n="cases.selected">${t("cases.selected")}</div>
        <h2>${escapeHtml(item.title)}</h2>
        <p>${escapeHtml(item.non_identifying_ref || t("cases.no_ref"))} · ${escapeHtml(item.case_id)}</p>
      </div>
      <div class="case-detail-actions">
        <a class="primary-btn" href="${escapeHtml(liveUrl(item))}">${t("cases.open_in_live")}</a>
        <a class="secondary-btn" href="${escapeHtml(reportUrl(item.case_id))}">${t("cases.export_report")}</a>
      </div>
    </div>
    <div class="case-stat-grid">
      ${renderStat(t("cases.stat_events"), events.length)}
      ${renderStat(t("cases.stat_debriefs"), debriefs.length)}
      ${renderStat(t("cases.stat_updated"), formatDateTime(item.updated_at || item.created_at))}
      ${renderStat(t("cases.stat_procedure"), item.procedure_key || t("cases.not_set"))}
    </div>
    <section class="case-preview-section">
      <div class="section-heading compact-heading"><h3>${t("cases.latest_debrief")}</h3><span>${escapeHtml(latest?.confidence || t("cases.none"))}</span></div>
      ${latest ? renderDebriefPreview(latest) : `<div class="empty-state compact">${t("cases.no_debrief_generated")}</div>`}
    </section>
    <section class="case-preview-section">
      <div class="section-heading compact-heading"><h3>${t("cases.timeline")}</h3><span>${events.length}</span></div>
      <div class="case-preview-list">
        ${events.map(renderEventPreview).join("") || `<div class="empty-state compact">${t("cases.no_timeline")}</div>`}
      </div>
    </section>
    <section class="case-preview-section">
      <div class="section-heading compact-heading"><h3>${t("cases.saved_debriefs")}</h3><span>${debriefs.length}</span></div>
      <div class="case-preview-list">
        ${debriefs.map(renderDebriefHistoryPreview).join("") || `<div class="empty-state compact">${t("cases.no_saved_debriefs")}</div>`}
      </div>
    </section>
  `;
}

function renderStat(label, value) {
  return `<div class="case-stat"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function renderDebriefPreview(debrief) {
  return `
    <div class="case-debrief-preview ${escapeHtml(debrief.confidence || "low")}">
      <strong>${escapeHtml(debrief.case_summary || t("cases.no_summary"))}</strong>
      <div class="case-mini-grid">
        ${renderMiniList(t("cases.doc_gaps"), debrief.documentation_gaps || [])}
        ${renderMiniList(t("cases.follow_up"), debrief.follow_up || [])}
      </div>
    </div>
  `;
}

function renderMiniList(title, items) {
  return `
    <div class="case-mini-list">
      <span>${escapeHtml(title)}</span>
      ${(items || []).slice(0, 3).map((item) => `<small>${escapeHtml(item)}</small>`).join("") || `<small>${t("cases.none_listed")}</small>`}
    </div>
  `;
}

function renderEventPreview(event) {
  return `
    <div class="case-preview-item">
      <strong>${escapeHtml(event.title)}</strong>
      <span>${escapeHtml(event.event_type)} · ${escapeHtml(formatDateTime(event.created_at))}</span>
      <p>${escapeHtml(event.description || "")}</p>
    </div>
  `;
}

function renderDebriefHistoryPreview(item) {
  const debrief = item.debrief || {};
  return `
    <div class="case-preview-item">
      <strong>${escapeHtml(truncate(debrief.case_summary || t("cases.saved_debrief"), 180))}</strong>
      <span>${escapeHtml(formatDateTime(item.created_at))} · ${escapeHtml(debrief.confidence || t("common.unknown"))} · ${t("cases.item_events", { n: escapeHtml(item.timeline_event_count ?? 0) })}</span>
    </div>
  `;
}

function liveUrl(item) {
  const nodeKey = item.procedure_key || item.selected_node_keys?.[0] || "live";
  return `/live.html?node=${encodeURIComponent(nodeKey)}&case_id=${encodeURIComponent(item.case_id)}&started=1`;
}

function reportUrl(caseId) {
  return `/case-report.html?case_id=${encodeURIComponent(caseId)}`;
}

function showDetailError(error) {
  caseDetail.className = "case-detail empty-state";
  caseDetail.textContent = error.message;
}

function formatDateTime(value) {
  if (!value) return t("common.unknown");
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const locale = window.I18N && window.I18N.getLang() === "en" ? "en-GB" : "el-GR";
  return date.toLocaleString(locale, { dateStyle: "short", timeStyle: "short" });
}

function truncate(value, maxLength) {
  const text = String(value || "");
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

caseSearch.addEventListener("input", renderCases);
document.addEventListener("ionm:langchange", () => {
  if (!cases.length) return;
  renderMetrics(cases);
  renderCases();
  if (selectedCaseId) {
    const current = cases.find((item) => item.case_id === selectedCaseId);
    if (current) renderCaseDetail(current);
  }
});
loadCases().catch((error) => {
  caseList.className = "case-list empty-state";
  caseList.textContent = error.message;
});