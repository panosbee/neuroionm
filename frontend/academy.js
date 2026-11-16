let academyData = null;
let selectedNodeKey = null;
let selectedPlanner = null;
let currentLesson = null;
let currentSimulation = null;
let currentWaveforms = null;
let currentBibliography = null;
let currentLessonProgress = { checklist: {} };
let currentPins = [];
let currentMapView = "full";
let bodyMapAspect = 0.62;
let simulationState = createEmptySimulationState();
let selectedWaveformScenarioKey = "baseline";
let waveformCoachResult = null;
let waveformParameters = null;
let waveformParametersDirty = false;
let waveformApplyNotice = null;
let waveformQuizAnswers = {};
let waveformQuizGrade = null;
let checkpointAnswers = {};
let checkpointGrade = null;
let caseScenarioAnswers = {};
let caseScenarioGrades = {};
let academyLearners = [];
let selectedLearner = null;
let learnerReport = null;

const healthStatus = document.getElementById("health-status");
const academyMetrics = document.getElementById("academy-metrics");
const learnerPanel = document.getElementById("learner-panel");
const academyModules = document.getElementById("academy-modules");
const academyLibrary = document.getElementById("academy-library");
const lessonDetail = document.getElementById("lesson-detail");
const simulationPanel = document.getElementById("simulation-panel");
const waveformPanel = document.getElementById("waveform-panel");
const evidencePanel = document.getElementById("evidence-panel");
const academyMain = document.querySelector(".academy-main");
const academyWorkspaceTabs = document.getElementById("academy-workspace-tabs");
const tutorQuestion = document.getElementById("tutor-question");
const tutorAnswer = document.getElementById("tutor-answer");
const askTutorButton = document.getElementById("ask-tutor-btn");
const pinsLayer = document.getElementById("pins-layer");
const bodyMap = document.getElementById("bodymap");
const bodyMapImage = document.getElementById("bodymap-image");
const mapViewControls = document.getElementById("map-view-controls");
const mapViewCount = document.getElementById("map-view-count");

const t = (key, vars) => (window.I18N ? window.I18N.t(key, vars) : key);
// Localize a content field (KB/muscle/channel/target data) to the active language.
const L = (obj, base) => (window.I18N && window.I18N.L ? window.I18N.L(obj, base) : (obj ? obj[base] : undefined));

function tf(prefix, key, fallback) {
  const full = `${prefix}.${key}`;
  const value = t(full);
  return value === full ? (fallback ?? key) : value;
}

function nodeTypeLabel(type) {
  return tf("nodetype", type, type);
}

const mapViewDefinitions = {
  full: { i18nKey: "view.full", bounds: { x1: 0, y1: 0, x2: 100, y2: 100 } },
  anterior: { i18nKey: "view.anterior", bounds: { x1: 31, y1: 2, x2: 65, y2: 47 } },
  posterior: { i18nKey: "view.posterior", bounds: { x1: 31, y1: 52, x2: 66, y2: 96 } },
  headNeck: { i18nKey: "view.cranial", bounds: { x1: 36, y1: 1, x2: 62, y2: 18 } },
  upperLimb: { i18nKey: "view.upper_limb", bounds: { x1: 22, y1: 8, x2: 73, y2: 32 } },
  posteriorUpperLimb: { i18nKey: "view.post_upper", bounds: { x1: 30, y1: 58, x2: 68, y2: 76 } },
  lumbosacral: { i18nKey: "view.lumbosacral", bounds: { x1: 27, y1: 23, x2: 70, y2: 70 } },
  lowerLimb: { i18nKey: "view.lower_limb", bounds: { x1: 26, y1: 31, x2: 69, y2: 96 } },
};

function createEmptySimulationState() {
  return {
    active: false,
    placements: [],
    selectedTargetId: null,
    validation: null,
    coachResult: null,
  };
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || response.statusText);
  }
  return response.json();
}

async function init() {
  initialiseBodyMap();
  bindWorkspaceTabs();
  try {
    const health = await api("/api/health");
    healthStatus.textContent = health.mongo_connected ? t("health.mongo_on") : t("health.mongo_off");
    healthStatus.className = `status ${health.mongo_connected ? "ok" : "error"}`;

    await loadLearnerState();
    academyData = await api("/api/academy/curriculum");
    renderAcademyShell();

    const requestedNode = new URLSearchParams(window.location.search).get("node");
    const hasRequestedNode = Boolean(requestedNode && requestedNode !== "academy");
    const initialNode = hasRequestedNode
      ? requestedNode
      : firstLessonKey() || academyData.simulation_library?.[0]?.node_key;
    if (initialNode) await selectLesson(initialNode, { startSimulation: hasRequestedNode });
  } catch (error) {
    healthStatus.textContent = t("health.api_error");
    healthStatus.className = "status error";
    lessonDetail.className = "academy-panel empty-state compact";
    lessonDetail.textContent = error.message;
  }
}

function bindWorkspaceTabs() {
  academyWorkspaceTabs?.querySelectorAll("button[data-academy-tab]").forEach((button) => {
    button.addEventListener("click", () => setAcademyWorkspacePanel(button.dataset.academyTab));
  });
}

function setAcademyWorkspacePanel(panelKey) {
  if (!academyMain || !document.querySelector(`[data-academy-panel="${panelKey}"]`)) return;
  academyMain.dataset.activePanel = panelKey;
  academyWorkspaceTabs?.querySelectorAll("button[data-academy-tab]").forEach((button) => {
    const active = button.dataset.academyTab === panelKey;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
}

async function loadLearnerState() {
  const [learners, session] = await Promise.all([
    api("/api/academy/learners"),
    api("/api/academy/learners/session"),
  ]);
  academyLearners = learners.items || [];
  selectedLearner = session.learner || null;
  learnerReport = selectedLearner ? await loadLearnerReport() : null;
}

async function loadLearnerReport() {
  try {
    return await api("/api/academy/progress/report");
  } catch (error) {
    return null;
  }
}

function renderAcademyShell() {
  const coverage = academyData.coverage || {};
  academyMetrics.innerHTML = `
    ${renderMetric(t("acad.m_modules"), coverage.modules ?? 0, t("acad.m_curriculum"))}
    ${renderMetric(t("acad.m_lessons"), coverage.academy_nodes ?? 0, t("acad.m_kb_nodes"))}
    ${renderMetric(t("acad.m_labs"), coverage.simulation_ready_nodes ?? 0, t("acad.m_sim_ready"))}
  `;

  academyModules.className = "academy-modules vertical-list";
  renderLearnerPanel();
  academyModules.innerHTML = (academyData.modules || []).map(renderAcademyModule).join("");
  academyLibrary.innerHTML = (academyData.simulation_library || []).map(renderAcademySimulationCard).join("");
  bindAcademyNavigation();
}

function renderLearnerPanel() {
  if (!learnerPanel) return;
  learnerPanel.className = "learner-panel";
  const reportSummary = learnerReport?.summary || {};
  const canStart = Boolean(firstLessonKey());
  const canContinue = Boolean(nextLessonKey());
  learnerPanel.innerHTML = `
    <div class="section-heading compact-heading">
      <h3>${escapeHtml(t("acad.learner"))}</h3>
      <span>${escapeHtml(selectedLearner ? t("acad.mongo_progress") : t("acad.select_profile"))}</span>
    </div>
    <select id="learner-select" aria-label="${escapeHtml(t("acad.learner_aria"))}">
      <option value="">${escapeHtml(t("acad.select_learner"))}</option>
      ${academyLearners.map((learner) => `
        <option value="${escapeHtml(learner.learner_id)}" ${selectedLearner?.learner_id === learner.learner_id ? "selected" : ""}>
          ${escapeHtml(learner.full_name || learner.username || learner.email)}
        </option>
      `).join("")}
    </select>
    <div class="learner-actions">
      <button id="start-academy-btn" class="primary-btn" type="button" ${canStart ? "" : "disabled"}>${escapeHtml(t("acad.start_academy"))}</button>
      <button id="continue-academy-btn" class="secondary-btn" type="button" ${canContinue ? "" : "disabled"}>${escapeHtml(t("acad.continue_btn"))}</button>
    </div>
    ${selectedLearner ? `
      <div class="learner-summary">
        <strong>${escapeHtml(selectedLearner.full_name || selectedLearner.username)}</strong>
        <span>${escapeHtml([selectedLearner.specialty, selectedLearner.organization].filter(Boolean).join(" · ") || selectedLearner.email || t("acad.academy_profile"))}</span>
      </div>
      <div class="learner-report-mini">
        ${renderMiniReportMetric(t("acad.mini_started"), reportSummary.lessons_started ?? 0)}
        ${renderMiniReportMetric(t("acad.mini_complete"), reportSummary.lessons_completed ?? 0)}
        ${renderMiniReportMetric(t("acad.mini_avg"), `${reportSummary.average_score ?? 0}%`)}
        ${renderMiniReportMetric(t("acad.mini_review"), reportSummary.needs_review_count ?? 0)}
      </div>
      ${renderReportReviewList()}
      ${renderAdaptivePath()}
    ` : `<div class="empty-state compact">${escapeHtml(t("acad.create_users_hint"))}</div>`}
  `;
  learnerPanel.querySelector("#learner-select")?.addEventListener("change", (event) => selectLearner(event.target.value).catch(showLearnerError));
  learnerPanel.querySelector("#start-academy-btn")?.addEventListener("click", () => startAcademy().catch(showLearnerError));
  learnerPanel.querySelector("#continue-academy-btn")?.addEventListener("click", () => continueAcademy().catch(showLearnerError));
  learnerPanel.querySelectorAll("[data-adaptive-key]").forEach((button) => {
    button.addEventListener("click", () => selectLesson(button.dataset.adaptiveKey).then(() => setAcademyWorkspacePanel("lesson")).catch(showLearnerError));
  });
}

async function startAcademy() {
  const nodeKey = firstLessonKey();
  if (!nodeKey) return;
  await selectLesson(nodeKey);
  setAcademyWorkspacePanel("lesson");
}

async function continueAcademy() {
  const nodeKey = nextLessonKey();
  if (!nodeKey) return;
  await selectLesson(nodeKey);
  setAcademyWorkspacePanel("lesson");
}

function renderMiniReportMetric(label, value) {
  return `<span><b>${escapeHtml(value)}</b>${escapeHtml(label)}</span>`;
}

function renderReportReviewList() {
  const review = learnerReport?.recommended_review || [];
  if (!review.length) return "";
  return `
    <div class="learner-review-list">
      ${review.slice(0, 4).map((item) => `<span>${escapeHtml(item)}</span>`).join("")}
    </div>
  `;
}

function renderAdaptivePath() {
  const path = learnerReport?.adaptive_path || [];
  if (!path.length) return "";
  return `
    <div class="adaptive-path">
      <span class="adaptive-path-title">${escapeHtml(t("acad.adaptive_next"))}</span>
      ${path
        .slice(0, 4)
        .map(
          (item) => `
        <button class="adaptive-step ${escapeHtml(item.priority)}" type="button" data-adaptive-key="${escapeHtml(item.node_key)}">
          <span class="adaptive-rank">${escapeHtml(item.rank ?? "")}</span>
          <span class="adaptive-main">
            <strong>${escapeHtml(item.title || item.node_key)}</strong>
            <small>${escapeHtml(tf("acad_adaptive", item.priority, item.priority))}${item.focus_areas?.length ? ` · ${escapeHtml(item.focus_areas.join(", "))}` : ""}</small>
          </span>
          <span class="adaptive-pct">${escapeHtml(item.completion_percent ?? 0)}%</span>
        </button>`
        )
        .join("")}
    </div>
  `;
}

async function selectLearner(learnerId) {
  if (!learnerId) return;
  const response = await api("/api/academy/learners/select", {
    method: "POST",
    body: JSON.stringify({ learner_id: learnerId }),
  });
  selectedLearner = response.learner;
  learnerReport = await loadLearnerReport();
  if (selectedNodeKey) currentLessonProgress = await loadLessonProgress(selectedNodeKey);
  renderAcademyShell();
  renderLessonDetail();
  renderSimulationPanel();
  renderWaveformPanel();
}

function showLearnerError(error) {
  learnerPanel.className = "learner-panel empty-state compact";
  learnerPanel.textContent = error.message;
}

function renderAcademyModule(module) {
  return `
    <article class="academy-module">
      <div>
        <strong>${escapeHtml(module.title)}</strong>
        <span>${escapeHtml(module.lesson_count)} ${escapeHtml(t("acad.lessons_lc"))} · ${escapeHtml(module.simulation_count)} ${escapeHtml(t("acad.labs_lc"))}</span>
      </div>
      <div class="academy-lessons">
        ${(module.lessons || []).map(renderAcademyLesson).join("") || `<span class="empty-state">${escapeHtml(t("acad.no_lessons"))}</span>`}
      </div>
    </article>
  `;
}

function renderAcademyLesson(lesson) {
  const active = lesson.node_key === selectedNodeKey;
  return `
    <button class="academy-lesson${active ? " active" : ""}" type="button" data-node-key="${escapeHtml(lesson.node_key)}">
      <span>${escapeHtml(L(lesson, "title"))}</span>
      <small>${escapeHtml(lesson.simulation_ready ? t("acad.lab_tag") : nodeTypeLabel(lesson.node_type))}</small>
    </button>
  `;
}

function renderAcademySimulationCard(simulation) {
  const active = simulation.node_key === selectedNodeKey;
  return `
    <button class="academy-sim-card${active ? " active" : ""}" type="button" data-node-key="${escapeHtml(simulation.node_key)}">
      <strong>${escapeHtml(L(simulation, "title"))}</strong>
      <span>${escapeHtml(L(simulation, "summary") || simulation.domain)}</span>
      <small>${escapeHtml(simulation.channel_count)} ${escapeHtml(t("acad.channels_lc"))} · ${escapeHtml(simulation.pin_count)} ${escapeHtml(t("acad.pins_lc"))}</small>
    </button>
  `;
}

function bindAcademyNavigation() {
  document.querySelectorAll(".academy-lesson[data-node-key]").forEach((button) => {
    button.addEventListener("click", () => selectLesson(button.dataset.nodeKey));
  });
  document.querySelectorAll(".academy-sim-card[data-node-key]").forEach((button) => {
    button.addEventListener("click", () => selectLesson(button.dataset.nodeKey, { startSimulation: true }));
  });
}

async function selectLesson(nodeKey, options = {}) {
  selectedNodeKey = nodeKey;
  selectedPlanner = null;
  currentLesson = null;
  currentSimulation = null;
  currentWaveforms = null;
  currentBibliography = null;
  currentLessonProgress = { checklist: {} };
  simulationState = createEmptySimulationState();
  selectedWaveformScenarioKey = "baseline";
  waveformCoachResult = null;
  waveformParameters = null;
  waveformParametersDirty = false;
  waveformApplyNotice = null;
  waveformQuizAnswers = {};
  waveformQuizGrade = null;
  checkpointAnswers = {};
  checkpointGrade = null;
  caseScenarioAnswers = {};
  caseScenarioGrades = {};
  lessonDetail.className = "academy-panel empty-state compact";
  lessonDetail.textContent = t("acad.loading_lesson");
  simulationPanel.className = "academy-panel simulator-section empty-state compact";
  simulationPanel.textContent = t("acad.loading_lab");
  waveformPanel.className = "academy-panel waveform-section empty-state compact";
  waveformPanel.textContent = t("acad.loading_waveforms");

  selectedPlanner = await api(`/api/knowledge/planner?node_key=${encodeURIComponent(nodeKey)}`);
  currentLesson = await loadLesson(nodeKey);
  currentLessonProgress = await loadLessonProgress(nodeKey);
  checkpointGrade = currentLessonProgress.checkpoint_grade || null;
  waveformQuizGrade = currentLessonProgress.waveform_quiz_grade || null;
  caseScenarioGrades = currentLessonProgress.case_grades || {};
  waveformParameters = currentLessonProgress.waveform_parameters || null;
  currentSimulation = await loadSimulation(nodeKey);
  currentWaveforms = await loadWaveforms(nodeKey);
  currentBibliography = await loadBibliography(nodeKey);
  if (options.startSimulation && currentSimulation) startSimulation(false);

  window.history.replaceState(null, "", `/academy.html?node=${encodeURIComponent(nodeKey)}`);
  renderAcademyShell();
  renderLessonDetail();
  renderSimulationPanel();
  renderWaveformPanel();
  renderEvidencePanel();
  renderPins(selectedPlanner.pins || []);
  if (options.startSimulation && currentSimulation) setAcademyWorkspacePanel("simulation");
}

async function loadLesson(nodeKey) {
  return api(`/api/academy/lessons/${encodeURIComponent(nodeKey)}`);
}

async function loadSimulation(nodeKey) {
  try {
    return await api(`/api/academy/simulations/${encodeURIComponent(nodeKey)}`);
  } catch (error) {
    return null;
  }
}

async function loadWaveforms(nodeKey) {
  try {
    const params = new URLSearchParams();
    const controls = waveformParameters || {};
    Object.entries(controls).forEach(([key, value]) => {
      if (value !== undefined && value !== null) params.set(key, String(value));
    });
    const suffix = params.toString() ? `?${params.toString()}` : "";
    return await api(`/api/academy/waveforms/${encodeURIComponent(nodeKey)}${suffix}`);
  } catch (error) {
    return null;
  }
}

async function loadBibliography(nodeKey) {
  try {
    return await api(`/api/academy/bibliography/${encodeURIComponent(nodeKey)}?limit=8`);
  } catch (error) {
    return { status: "unavailable", items: [], error: error.message };
  }
}

async function loadLessonProgress(nodeKey) {
  if (!selectedLearner) return { checklist: {} };
  try {
    const result = await api(`/api/academy/progress/lessons/${encodeURIComponent(nodeKey)}`);
    return result.progress || { checklist: {} };
  } catch (error) {
    return { checklist: {}, progress_error: error.message };
  }
}

function renderLessonDetail() {
  const { node, roots, cranial_nerves: cranialNerves, clinical_plan: clinicalPlan } = selectedPlanner;
  const lesson = currentLesson || {};
  const readiness = clinicalPlan?.readiness || { state: "review", detail: t("acad.no_readiness") };
  lessonDetail.className = "academy-panel";
  lessonDetail.innerHTML = `
    <div class="node-heading">
      <div>
        <h2>${escapeHtml(L(node, "title"))}</h2>
        <p>${escapeHtml(L(node, "summary") || "")}</p>
      </div>
      <div class="node-type">${escapeHtml(nodeTypeLabel(node.node_type))}</div>
    </div>
    <div class="clinical-strip compact-strip">
      <div class="readiness-card ${escapeHtml(readiness.state)}">
        <span>${escapeHtml(tf("readiness", readiness.state, readiness.label || readiness.state))}</span>
        <strong>${escapeHtml(readiness.detail || "")}</strong>
      </div>
      ${renderMetric(t("acad.m_roots_cn"), [...roots, ...cranialNerves.map((item) => `CN ${item}`)].join(" · ") || t("acad.domain_fallback"), t("acad.linked_anatomy"))}
      ${renderMetric(t("acad.m_channels"), clinicalPlan?.coverage?.muscle_count ?? 0, `${clinicalPlan?.coverage?.mapped_muscle_count ?? 0} ${t("acad.mapped")}`)}
      ${renderMetric(t("acad.m_pins"), clinicalPlan?.coverage?.pin_count ?? 0, `${clinicalPlan?.coverage?.bilateral_muscle_count ?? 0} ${t("acad.bilateral")}`)}
    </div>
    ${renderProgressPanel()}
    ${renderLearningPath(lesson.learning_path)}
    ${renderObjectives(lesson.objectives || [])}
    ${renderCompetencies(lesson.competencies || [])}
    ${renderSpecialtyPanel(node)}
    ${renderLessonChecklist(lesson.checklist || [])}
    ${renderChannelPlan(clinicalPlan)}
    ${renderCheckpoints(lesson.checkpoints || [])}
    ${renderCaseScenarios(lesson.case_scenarios || [])}
  `;
  bindLessonInteractions();
}

function renderSpecialtyPanel(node) {
  const meta = node?.metadata || {};
  if (meta.ssep_montage) return renderSsepMontage(L(meta, "ssep_montage"));
  if (meta.screw_mapping) return renderScrewMapping(L(meta, "screw_mapping"));
  return "";
}

function renderSsepMontage(montage) {
  const stim = montage.stimulation || {};
  const channels = montage.channels || [];
  const scalp = montage.scalp_positions || [];
  return `
    <section class="clinical-section wide-section specialty-section ssep-montage">
      <div class="section-heading">
        <h3>${escapeHtml(t("acad.ssep_montage"))} · ${escapeHtml(montage.potential || "")}</h3>
        <span>${channels.length} ${escapeHtml(t("acad.recording_channels"))}</span>
      </div>
      ${montage.indication ? `<p class="specialty-indication">${escapeHtml(montage.indication)}</p>` : ""}
      <div class="specialty-grid">
        <div class="specialty-card">
          <h4>${escapeHtml(t("acad.stimulation"))}</h4>
          <p><strong>${escapeHtml(t("acad.site"))}:</strong> ${escapeHtml(stim.site || "—")}</p>
          <p><strong>${escapeHtml(t("acad.rate"))}:</strong> ${escapeHtml(stim.rate_hz || "—")} Hz</p>
          <p><strong>${escapeHtml(t("acad.pulse_width"))}:</strong> ${escapeHtml(stim.pulse_width_us || "—")} µs</p>
          <p><strong>${escapeHtml(t("acad.intensity"))}:</strong> ${escapeHtml(stim.intensity || "—")}</p>
          ${stim.polarity ? `<p><strong>${escapeHtml(t("acad.polarity"))}:</strong> ${escapeHtml(stim.polarity)}</p>` : ""}
        </div>
        <div class="specialty-card">
          <h4>${escapeHtml(t("acad.recording_setup"))}</h4>
          <p><strong>${escapeHtml(t("acad.scalp"))}:</strong> ${scalp.map((p) => `<span class="chip">${escapeHtml(p)}</span>`).join(" ") || "—"}</p>
          <p><strong>${escapeHtml(t("acad.ground"))}:</strong> ${escapeHtml(montage.ground || "—")}</p>
          <p><strong>${escapeHtml(t("acad.anesthesia"))}:</strong> ${escapeHtml(montage.anesthesia || "—")}</p>
        </div>
      </div>
      <div class="channel-montage-table">
        <div class="cmt-head">
          <span>${escapeHtml(t("acad.potential"))}</span><span>${escapeHtml(t("acad.montage"))}</span><span>${escapeHtml(t("acad.latency_ms"))}</span><span>${escapeHtml(t("acad.role"))}</span>
        </div>
        ${channels.map((ch) => `
          <div class="cmt-row">
            <span class="cmt-name">${escapeHtml(ch.name || "")}</span>
            <span>${escapeHtml(ch.montage || "")}</span>
            <span>${escapeHtml(ch.latency_ms || "")}</span>
            <span>${escapeHtml(ch.role || "")}</span>
          </div>
        `).join("")}
      </div>
      ${montage.warning ? `<div class="specialty-warning"><strong>${escapeHtml(t("acad.warning_criteria"))}:</strong> ${escapeHtml(montage.warning)}</div>` : ""}
    </section>
  `;
}

function renderScrewMapping(mapping) {
  const myotomes = mapping.recording_myotomes || [];
  return `
    <section class="clinical-section wide-section specialty-section screw-mapping">
      <div class="section-heading">
        <h3>${escapeHtml(t("acad.screw_mapping"))} · ${escapeHtml(mapping.level || "")}</h3>
        <span>${escapeHtml(mapping.region || "")}</span>
      </div>
      <div class="specialty-grid">
        <div class="specialty-card">
          <h4>${escapeHtml(t("acad.entry_point"))}</h4>
          <p>${escapeHtml(mapping.entry_point || "—")}</p>
        </div>
        <div class="specialty-card">
          <h4>${escapeHtml(t("acad.trajectory"))}</h4>
          <p>${escapeHtml(mapping.trajectory || "—")}</p>
        </div>
        <div class="specialty-card danger-card">
          <h4>${escapeHtml(t("acad.at_risk"))}</h4>
          <p>${escapeHtml(mapping.at_risk || "—")}</p>
        </div>
        <div class="specialty-card">
          <h4>${escapeHtml(t("acad.recording_muscles"))}</h4>
          <div class="mini-tags">
            ${myotomes.map((m) => `<span>${escapeHtml(m)}</span>`).join("") || "<span>—</span>"}
          </div>
        </div>
      </div>
      ${mapping.t_emg ? `<div class="specialty-warning"><strong>${escapeHtml(t("acad.temg_thresholds"))}:</strong> ${escapeHtml(mapping.t_emg)}</div>` : ""}
    </section>
  `;
}

function renderProgressPanel() {
  const progress = getLessonProgress();
  const checklistTotal = currentLesson?.checklist?.length || 0;
  const checklistDone = Object.values(progress.checklist || {}).filter(Boolean).length;
  const checkpointScore = progress.scores?.checkpoint ?? progress.checkpoint_score ?? checkpointGrade?.score_percent;
  const waveformScore = progress.scores?.waveform_quiz ?? waveformQuizGrade?.score_percent;
  const caseScore = progress.scores?.best_case;
  const percent = progress.completion_percent ?? lessonProgressPercent(progress, checklistDone, checklistTotal);
  return `
    <section class="academy-progress">
      <div>
        <span>${escapeHtml(t("acad.lesson_progress"))}</span>
        <strong>${escapeHtml(percent)}%</strong>
      </div>
      <div class="progress-bar"><i style="width:${escapeHtml(percent)}%"></i></div>
      <div class="progress-flags">
        <span>${escapeHtml(selectedLearner ? t("acad.mongo_saved") : t("acad.select_learner_short"))}</span>
        <span>${escapeHtml(progress.lesson_status || t("acad.not_started"))}</span>
        <span>${escapeHtml(checklistDone)}/${escapeHtml(checklistTotal)} ${escapeHtml(t("acad.checklist_lc"))}</span>
        <span>${progress.placement_passed ? escapeHtml(t("acad.placement_passed")) : escapeHtml(t("acad.placement_pending"))}</span>
        <span>${progress.waveform_reviewed ? escapeHtml(t("acad.waveform_reviewed")) : escapeHtml(t("acad.waveform_pending"))}</span>
        <span>${checkpointScore !== undefined ? `${escapeHtml(checkpointScore)}% ${escapeHtml(t("acad.checkpoint_suffix"))}` : escapeHtml(t("acad.checkpoint_pending"))}</span>
        <span>${waveformScore !== undefined && waveformScore !== null ? `${escapeHtml(waveformScore)}% ${escapeHtml(t("acad.waveform_suffix"))}` : escapeHtml(t("acad.waveform_quiz_pending"))}</span>
        <span>${caseScore !== undefined && caseScore !== null ? `${escapeHtml(caseScore)}% ${escapeHtml(t("acad.case_suffix"))}` : escapeHtml(t("acad.case_pending"))}</span>
      </div>
      ${(progress.recommended_review || []).length ? `<div class="progress-review">${progress.recommended_review.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>` : ""}
    </section>
  `;
}

function lessonProgressPercent(progress, checklistDone, checklistTotal) {
  const parts = [];
  if (checklistTotal) parts.push(checklistDone / checklistTotal);
  parts.push(progress.placement_passed ? 1 : 0);
  parts.push(progress.waveform_reviewed ? 1 : 0);
  if (progress.checkpoint_score !== undefined) parts.push(progress.checkpoint_score / 100);
  return Math.round((parts.reduce((sum, value) => sum + value, 0) / Math.max(parts.length, 1)) * 100);
}

function renderLearningPath(path) {
  if (!path) return "";
  return `
    <section class="academy-inner-section learning-path-section">
      <div class="section-heading"><h3>${escapeHtml(t("acad.learning_path"))}</h3><span>${escapeHtml(path.level)} · ${escapeHtml(path.estimated_minutes)} ${escapeHtml(t("acad.min"))}</span></div>
      <div class="learning-sequence">
        ${(path.sequence || []).map((item, index) => `<span><b>${index + 1}</b>${escapeHtml(item.replaceAll("_", " "))}</span>`).join("")}
      </div>
    </section>
  `;
}

function renderObjectives(objectives) {
  if (!objectives.length) return "";
  return `
    <section class="academy-inner-section">
      <div class="section-heading"><h3>${escapeHtml(t("acad.objectives"))}</h3><span>${objectives.length} ${escapeHtml(t("acad.goals"))}</span></div>
      <div class="objective-grid">
        ${objectives.map((objective) => `
          <article class="objective-card">
            <strong>${escapeHtml(objective.title)}</strong>
            <span>${escapeHtml(objective.detail)}</span>
          </article>
        `).join("")}
      </div>
    </section>
  `;
}

function renderCompetencies(competencies) {
  if (!competencies.length) return "";
  return `
    <section class="academy-inner-section">
      <div class="section-heading"><h3>${escapeHtml(t("acad.competencies"))}</h3><span>${competencies.length} ${escapeHtml(t("acad.tracked"))}</span></div>
      <div class="competency-grid">
        ${competencies.map((competency) => `
          <div class="competency-card ${escapeHtml(competency.state)}">
            <strong>${escapeHtml(competency.label)}</strong>
            <span>${escapeHtml(competency.state)} · ${escapeHtml(competency.evidence_count)} ${escapeHtml(t("acad.evidence"))}</span>
          </div>
        `).join("")}
      </div>
    </section>
  `;
}

function renderLessonChecklist(checklist) {
  if (!checklist.length) return "";
  const progress = getLessonProgress();
  return `
    <section class="academy-inner-section">
      <div class="section-heading"><h3>${escapeHtml(t("acad.checklist"))}</h3><span>${checklist.length} ${escapeHtml(t("acad.items"))}</span></div>
      <div class="lesson-checklist">
        ${checklist.map((item) => `
          <label class="checklist-item ${escapeHtml(item.status)}">
            <input type="checkbox" data-checklist-id="${escapeHtml(item.id)}" ${progress.checklist?.[item.id] ? "checked" : ""}>
            <span>${escapeHtml(item.label)}</span>
            <small>${escapeHtml(item.required ? t("acad.required") : item.status)}</small>
          </label>
        `).join("")}
      </div>
    </section>
  `;
}

function renderCheckpoints(checkpoints) {
  if (!checkpoints.length) return "";
  return `
    <section class="academy-inner-section checkpoint-section">
      <div class="section-heading"><h3>${escapeHtml(t("acad.checkpoints"))}</h3><span>${checkpoints.length} ${escapeHtml(t("acad.questions"))}</span></div>
      <div class="checkpoint-list">
        ${checkpoints.map(renderCheckpoint).join("")}
      </div>
      <button id="submit-checkpoints" class="secondary-btn" type="button">${escapeHtml(t("acad.grade_checkpoints"))}</button>
      ${renderCheckpointGrade()}
    </section>
  `;
}

function renderCheckpoint(checkpoint) {
  const selected = checkpointAnswers[checkpoint.id] || [];
  const inputType = checkpoint.type === "single_select" ? "radio" : checkpoint.type === "multi_select" ? "checkbox" : null;
  if (!inputType || !checkpoint.options?.length) {
    return `
      <article class="checkpoint-card">
        <strong>${escapeHtml(checkpoint.prompt || t("acad.checkpoint_fallback"))}</strong>
        <div class="empty-state compact">${escapeHtml(t("acad.checkpoint_not_ready"))}</div>
      </article>
    `;
  }
  return `
    <article class="checkpoint-card">
      <strong>${escapeHtml(checkpoint.prompt)}</strong>
      <div class="checkpoint-options">
        ${(checkpoint.options || []).map((option) => `
          <label>
            <input type="${inputType}" name="checkpoint-${escapeHtml(checkpoint.id)}" data-checkpoint-id="${escapeHtml(checkpoint.id)}" value="${escapeHtml(option.value)}" ${selected.includes(option.value) ? "checked" : ""}>
            <span>${escapeHtml(option.label)}</span>
          </label>
        `).join("")}
      </div>
    </article>
  `;
}

function renderCheckpointGrade() {
  const grade = checkpointGrade || getLessonProgress().checkpoint_grade;
  if (!grade) return "";
  return `
    <div class="checkpoint-grade ${grade.passed ? "passed" : "review"}">
      <strong>${escapeHtml(grade.score_percent)}%</strong>
      <span>${escapeHtml(grade.passed ? t("acad.passed") : t("acad.needs_review"))}</span>
      <div>
        ${(grade.recommendations || []).map((item) => `<small>${escapeHtml(item)}</small>`).join("")}
      </div>
    </div>
  `;
}

function renderCaseScenarios(scenarios) {
  if (!scenarios.length) return "";
  return `
    <section class="academy-inner-section case-scenario-section">
      <div class="section-heading"><h3>${escapeHtml(t("acad.case_scenarios"))}</h3><span>${scenarios.length} ${escapeHtml(t("acad.drills"))}</span></div>
      <div class="case-scenario-grid">
        ${scenarios.map((scenario) => `
          <article class="case-scenario-card">
            <strong>${escapeHtml(scenario.title)}</strong>
            <span>${escapeHtml(scenario.context)}</span>
            <em>${escapeHtml(scenario.rubric || t("acad.rubric_fallback"))}</em>
            <div class="case-action-options">
              ${(scenario.action_options || []).map((option) => `
                <label>
                  <input type="checkbox" data-case-id="${escapeHtml(scenario.id)}" value="${escapeHtml(option.value)}" ${(caseScenarioAnswers[scenario.id] || []).includes(option.value) ? "checked" : ""}>
                  <span>${escapeHtml(option.label)}</span>
                </label>
              `).join("")}
            </div>
            <button class="secondary-btn case-grade-btn" type="button" data-case-id="${escapeHtml(scenario.id)}">${escapeHtml(t("acad.grade_case"))}</button>
            ${caseScenarioGrades[scenario.id] ? renderGenericGrade(caseScenarioGrades[scenario.id], t("acad.case_suffix")) : ""}
          </article>
        `).join("")}
      </div>
    </section>
  `;
}

function bindLessonInteractions() {
  lessonDetail.querySelectorAll("input[data-checklist-id]").forEach((input) => {
    input.addEventListener("change", () => {
      saveLessonProgress({ checklist: { [input.dataset.checklistId]: input.checked } }).catch((error) => {
        currentLessonProgress.progress_error = error.message;
      });
      currentLessonProgress.checklist = { ...(currentLessonProgress.checklist || {}), [input.dataset.checklistId]: input.checked };
      renderLessonDetail();
      renderLearnerPanel();
    });
  });
  lessonDetail.querySelectorAll("input[data-checkpoint-id]").forEach((input) => {
    input.addEventListener("change", () => updateCheckpointAnswer(input));
  });
  lessonDetail.querySelectorAll("input[data-case-id]").forEach((input) => {
    input.addEventListener("change", () => updateCaseScenarioAnswer(input));
  });
  lessonDetail.querySelector("#submit-checkpoints")?.addEventListener("click", () => submitCheckpoints().catch((error) => {
    checkpointGrade = { score_percent: 0, passed: false, recommendations: [error.message] };
    renderLessonDetail();
  }));
  lessonDetail.querySelectorAll(".case-grade-btn").forEach((button) => {
    button.addEventListener("click", () => submitCaseScenario(button.dataset.caseId).catch((error) => {
      caseScenarioGrades[button.dataset.caseId] = { score_percent: 0, passed: false, recommendations: [error.message] };
      renderLessonDetail();
    }));
  });
}

function updateCheckpointAnswer(input) {
  const checkpointId = input.dataset.checkpointId;
  if (input.type === "radio") {
    checkpointAnswers[checkpointId] = input.checked ? [input.value] : [];
    return;
  }
  const values = new Set(checkpointAnswers[checkpointId] || []);
  if (input.checked) values.add(input.value);
  else values.delete(input.value);
  checkpointAnswers[checkpointId] = Array.from(values);
}

function updateCaseScenarioAnswer(input) {
  const scenarioId = input.dataset.caseId;
  const values = new Set(caseScenarioAnswers[scenarioId] || []);
  if (input.checked) values.add(input.value);
  else values.delete(input.value);
  caseScenarioAnswers[scenarioId] = Array.from(values);
}

async function submitCaseScenario(scenarioId) {
  const selectedActions = caseScenarioAnswers[scenarioId] || [];
  if (!selectedActions.length) throw new Error(t("acad.select_one_action"));
  const result = await api("/api/academy/cases/grade", {
    method: "POST",
    body: JSON.stringify({ node_key: selectedNodeKey, scenario_id: scenarioId, selected_actions: selectedActions }),
  });
  caseScenarioGrades[scenarioId] = result.grade;
  currentLessonProgress = result.progress || await loadLessonProgress(selectedNodeKey);
  learnerReport = await loadLearnerReport();
  renderLessonDetail();
  renderLearnerPanel();
}

async function submitCheckpoints() {
  const unanswered = (currentLesson?.checkpoints || []).filter((checkpoint) => !(checkpointAnswers[checkpoint.id] || []).length);
  if (unanswered.length) {
    throw new Error(t("acad.answer_all_checkpoints", { n: unanswered.length }));
  }
  const payload = {
    node_key: selectedNodeKey,
    answers: Object.entries(checkpointAnswers).map(([checkpoint_id, selected_values]) => ({ checkpoint_id, selected_values })),
  };
  const result = await api("/api/academy/checkpoints/grade", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  checkpointGrade = result.grade;
  currentLessonProgress = result.progress || await loadLessonProgress(selectedNodeKey);
  learnerReport = await loadLearnerReport();
  renderLessonDetail();
  renderLearnerPanel();
}

function getLessonProgress() {
  return currentLessonProgress || { checklist: {} };
}

async function saveLessonProgress(patch) {
  if (!selectedNodeKey || !selectedLearner) return;
  const result = await api(`/api/academy/progress/lessons/${encodeURIComponent(selectedNodeKey)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  currentLessonProgress = result.progress || currentLessonProgress;
  learnerReport = await loadLearnerReport();
}

function renderChannelPlan(plan) {
  const channels = plan?.channel_plan || [];
  return `
    <section class="clinical-section wide-section academy-inner-section">
      <div class="section-heading">
        <h3>${escapeHtml(t("acad.channel_plan"))}</h3>
        <span>${channels.length} ${escapeHtml(t("acad.muscles_electrodes"))}</span>
      </div>
      <div class="channel-table">
        ${channels.map(renderChannel).join("") || `<div class="empty-state compact">${escapeHtml(t("acad.no_linked_muscles"))}</div>`}
      </div>
    </section>
  `;
}

function renderChannel(channel) {
  const signals = [...(channel.roots || []), ...(channel.cranial_nerves || []).map((item) => `CN ${item}`)];
  return `
    <div class="channel-row ${escapeHtml(channel.coordinate_status)}">
      <div class="channel-main">
        <strong>${escapeHtml(L(channel, "name") || channel.lat_name)}</strong>
        <span>${escapeHtml(channel.lat_name)}</span>
      </div>
      <div class="channel-detail">
        <span>${escapeHtml(L(channel, "placement") || t("acad.no_placement_kb"))}</span>
        <small>${escapeHtml(L(channel, "action") || "")}</small>
      </div>
      <div class="channel-tags">
        ${signals.map((signal) => `<span>${escapeHtml(signal)}</span>`).join("") || `<span>${escapeHtml(channel.coordinate_status)}</span>`}
      </div>
      <div class="coord-status">${escapeHtml(tf("coord", channel.coordinate_status, channel.coordinate_status))}</div>
    </div>
  `;
}

function renderSimulationPanel() {
  if (!currentSimulation) {
    simulationPanel.className = "academy-panel simulator-section empty-state compact";
    simulationPanel.textContent = t("acad.no_placement_lab");
    return;
  }

  simulationPanel.className = "academy-panel simulator-section";
  const targets = currentSimulation.targets || [];
  const selectedTarget = targets.find((target) => target.target_id === simulationState.selectedTargetId) || targets[0];
  simulationPanel.innerHTML = `
    <div class="section-heading">
      <h3>${escapeHtml(t("acad.placement_lab"))}</h3>
      <span>${targets.length} ${escapeHtml(t("acad.targets"))}</span>
    </div>
    <div class="simulator-toolbar">
      <button class="secondary-btn sim-start" type="button">${simulationState.active ? escapeHtml(t("acad.restart")) : escapeHtml(t("acad.start"))}</button>
      <button class="secondary-btn sim-reset" type="button" ${simulationState.active ? "" : "disabled"}>${escapeHtml(t("acad.reset"))}</button>
      <button class="secondary-btn sim-coach" type="button" ${simulationState.validation ? "" : "disabled"}>${escapeHtml(t("acad.ai_coach"))}</button>
    </div>
    <div class="simulator-grid">
      <div class="target-list">
        ${targets.map((target) => renderSimulationTarget(target, selectedTarget)).join("")}
      </div>
      <div class="simulator-feedback">
        ${renderSimulationStatus(selectedTarget)}
        ${renderSignalFeedback(simulationState.validation)}
        ${renderSimulationCoach(simulationState.coachResult)}
      </div>
    </div>
  `;
  bindSimulationControls();
}

function renderWaveformPanel() {
  if (!currentWaveforms?.scenarios?.length) {
    waveformPanel.className = "academy-panel waveform-section empty-state compact";
    waveformPanel.textContent = t("acad.no_waveforms");
    return;
  }
  const scenario = currentWaveforms.scenarios.find((item) => item.scenario_key === selectedWaveformScenarioKey) || currentWaveforms.scenarios[0];
  selectedWaveformScenarioKey = scenario.scenario_key;
  const baselineScenario = currentWaveforms.scenarios.find((item) => item.scenario_key === "baseline");
  const baselineByLabel = new Map((baselineScenario?.channels || []).map((channel) => [channel.label, channel]));
  const showBaseline = scenario.scenario_key !== "baseline";
  waveformPanel.className = "academy-panel waveform-section";
  waveformPanel.innerHTML = `
    <div class="section-heading">
      <h3>${escapeHtml(t("acad.waveform_lab"))}</h3>
      <span>${escapeHtml(currentWaveforms.modality.label)} · ${escapeHtml(scenario.status)}</span>
    </div>
    ${renderWaveformDevice(currentWaveforms.modality, scenario)}
    <div class="waveform-notice">${escapeHtml(currentWaveforms.model.notice)}</div>
    ${renderWaveformControls()}
    <div class="waveform-toolbar" role="toolbar" aria-label="${escapeHtml(t("acad.waveform_lab"))}">
      ${currentWaveforms.scenarios.map(renderWaveformScenarioButton).join("")}
      <button class="secondary-btn waveform-coach" type="button">${escapeHtml(t("acad.ai_waveform_coach"))}</button>
    </div>
    ${showBaseline ? `<div class="waveform-legend"><span class="wf-legend-current">${escapeHtml(t("acad.wf_current"))}</span><span class="wf-legend-baseline">${escapeHtml(t("acad.wf_baseline"))}</span></div>` : ""}
    <div class="waveform-summary ${escapeHtml(scenario.status)}">
      <strong>${escapeHtml(scenario.title)}</strong>
      <span>${escapeHtml(scenario.summary)}</span>
    </div>
    <div class="waveform-grid">
      ${scenario.channels.map((channel) => renderWaveformChannel(channel, currentWaveforms.modality, showBaseline ? baselineByLabel.get(channel.label) : null)).join("")}
    </div>
    <div class="waveform-tasks">
      <strong>${escapeHtml(t("acad.interpretation_tasks"))}</strong>
      ${scenario.interpretation_tasks.map((task) => `<span>${escapeHtml(task)}</span>`).join("")}
    </div>
    ${renderWaveformQuiz(currentWaveforms.quiz || [])}
    ${waveformCoachResult ? `<div class="waveform-coach-output">${renderAiResult(waveformCoachResult)}</div>` : ""}
  `;
  bindWaveformControls();
}

function renderWaveformControls() {
  const controls = currentWaveforms.controls || [];
  const parameters = { ...(currentWaveforms.parameters || {}), ...(waveformParameters || {}) };
  if (!controls.length) return "";
  return `
    <div class="waveform-control-grid">
      ${controls.map((control) => `
        <label>
          <span>${escapeHtml(control.label)} <b>${escapeHtml(parameters[control.key] ?? "")}${escapeHtml(control.unit || "")}</b></span>
          <input type="range" min="${escapeHtml(control.min)}" max="${escapeHtml(control.max)}" step="${escapeHtml(control.step)}" value="${escapeHtml(parameters[control.key] ?? control.min)}" data-waveform-param="${escapeHtml(control.key)}">
        </label>
      `).join("")}
      <button class="secondary-btn waveform-apply" type="button">${escapeHtml(waveformParametersDirty ? t("acad.apply_changes") : t("acad.apply_parameters"))}</button>
      <div class="waveform-apply-state ${escapeHtml(waveformApplyNotice?.state || "idle")}">${escapeHtml(waveformApplyNotice?.message || t("acad.params_loaded"))}</div>
    </div>
  `;
}

function renderWaveformQuiz(questions) {
  if (!questions.length) return "";
  const grade = waveformQuizGrade || getLessonProgress().waveform_quiz_grade;
  return `
    <div class="waveform-quiz">
      <div class="section-heading compact-heading"><h3>${escapeHtml(t("acad.waveform_quiz"))}</h3><span>${questions.length} ${escapeHtml(t("acad.questions"))}</span></div>
      ${questions.map(renderWaveformQuizQuestion).join("")}
      <button class="secondary-btn waveform-quiz-submit" type="button">${escapeHtml(t("acad.grade_waveform_quiz"))}</button>
      ${grade ? renderGenericGrade(grade, t("acad.waveform_suffix")) : ""}
    </div>
  `;
}

function renderWaveformQuizQuestion(question) {
  const selected = waveformQuizAnswers[question.id] || [];
  const inputType = question.type === "single_select" ? "radio" : "checkbox";
  return `
    <article class="checkpoint-card waveform-quiz-card">
      <strong>${escapeHtml(question.prompt)}</strong>
      <div class="checkpoint-options">
        ${(question.options || []).map((option) => `
          <label>
            <input type="${inputType}" name="waveform-${escapeHtml(question.id)}" data-waveform-question-id="${escapeHtml(question.id)}" value="${escapeHtml(option.value)}" ${selected.includes(option.value) ? "checked" : ""}>
            <span>${escapeHtml(option.label)}</span>
          </label>
        `).join("")}
      </div>
    </article>
  `;
}

function renderGenericGrade(grade, label) {
  return `
    <div class="checkpoint-grade ${grade.passed ? "passed" : "review"}">
      <strong>${escapeHtml(grade.score_percent)}%</strong>
      <span>${escapeHtml(grade.passed ? `${label} ${t("acad.passed")}` : `${label} ${t("acad.needs_review")}`)}</span>
      <div>${(grade.recommendations || []).map((item) => `<small>${escapeHtml(item)}</small>`).join("")}</div>
    </div>
  `;
}

function renderWaveformScenarioButton(scenario) {
  const active = scenario.scenario_key === selectedWaveformScenarioKey;
  return `
    <button class="waveform-scenario${active ? " active" : ""}" type="button" data-scenario-key="${escapeHtml(scenario.scenario_key)}" aria-pressed="${active ? "true" : "false"}">
      ${escapeHtml(scenario.title)}
    </button>
  `;
}

function renderWaveformDevice(modality, scenario) {
  const device = modality.device || {};
  const acquisition = scenario?.acquisition || {};
  const chips = [
    [t("acad.dev_sweep"), `${escapeHtml(modality.time_max)} ${escapeHtml(modality.time_unit)}`],
    [t("acad.dev_timebase"), device.time_base_ms_per_div !== undefined ? `${escapeHtml(device.time_base_ms_per_div)} ms/div` : "—"],
    [t("acad.dev_sensitivity"), device.sensitivity_uv_per_div !== undefined ? `${escapeHtml(device.sensitivity_uv_per_div)} µV/div` : "—"],
    [t("acad.dev_averages"), String(acquisition.averages ?? device.averages ?? "—")],
    [t("acad.dev_filters"), device.lff_hz !== undefined ? `${escapeHtml(device.lff_hz)}–${escapeHtml(device.hff_hz)} Hz` : "—"],
    [t("acad.dev_notch"), device.notch_hz ? `${escapeHtml(device.notch_hz)} Hz` : t("acad.dev_off")],
  ];
  const line = acquisition.line_interference ? `<span class="wf-line-${escapeHtml(acquisition.line_interference)}"><b>${escapeHtml(t("acad.dev_line"))}</b>${escapeHtml(acquisition.line_interference)}</span>` : "";
  return `
    <div class="waveform-device">
      ${chips.map(([key, value]) => `<span><b>${escapeHtml(key)}</b>${value}</span>`).join("")}
      ${line}
    </div>
  `;
}

function renderWaveformChannel(channel, modality, baselineChannel) {
  const metrics = channel.metrics || {};
  const components = metrics.components || [];
  const baselineMetrics = baselineChannel?.metrics || null;
  const subtitleParts = [channel.montage || channel.lat_name, ...(channel.roots || []), ...(channel.cranial_nerves || []).map((item) => `CN ${item}`)];
  const delta = renderWaveformDelta(metrics, baselineMetrics, modality);
  return `
    <article class="waveform-card ${escapeHtml(channel.status)}">
      <div class="waveform-card-head">
        <div>
          <strong>${escapeHtml(channel.label)}</strong>
          <span>${escapeHtml(subtitleParts.filter(Boolean).join(" · ") || modality.label)}</span>
        </div>
        <small>${escapeHtml(channel.status)}</small>
      </div>
      ${renderWaveformSvg(channel.samples || [], modality, metrics, baselineChannel?.samples || [], channel)}
      ${delta}
      <div class="waveform-metrics">
        ${renderMetricChip(t("acad.mc_amp"), metrics.amplitude_uv, modality.value_unit)}
        ${renderMetricChip(t("acad.mc_latency"), metrics.latency_ms, modality.time_unit)}
        ${renderMetricChip(t("acad.mc_noise"), metrics.noise_uv, modality.value_unit)}
        ${metrics.threshold_ma !== undefined ? renderMetricChip(t("acad.mc_threshold"), metrics.threshold_ma, "mA") : ""}
        ${renderMetricChip(t("acad.mc_snr"), metrics.signal_to_noise, "")}
        ${metrics.reproducibility_percent !== undefined ? renderMetricChip(t("acad.mc_repro"), metrics.reproducibility_percent, "%") : ""}
      </div>
      ${components.length ? `<div class="waveform-components">${components.map((component) => `<span><b>${escapeHtml(component.label)}</b>${escapeHtml(component.latency_ms)} ${escapeHtml(modality.time_unit)}</span>`).join("")}</div>` : ""}
      <div class="waveform-points">
        ${(channel.teaching_points || []).map((point) => `<span>${escapeHtml(point)}</span>`).join("")}
      </div>
    </article>
  `;
}

function renderWaveformDelta(metrics, baselineMetrics, modality) {
  if (!baselineMetrics) return "";
  const baseAmp = Number(baselineMetrics.amplitude_uv);
  const curAmp = Number(metrics.amplitude_uv);
  const baseLat = Number(baselineMetrics.latency_ms);
  const curLat = Number(metrics.latency_ms);
  const parts = [];
  if (Number.isFinite(baseAmp) && baseAmp > 0 && Number.isFinite(curAmp)) {
    const dropPercent = Math.round(((baseAmp - curAmp) / baseAmp) * 100);
    const cls = dropPercent >= 50 ? "alarm" : dropPercent >= 20 ? "warn" : "ok";
    const sign = dropPercent >= 0 ? "−" : "+";
    parts.push(`<span class="wf-delta-${cls}"><b>${escapeHtml(t("acad.wf_amp_delta"))}</b>${sign}${Math.abs(dropPercent)}%</span>`);
  }
  if (Number.isFinite(baseLat) && Number.isFinite(curLat)) {
    const shift = curLat - baseLat;
    const cls = Math.abs(shift) >= 1.5 ? "warn" : "ok";
    const sign = shift >= 0 ? "+" : "−";
    parts.push(`<span class="wf-delta-${cls}"><b>${escapeHtml(t("acad.wf_lat_delta"))}</b>${sign}${Math.abs(shift).toFixed(1)} ${escapeHtml(modality.time_unit)}</span>`);
  }
  if (!parts.length) return "";
  return `<div class="waveform-delta" aria-label="${escapeHtml(t("acad.wf_vs_baseline"))}">${parts.join("")}</div>`;
}

function renderWaveformSvg(samples, modality, metrics, baselineSamples, channel) {
  if (!samples.length) return `<div class="empty-state compact">${escapeHtml(t("acad.no_samples"))}</div>`;
  const width = 440;
  const height = 150;
  const paddingX = 30;
  const paddingY = 16;
  const minT = Number(modality.time_min);
  const maxT = Number(modality.time_max);
  const grid = modality.grid || { x_divisions: 10, y_divisions: 8 };
  const xDiv = Math.max(1, Number(grid.x_divisions) || 10);
  const yDiv = Math.max(1, Number(grid.y_divisions) || 8);
  const plotW = width - paddingX * 2;
  const plotH = height - paddingY * 2;
  const baseline = Array.isArray(baselineSamples) ? baselineSamples : [];
  const allValues = [...samples, ...baseline].map((sample) => Math.abs(Number(sample.value) || 0));
  const maxAbs = Math.max(...allValues, 1);
  const xFor = (t) => paddingX + ((t - minT) / (maxT - minT)) * plotW;
  const yFor = (value) => height / 2 - (value / maxAbs) * (plotH / 2);
  const points = samples.map((sample) => `${formatSvgNumber(xFor(Number(sample.t)))},${formatSvgNumber(yFor(Number(sample.value)))}`).join(" ");
  const baselinePoints = baseline.length
    ? baseline.map((sample) => `${formatSvgNumber(xFor(Number(sample.t)))},${formatSvgNumber(yFor(Number(sample.value)))}`).join(" ")
    : "";
  let gridLines = "";
  for (let i = 0; i <= xDiv; i += 1) {
    const x = paddingX + (plotW * i) / xDiv;
    gridLines += `<line class="wf-grid" x1="${formatSvgNumber(x)}" y1="${paddingY}" x2="${formatSvgNumber(x)}" y2="${height - paddingY}"></line>`;
  }
  for (let j = 0; j <= yDiv; j += 1) {
    const y = paddingY + (plotH * j) / yDiv;
    gridLines += `<line class="wf-grid" x1="${paddingX}" y1="${formatSvgNumber(y)}" x2="${width - paddingX}" y2="${formatSvgNumber(y)}"></line>`;
  }
  // Amplitude (µV) axis calibration on the left edge.
  const ampUnit = modality.value_unit || "µV";
  const ampLabels = `
    <text class="wf-axis-label" x="2" y="${paddingY + 4}">+${formatSvgNumber(maxAbs)} ${escapeHtml(ampUnit)}</text>
    <text class="wf-axis-label" x="2" y="${height - paddingY}">−${formatSvgNumber(maxAbs)} ${escapeHtml(ampUnit)}</text>
  `;
  const components = (metrics && metrics.components) || [];
  const markers = components
    .filter((component) => Number(component.latency_ms) >= minT && Number(component.latency_ms) <= maxT)
    .map((component) => {
      const t = Number(component.latency_ms);
      let nearest = samples[0];
      let best = Infinity;
      for (const sample of samples) {
        const distance = Math.abs(Number(sample.t) - t);
        if (distance < best) {
          best = distance;
          nearest = sample;
        }
      }
      const x = xFor(t);
      const y = yFor(Number(nearest.value));
      return `<g class="wf-marker"><circle cx="${formatSvgNumber(x)}" cy="${formatSvgNumber(y)}" r="2.6"></circle><text x="${formatSvgNumber(x)}" y="${formatSvgNumber(Math.max(10, y - 6))}">${escapeHtml(component.label)}</text></g>`;
    })
    .join("");
  const ariaLabel = [
    channel?.label || modality.label,
    metrics?.amplitude_uv !== undefined ? `${t("acad.mc_amp")} ${metrics.amplitude_uv} ${ampUnit}` : "",
    metrics?.latency_ms !== undefined ? `${t("acad.mc_latency")} ${metrics.latency_ms} ${modality.time_unit}` : "",
  ].filter(Boolean).join(", ");
  return `
    <svg class="waveform-svg" viewBox="0 0 ${width} ${height}" role="img" tabindex="0" aria-label="${escapeHtml(ariaLabel)}">
      <title>${escapeHtml(ariaLabel)}</title>
      ${gridLines}
      <line class="waveform-axis" x1="${paddingX}" y1="${height / 2}" x2="${width - paddingX}" y2="${height / 2}"></line>
      ${baselinePoints ? `<polyline class="waveform-baseline-line" points="${baselinePoints}"></polyline>` : ""}
      <polyline class="waveform-line" points="${points}"></polyline>
      ${markers}
      ${ampLabels}
      <text class="wf-axis-label" x="${paddingX}" y="${height - 4}">${escapeHtml(minT)} ${escapeHtml(modality.time_unit)}</text>
      <text class="wf-axis-label" x="${width - paddingX - 52}" y="${height - 4}">${escapeHtml(maxT)} ${escapeHtml(modality.time_unit)}</text>
    </svg>
  `;
}

function renderMetricChip(label, value, unit) {
  return `<span><b>${escapeHtml(label)}</b>${escapeHtml(value ?? "-")}${unit ? ` ${escapeHtml(unit)}` : ""}</span>`;
}

function bindWaveformControls() {
  waveformPanel.querySelectorAll("input[data-waveform-param]").forEach((input) => {
    input.addEventListener("input", () => {
      // Accumulate every slider edit onto the working set (loaded parameters +
      // any in-progress edits) so adjusting several sliders before "Apply" keeps
      // all of them, instead of each change reverting the others to baseline.
      waveformParameters = {
        ...(currentWaveforms.parameters || {}),
        ...(waveformParameters || {}),
        [input.dataset.waveformParam]: Number(input.value),
      };
      waveformParametersDirty = true;
      waveformApplyNotice = { state: "pending", message: t("acad.params_ready") };
      input.closest("label")?.querySelector("b")?.replaceChildren(document.createTextNode(`${input.value}${parameterUnit(input.dataset.waveformParam)}`));
      waveformPanel.querySelector(".waveform-apply")?.replaceChildren(document.createTextNode(t("acad.apply_changes")));
      const state = waveformPanel.querySelector(".waveform-apply-state");
      if (state) {
        state.className = "waveform-apply-state pending";
        state.textContent = waveformApplyNotice.message;
      }
    });
  });
  waveformPanel.querySelector(".waveform-apply")?.addEventListener("click", () => applyWaveformParameters().catch(showWaveformError));
  waveformPanel.querySelectorAll(".waveform-scenario").forEach((button) => {
    button.addEventListener("click", () => {
      selectedWaveformScenarioKey = button.dataset.scenarioKey;
      waveformCoachResult = null;
      saveLessonProgress({ waveform_reviewed: true, waveform_scenario: selectedWaveformScenarioKey, waveform_parameters: currentWaveforms.parameters }).then(() => {
        renderLearnerPanel();
      }).catch(showWaveformError);
      renderWaveformPanel();
      renderLessonDetail();
    });
  });
  waveformPanel.querySelectorAll("input[data-waveform-question-id]").forEach((input) => {
    input.addEventListener("change", () => updateWaveformQuizAnswer(input));
  });
  waveformPanel.querySelector(".waveform-quiz-submit")?.addEventListener("click", () => submitWaveformQuiz().catch(showWaveformError));
  waveformPanel.querySelector(".waveform-coach")?.addEventListener("click", () => askWaveformCoach().catch(showWaveformError));
}

function parameterUnit(key) {
  return (currentWaveforms?.controls || []).find((control) => control.key === key)?.unit || "";
}

async function applyWaveformParameters() {
  const parameters = waveformParameters || currentWaveforms?.parameters || {};
  const requestedScenario = nextWaveformScenarioAfterApply(selectedWaveformScenarioKey, parameters);
  waveformApplyNotice = { state: "pending", message: t("acad.applying_params") };
  selectedWaveformScenarioKey = requestedScenario;
  currentWaveforms = await loadWaveforms(selectedNodeKey);
  waveformParameters = currentWaveforms.parameters;
  waveformParametersDirty = false;
  const scenario = currentWaveforms.scenarios.find((item) => item.scenario_key === selectedWaveformScenarioKey) || currentWaveforms.scenarios[0];
  selectedWaveformScenarioKey = scenario.scenario_key;
  await saveLessonProgress({ waveform_reviewed: true, waveform_scenario: selectedWaveformScenarioKey, waveform_parameters: currentWaveforms.parameters });
  waveformApplyNotice = {
    state: "applied",
    message: selectedLearner ? t("acad.applied_saved", { title: scenario.title }) : t("acad.applied_local", { title: scenario.title }),
  };
  renderWaveformPanel();
  renderLessonDetail();
  renderLearnerPanel();
}

function nextWaveformScenarioAfterApply(currentScenarioKey, parameters) {
  // The parameter sliders (amplitude drop / latency shift / threshold / noise)
  // define a custom abnormality, so "Apply" always routes to the scenario those
  // parameters actually drive — regardless of which preset is currently shown.
  // This prevents stranding the user on a fixed canned preset (stimulation
  // failure / anesthetic suppression) whose trace ignores the live parameters.
  if (!waveformParametersDirty) return currentScenarioKey;
  const amplitudeDrop = Number(parameters.amplitude_drop_percent ?? currentWaveforms?.parameters?.amplitude_drop_percent ?? 0);
  const latencyShift = Number(parameters.latency_shift_ms ?? currentWaveforms?.parameters?.latency_shift_ms ?? 0);
  const threshold = Number(parameters.threshold_ma ?? currentWaveforms?.parameters?.threshold_ma ?? 0);
  const noise = Number(parameters.noise_scale ?? currentWaveforms?.parameters?.noise_scale ?? 0);
  if (noise >= 1.4) return "artifact";
  if (threshold >= 14) return "threshold_change";
  if (amplitudeDrop >= 35 && latencyShift >= 3) return "combined_warning";
  if (latencyShift >= 3) return "latency_shift";
  if (amplitudeDrop >= 25) return "amplitude_drop";
  return "baseline";
}

function updateWaveformQuizAnswer(input) {
  const questionId = input.dataset.waveformQuestionId;
  if (input.type === "radio") {
    waveformQuizAnswers[questionId] = input.checked ? [input.value] : [];
    return;
  }
  const values = new Set(waveformQuizAnswers[questionId] || []);
  if (input.checked) values.add(input.value);
  else values.delete(input.value);
  waveformQuizAnswers[questionId] = Array.from(values);
}

async function submitWaveformQuiz() {
  const unanswered = (currentWaveforms?.quiz || []).filter((question) => !(waveformQuizAnswers[question.id] || []).length);
  if (unanswered.length) throw new Error(t("acad.answer_all_waveform", { n: unanswered.length }));
  const result = await api("/api/academy/waveforms/quiz/grade", {
    method: "POST",
    body: JSON.stringify({
      node_key: selectedNodeKey,
      parameters: currentWaveforms.parameters,
      answers: Object.entries(waveformQuizAnswers).map(([checkpoint_id, selected_values]) => ({ checkpoint_id, selected_values })),
    }),
  });
  waveformQuizGrade = result.grade;
  currentLessonProgress = result.progress || await loadLessonProgress(selectedNodeKey);
  learnerReport = await loadLearnerReport();
  renderWaveformPanel();
  renderLessonDetail();
  renderLearnerPanel();
}

async function askWaveformCoach() {
  const scenario = currentWaveforms.scenarios.find((item) => item.scenario_key === selectedWaveformScenarioKey) || currentWaveforms.scenarios[0];
  const channel = scenario.channels[0];
  const question = t("acad.coach_waveform_q", {
    node: L(currentWaveforms.node, "title"),
    modality: currentWaveforms.modality.label,
    scenario: scenario.title,
    amp: channel.metrics.amplitude_uv,
    ampUnit: currentWaveforms.modality.value_unit,
    lat: channel.metrics.latency_ms,
    noise: channel.metrics.noise_uv,
    noiseUnit: currentWaveforms.modality.value_unit,
    snr: channel.metrics.signal_to_noise,
  });
  waveformCoachResult = await api("/api/academy/tutor/ask", {
    method: "POST",
    body: JSON.stringify({ question, node_key: selectedNodeKey, waveform_scenario: selectedWaveformScenarioKey, retrieval_top_k: 8 }),
  });
  renderWaveformPanel();
}

function showWaveformError(error) {
  waveformPanel.insertAdjacentHTML("beforeend", `<div class="sim-status error"><strong>error</strong><span>${escapeHtml(error.message)}</span></div>`);
}

function renderSimulationTarget(target, selectedTarget) {
  const result = simulationState.validation?.results?.find((item) => item.target_id === target.target_id);
  const placed = simulationState.placements.some((item) => item.target_id === target.target_id);
  const state = result?.status || (placed ? "placed" : "pending");
  const selected = selectedTarget?.target_id === target.target_id;
  const signals = [...(target.roots || []), ...(target.cranial_nerves || []).map((item) => `CN ${item}`)];
  return `
    <button class="sim-target ${state}${selected ? " active" : ""}" type="button" data-target-id="${escapeHtml(target.target_id)}">
      <strong>${escapeHtml(L(target, "name") || target.lat_name)}</strong>
      <span>${escapeHtml([target.lat_name, tf("side", target.side, target.side), target.view].filter(Boolean).join(" · "))}</span>
      <small>${escapeHtml(signals.join(" · ") || L(target, "placement") || t("acad.training_target"))}</small>
    </button>
  `;
}

function renderSimulationStatus(selectedTarget) {
  if (!simulationState.active) {
    return `<div class="sim-status empty-state compact">${escapeHtml(t("acad.lab_inactive"))}</div>`;
  }
  const summary = simulationState.validation?.summary;
  return `
    <div class="sim-status ${summary?.status || "active"}">
      <strong>${escapeHtml(summary?.status || t("acad.active"))}</strong>
      <span>${escapeHtml(selectedTarget ? `${L(selectedTarget, "name") || selectedTarget.lat_name} · ${tf("side", selectedTarget.side, selectedTarget.side)}` : t("acad.all_targets_placed"))}</span>
      <small>${summary ? `${summary.correct_count}/${summary.target_count} ${t("acad.correct")} · ${summary.accuracy_percent}% ${t("acad.accuracy")}` : `${simulationState.placements.length}/${currentSimulation.targets.length} ${t("acad.placed")}`}</small>
    </div>
    ${renderPlacementResults(simulationState.validation)}
  `;
}

function renderPlacementResults(validation) {
  const results = validation?.results || [];
  if (!results.length) return "";
  return `
    <div class="placement-results">
      ${results.slice(-6).map((result) => `
        <div class="placement-result ${escapeHtml(result.status)}">
          <strong>${escapeHtml(L(result.target, "name") || result.target?.lat_name || result.target_id)}</strong>
          <span>${escapeHtml(result.feedback || "")}</span>
          <small>${escapeHtml(result.distance ?? "-")} ${escapeHtml(t("acad.distance"))} · ${escapeHtml(result.signal_quality_percent ?? 0)}% ${escapeHtml(t("acad.signal"))}</small>
        </div>
      `).join("")}
    </div>
  `;
}

function renderSignalFeedback(validation) {
  const signals = validation?.signals;
  if (!signals) return `<div class="signal-panel empty-state compact">${escapeHtml(t("acad.no_signal_yet"))}</div>`;
  return `
    <div class="signal-panel ${escapeHtml(signals.noise_risk)}">
      <div class="signal-summary">
        <strong>${escapeHtml(signals.overall_quality_percent)}%</strong>
        <span>${escapeHtml(signals.noise_risk)} ${escapeHtml(t("acad.noise_risk"))}</span>
      </div>
      <div class="signal-traces">
        ${signals.traces.map((trace) => `
          <div class="signal-trace ${escapeHtml(trace.status)}">
            <span>${escapeHtml(trace.label || trace.target_id)}</span>
            <div class="trace-line"><i style="width:${Number(trace.quality_percent || 0)}%"></i></div>
            <small>${escapeHtml(trace.quality_percent || 0)}%</small>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

function renderSimulationCoach(coachResult) {
  if (!coachResult) return "";
  return `<div class="sim-coach-output">${renderAiResult(coachResult)}</div>`;
}

function bindSimulationControls() {
  simulationPanel.querySelector(".sim-start")?.addEventListener("click", () => startSimulation(true));
  simulationPanel.querySelector(".sim-reset")?.addEventListener("click", () => resetSimulation());
  simulationPanel.querySelector(".sim-coach")?.addEventListener("click", () => askSimulationCoach().catch(showSimulationError));
  simulationPanel.querySelectorAll(".sim-target").forEach((button) => {
    button.addEventListener("click", () => {
      simulationState.selectedTargetId = button.dataset.targetId;
      renderSimulationPanel();
      renderPins(selectedPlanner.pins || []);
    });
  });
}

function startSimulation(render = true) {
  if (!currentSimulation) return;
  simulationState = createEmptySimulationState();
  simulationState.active = true;
  simulationState.selectedTargetId = currentSimulation.targets?.[0]?.target_id || null;
  if (render) {
    renderSimulationPanel();
    renderPins(selectedPlanner.pins || []);
  }
}

function resetSimulation() {
  simulationState = createEmptySimulationState();
  renderSimulationPanel();
  renderPins(selectedPlanner.pins || []);
}

async function handleBodyMapPlacement(event) {
  if (!simulationState.active || !simulationState.selectedTargetId || !currentSimulation) return;
  if (event.target.closest(".map-view-btn") || event.target.closest(".pin-tooltip")) return;
  const point = eventToOriginalPoint(event);
  if (!point) return;
  const placement = {
    target_id: simulationState.selectedTargetId,
    x: point.x,
    y: point.y,
  };
  simulationState.placements = [
    ...simulationState.placements.filter((item) => item.target_id !== placement.target_id),
    placement,
  ];
  await validateSimulation();
  selectNextSimulationTarget();
  currentLessonProgress = simulationState.validation?.progress || await loadLessonProgress(selectedNodeKey);
  learnerReport = await loadLearnerReport();
  renderSimulationPanel();
  renderLessonDetail();
  renderLearnerPanel();
  renderPins(selectedPlanner.pins || []);
}

async function validateSimulation() {
  simulationState.validation = await api("/api/academy/simulations/validate", {
    method: "POST",
    body: JSON.stringify({
      node_key: selectedNodeKey,
      placements: simulationState.placements,
    }),
  });
}

function selectNextSimulationTarget() {
  const placedIds = new Set(simulationState.placements.map((item) => item.target_id));
  const nextTarget = (currentSimulation.targets || []).find((target) => !placedIds.has(target.target_id));
  simulationState.selectedTargetId = nextTarget?.target_id || simulationState.selectedTargetId;
}

async function askSimulationCoach() {
  const validation = simulationState.validation;
  if (!validation) return;
  const weakItems = validation.results.filter((item) => item.status !== "correct");
  const focus = weakItems[0] || validation.results[validation.results.length - 1];
  const question = focus
    ? t("acad.coach_sim_fix", {
        node: L(validation.node, "title"),
        target: L(focus.target, "name") || focus.target_id,
        distance: focus.distance,
        status: focus.status,
      })
    : t("acad.coach_sim_general", { node: L(validation.node, "title") });
  simulationState.coachResult = await api("/api/academy/tutor/ask", {
    method: "POST",
    body: JSON.stringify({ question, node_key: selectedNodeKey, placement_validation: validation, retrieval_top_k: 8 }),
  });
  renderSimulationPanel();
}

function showSimulationError(error) {
  const feedback = simulationPanel.querySelector(".simulator-feedback") || simulationPanel;
  feedback.innerHTML = `<div class="sim-status error"><strong>error</strong><span>${escapeHtml(error.message)}</span></div>`;
}

async function askTutor() {
  const question = tutorQuestion.value.trim();
  if (!question) {
    tutorAnswer.textContent = t("acad.write_question_first");
    return;
  }
  tutorAnswer.textContent = t("acad.deepseek_thinking");
  const result = await api("/api/academy/tutor/ask", {
    method: "POST",
    body: JSON.stringify({
      question,
      node_key: selectedNodeKey,
      placement_validation: simulationState.validation || currentLessonProgress.placement_validation || null,
      waveform_scenario: selectedWaveformScenarioKey,
      case_grade: currentLessonProgress.last_case_grade || null,
      retrieval_top_k: 8,
    }),
  });
  tutorAnswer.innerHTML = renderAiResult(result);
}

function renderEvidencePanel() {
  const protocols = selectedPlanner?.protocols || [];
  const gaps = selectedPlanner?.clinical_plan?.gaps || [];
  const sources = selectedPlanner?.clinical_plan?.traceability?.sources || [];
  const bibliography = currentBibliography || { status: "loading", items: [] };
  evidencePanel.className = "academy-panel";
  evidencePanel.innerHTML = `
    <div class="section-heading">
      <h3>${escapeHtml(t("acad.evidence"))}</h3>
      <span>${sources.length} ${escapeHtml(t("acad.sources"))} · ${protocols.length} ${escapeHtml(t("acad.protocols"))} · ${escapeHtml(bibliography.status)} ${escapeHtml(t("acad.bibliography"))}</span>
    </div>
    <div class="evidence-grid">
      <div class="evidence-column">
        <strong>${escapeHtml(t("acad.traceability"))}</strong>
        <div class="source-row stacked">
          ${sources.map((source) => `<span>${escapeHtml(source)}</span>`).join("") || `<span>${escapeHtml(t("acad.no_explicit_source"))}</span>`}
        </div>
      </div>
      <div class="evidence-column">
        <strong>${escapeHtml(t("acad.protocols_title"))}</strong>
        <div class="protocol-grid compact-protocols">
          ${protocols.map(renderProtocol).join("") || `<div class="empty-state compact">${escapeHtml(t("acad.no_protocol"))}</div>`}
        </div>
      </div>
      <div class="evidence-column">
        <strong>${escapeHtml(t("acad.kb_gaps"))}</strong>
        <div class="gap-list">
          ${gaps.map(renderGap).join("") || `<div class="empty-state compact">${escapeHtml(t("acad.no_gaps"))}</div>`}
        </div>
      </div>
      <div class="evidence-column bibliography-column">
        <strong>${escapeHtml(t("acad.pubmed_biblio"))}</strong>
        ${renderBibliography(bibliography)}
      </div>
    </div>
  `;
}

function renderBibliography(bibliography) {
  if (bibliography.status === "unavailable") {
    return `<div class="empty-state compact">${escapeHtml(t("acad.pubmed_unavailable", { error: bibliography.error || t("acad.request_failed") }))}</div>`;
  }
  if (!bibliography.items?.length) {
    return `<div class="empty-state compact">${escapeHtml(t("acad.no_pubmed_results", { query: bibliography.query || "-" }))}</div>`;
  }
  return `
    <div class="bibliography-query">${escapeHtml(bibliography.query)} · ${escapeHtml(bibliography.cache || "live")}</div>
    <div class="bibliography-list">
      ${bibliography.items.map((item) => `
        <a class="bibliography-item" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">
          <strong>${escapeHtml(item.title)}</strong>
          <span>${escapeHtml([item.journal, item.pubdate].filter(Boolean).join(" · "))}</span>
          <small>${escapeHtml((item.authors || []).join(" · "))}</small>
        </a>
      `).join("")}
    </div>
  `;
}

function renderProtocol(protocol) {
  const linkLabel = protocol.link_type === "domain_default" ? t("acad.domain_default") : protocol.link_type === "inherited" ? t("acad.inherited") : t("acad.direct");
  const rows = Object.entries(L(protocol, "content") || {})
    .map(([key, value]) => `<p><strong>${escapeHtml(key)}:</strong> ${escapeHtml(Array.isArray(value) ? value.join(" · ") : String(value))}</p>`)
    .join("");
  return `
    <article class="protocol-item">
      <strong>${escapeHtml(L(protocol, "title") || protocol.key)}</strong>
      <span>${escapeHtml([protocol.domain, linkLabel].filter(Boolean).join(" · "))}</span>
      ${rows}
    </article>
  `;
}

function renderGap(gap) {
  return `
    <div class="gap-item ${escapeHtml(gap.severity)}">
      <strong>${escapeHtml(gap.title)}</strong>
      <span>${escapeHtml(gap.detail)}</span>
    </div>
  `;
}

function renderPins(pins, options = {}) {
  currentPins = pins;
  if (!options.preserveView) currentMapView = chooseBestMapView(pins);
  applyMapViewport();
  renderMapViewControls();
  const visiblePins = pins.filter((pin) => projectPin(pin));
  pinsLayer.innerHTML = simulationState.active
    ? renderSimulationLayer(visiblePins)
    : visiblePins.map(renderPin).join("");
  if (mapViewCount) mapViewCount.textContent = `${visiblePins.length}/${pins.length} ${t("acad.pins_lc")}`;
}

function renderSimulationLayer(visiblePins) {
  const targetLookup = new Map(visiblePins.map((pin) => [pin.target_id, pin]));
  const validationLookup = new Map((simulationState.validation?.results || []).map((result) => [result.target_id, result]));
  const placedIds = new Set(simulationState.placements.map((placement) => placement.target_id));
  const targets = visiblePins.map((pin) => {
    const projectedTarget = projectPin(pin);
    if (!projectedTarget) return "";
    const result = validationLookup.get(pin.target_id);
    const status = result?.status || (placedIds.has(pin.target_id) ? "placed" : "pending");
    const selectedClass = simulationState.selectedTargetId === pin.target_id ? "selected" : "";
    return renderExpectedMarker(projectedTarget, status, pin, selectedClass);
  }).join("");
  const placements = simulationState.placements.map((placement) => {
    const target = targetLookup.get(placement.target_id);
    const result = validationLookup.get(placement.target_id);
    const projectedAttempt = projectPoint(placement.x, placement.y);
    if (!projectedAttempt) return "";
    return `
      <button class="placement-marker ${escapeHtml(result?.status || "placed")}" type="button" style="left:${projectedAttempt.left}%; top:${projectedAttempt.top}%;" aria-label="${escapeHtml(L(target, "name") || placement.target_id)} attempt">
        <span>${escapeHtml(result?.signal_quality_percent ?? "")}</span>
      </button>
    `;
  }).join("");
  return `${targets}${placements}`;
}

function renderExpectedMarker(projectedTarget, status, target, selectedClass = "") {
  return `<div class="expected-marker ${escapeHtml(status)} ${escapeHtml(selectedClass)}" style="left:${projectedTarget.left}%; top:${projectedTarget.top}%;" title="${escapeHtml(L(target, "name") || target.lat_name)}"></div>`;
}

function renderPin(pin) {
  const projected = projectPin(pin);
  if (!projected) return "";
  const { left, top, x, y } = projected;
  const signals = [...(pin.roots || []), ...(pin.cranial_nerves || []).map((item) => `CN ${item}`)];
  const context = signals.join(" · ") || [L(pin, "node_title"), pin.node_domain].filter(Boolean).join(" · ") || t("acad.no_linked_context");
  const alignClass = tooltipAlignment(left, top);
  const coordinateText = `${formatCoordinate(x)}%, ${formatCoordinate(y)}%`;
  const label = [L(pin, "name") || pin.lat_name, tf("side", pin.side, pin.side), pin.view].filter(Boolean).join(" · ");
  return `
    <button class="pin ${alignClass}" type="button" data-side="${escapeHtml(pin.side || "")}" aria-label="${escapeHtml(label)}" style="left:${left}%; top:${top}%;">
      <span class="pin-marker" aria-hidden="true"></span>
      <span class="pin-tooltip" role="tooltip">
        <strong>${escapeHtml(L(pin, "name") || pin.lat_name)}</strong>
        <small>${escapeHtml([pin.lat_name, tf("side", pin.side, pin.side), pin.view].filter(Boolean).join(" · "))}</small>
        <span><b>${escapeHtml(t("acad.electrode"))}:</b> ${escapeHtml(L(pin, "placement") || t("acad.no_placement_stored"))}</span>
        <span><b>${escapeHtml(t("acad.check"))}:</b> ${escapeHtml(L(pin, "action") || t("acad.no_check_stored"))}</span>
        ${renderPinMontage(L(pin, "electrode_montage"))}
        <span><b>${escapeHtml(t("acad.linked"))}:</b> ${escapeHtml(context)}</span>
        <span><b>${escapeHtml(t("acad.map"))}:</b> ${escapeHtml(tf("coord", pin.coordinate_status, pin.coordinate_status || t("acad.mapped_fallback")))} · ${escapeHtml(pin.coordinate_system || t("acad.percentage"))} ${escapeHtml(coordinateText)}</span>
        <em>${escapeHtml(pin.coordinate_source || t("acad.coord_source_missing"))}</em>
      </span>
    </button>
  `;
}

function renderPinMontage(montage) {
  if (!montage || typeof montage !== "object") return "";
  const rows = [];
  if (montage.active) rows.push(`<span class="pin-montage-line">${escapeHtml(montage.active)}</span>`);
  if (montage.reference) rows.push(`<span class="pin-montage-line">${escapeHtml(montage.reference)}</span>`);
  if (montage.stimulus) rows.push(`<span class="pin-montage-line"><b>${escapeHtml(t("acad.stim"))}:</b> ${escapeHtml(montage.stimulus)}</span>`);
  const meta = [
    montage.technique,
    montage.inter_electrode_cm ? `${montage.inter_electrode_cm} cm` : "",
    montage.nerve,
    montage.myotome || montage.branch,
  ].filter(Boolean).map((item) => escapeHtml(item)).join(" · ");
  if (meta) rows.push(`<span class="pin-montage-meta">${meta}</span>`);
  if (montage.pearl) rows.push(`<span class="pin-montage-pearl">${escapeHtml(montage.pearl)}</span>`);
  if (!rows.length) return "";
  return `<span class="pin-montage">${rows.join("")}</span>`;
}

function renderMapViewControls() {
  if (!mapViewControls) return;
  mapViewControls.innerHTML = Object.entries(mapViewDefinitions).map(([key, view]) => `
    <button class="map-view-btn${key === currentMapView ? " active" : ""}" type="button" data-view="${escapeHtml(key)}">
      ${escapeHtml(t(view.i18nKey))}
    </button>
  `).join("");
  mapViewControls.querySelectorAll(".map-view-btn").forEach((button) => {
    button.addEventListener("click", () => setMapView(button.dataset.view));
  });
}

function setMapView(viewKey) {
  if (!mapViewDefinitions[viewKey]) return;
  currentMapView = viewKey;
  renderPins(currentPins, { preserveView: true });
}

function chooseBestMapView(pins) {
  if (!pins.length) return "full";
  const preferred = preferredMapViews(pins).find((key) => {
    const view = mapViewDefinitions[key];
    return view && pins.every((pin) => isPinInsideBounds(pin, view.bounds));
  });
  if (preferred) return preferred;
  const candidates = Object.entries(mapViewDefinitions)
    .filter(([key]) => key !== "full")
    .filter(([, view]) => pins.every((pin) => isPinInsideBounds(pin, view.bounds)))
    .map(([key, view]) => ({ key, area: viewArea(view.bounds) }))
    .sort((a, b) => a.area - b.area);
  return candidates[0]?.key || "full";
}

function preferredMapViews(pins) {
  const roots = new Set(pins.flatMap((pin) => pin.roots || []));
  const names = new Set(pins.map((pin) => String(pin.lat_name || "").toLowerCase()));
  const hasCranial = pins.some((pin) => (pin.cranial_nerves || []).length || pin.node_domain === "cranial");
  const viewNames = new Set(pins.map((pin) => pin.view).filter(Boolean));
  if (hasCranial) return ["headNeck", "anterior", "posterior", "full"];
  if (hasUpperLimbTargets(roots, names)) {
    if (viewNames.size === 1 && viewNames.has("posterior")) return ["posteriorUpperLimb", "posterior", "full"];
    if (viewNames.size === 1 && viewNames.has("anterior")) return ["upperLimb", "anterior", "full"];
    return ["upperLimb", "posteriorUpperLimb", "anterior", "posterior", "full"];
  }
  if (hasLowerLimbTargets(roots, names)) return ["lowerLimb", "lumbosacral", "anterior", "posterior", "full"];
  if (viewNames.size === 1 && viewNames.has("posterior")) return ["posterior", "full"];
  if (viewNames.size === 1 && viewNames.has("anterior")) return ["anterior", "full"];
  return ["full"];
}

function hasUpperLimbTargets(roots, names) {
  const upperRoots = ["C5", "C6", "C7", "C8", "T1"];
  const upperNames = ["deltoid", "biceps brachii", "triceps brachii", "brachioradialis", "flexor carpi radialis", "flexor digitorum profundus", "abductor pollicis brevis", "abductor digiti minimi", "extensor carpi radialis", "extensor digitorum", "first dorsal interosseous"];
  return upperRoots.some((root) => roots.has(root)) || upperNames.some((name) => names.has(name));
}

function hasLowerLimbTargets(roots, names) {
  const lowerRoots = ["L1", "L2", "L3", "L4", "L5", "S1", "S2", "S3", "S4"];
  const lowerNames = ["iliopsoas", "cremaster", "adductor longus", "vastus medialis", "tibialis anterior", "peroneus longus", "extensor hallucis longus", "abductor hallucis", "gluteus medius", "biceps femoris", "gastrocnemius medial", "external anal sphincter"];
  return lowerRoots.some((root) => roots.has(root)) || lowerNames.some((name) => names.has(name));
}

function applyMapViewport() {
  if (!bodyMap) return;
  const view = mapViewDefinitions[currentMapView] || mapViewDefinitions.full;
  const bounds = view.bounds;
  const width = bounds.x2 - bounds.x1;
  const height = bounds.y2 - bounds.y1;
  const ratio = bodyMapAspect * (width / height);
  bodyMap.style.aspectRatio = String(ratio);
  bodyMap.style.backgroundSize = `${formatCoordinate(10000 / width)}% ${formatCoordinate(10000 / height)}%`;
  bodyMap.style.backgroundPosition = `${formatCoordinate(backgroundOffset(bounds.x1, width))}% ${formatCoordinate(backgroundOffset(bounds.y1, height))}%`;
  bodyMap.dataset.view = currentMapView;
}

function projectPin(pin) {
  const x = Number(pin.x);
  const y = Number(pin.y);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return projectPoint(x, y);
}

function projectPoint(x, y) {
  const bounds = (mapViewDefinitions[currentMapView] || mapViewDefinitions.full).bounds;
  if (!isPointInsideBounds(x, y, bounds)) return null;
  const width = bounds.x2 - bounds.x1;
  const height = bounds.y2 - bounds.y1;
  return {
    x,
    y,
    left: ((x - bounds.x1) / width) * 100,
    top: ((y - bounds.y1) / height) * 100,
  };
}

function eventToOriginalPoint(event) {
  const rect = bodyMap.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const left = ((event.clientX - rect.left) / rect.width) * 100;
  const top = ((event.clientY - rect.top) / rect.height) * 100;
  if (left < 0 || left > 100 || top < 0 || top > 100) return null;
  const bounds = (mapViewDefinitions[currentMapView] || mapViewDefinitions.full).bounds;
  return {
    x: Number((bounds.x1 + (left / 100) * (bounds.x2 - bounds.x1)).toFixed(2)),
    y: Number((bounds.y1 + (top / 100) * (bounds.y2 - bounds.y1)).toFixed(2)),
  };
}

function isPinInsideBounds(pin, bounds) {
  const x = Number(pin.x);
  const y = Number(pin.y);
  return Number.isFinite(x) && Number.isFinite(y) && isPointInsideBounds(x, y, bounds);
}

function isPointInsideBounds(x, y, bounds) {
  return x >= bounds.x1 && x <= bounds.x2 && y >= bounds.y1 && y <= bounds.y2;
}

function viewArea(bounds) {
  return (bounds.x2 - bounds.x1) * (bounds.y2 - bounds.y1);
}

function backgroundOffset(start, size) {
  if (size >= 100) return 0;
  return (start / (100 - size)) * 100;
}

function tooltipAlignment(x, y) {
  if (y < 18) return "tooltip-below";
  if (x > 70) return "tooltip-left";
  if (x < 30) return "tooltip-right";
  return "tooltip-center";
}

function formatCoordinate(value) {
  return Number(value).toFixed(1).replace(/\.0$/, "");
}

function formatSvgNumber(value) {
  return Number(value).toFixed(2).replace(/\.00$/, "");
}

function renderMetric(label, value, detail) {
  return `
    <div class="metric-card">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(detail)}</small>
    </div>
  `;
}

function renderAiResult(result) {
  const answer = escapeHtml(result.answer || t("acad.no_answer"));
  const retrieval = result.retrieval || { status: "unknown", items: [] };
  const items = retrieval.items || [];
  const bibliography = result.bibliography?.items || [];
  return `
    <div class="ai-response">${answer}</div>
    <div class="ai-evidence">
      <strong>${escapeHtml(t("acad.knowledge_evidence"))} · ${escapeHtml(retrieval.status || "unknown")}</strong>
      ${items.map(renderEvidenceItem).join("") || `<span>${escapeHtml(t("acad.no_evidence_chunks"))}</span>`}
      ${bibliography.length ? `<strong>${escapeHtml(t("acad.pubmed_bibliography"))}</strong>${bibliography.slice(0, 4).map((item) => `<div class="evidence-item"><span>${escapeHtml(item.title)}</span><small>${escapeHtml([item.journal, item.pubdate].filter(Boolean).join(" · "))}</small></div>`).join("")}` : ""}
    </div>
  `;
}

function renderEvidenceItem(item) {
  const source = [item.source_collection, item.source_key].filter(Boolean).join(" · ");
  return `
    <div class="evidence-item">
      <span>${escapeHtml(item.title || item.source_key || t("acad.knowledge_chunk"))}</span>
      <small>${escapeHtml(source)}${item.score ? ` · ${escapeHtml(item.score)}` : ""}</small>
    </div>
  `;
}

function firstLessonKey() {
  for (const module of academyData.modules || []) {
    const lesson = module.lessons?.[0];
    if (lesson?.node_key) return lesson.node_key;
  }
  return null;
}

function nextLessonKey() {
  const adaptiveLesson = (learnerReport?.adaptive_path || []).find((lesson) => lesson.node_key);
  if (adaptiveLesson?.node_key) return adaptiveLesson.node_key;
  const reviewLesson = (learnerReport?.needs_review || []).find((lesson) => lesson.node_key);
  return reviewLesson?.node_key || selectedNodeKey || firstLessonKey();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function initialiseBodyMap() {
  renderMapViewControls();
  applyMapViewport();
  if (!bodyMapImage) return;
  const setAspect = () => {
    if (bodyMapImage.naturalWidth && bodyMapImage.naturalHeight) {
      bodyMapAspect = bodyMapImage.naturalWidth / bodyMapImage.naturalHeight;
      applyMapViewport();
    }
  };
  if (bodyMapImage.complete) setAspect();
  bodyMapImage.addEventListener("load", setAspect, { once: true });
}

askTutorButton.addEventListener("click", () => askTutor().catch((error) => {
  tutorAnswer.textContent = error.message;
}));
bodyMap.addEventListener("click", (event) => {
  handleBodyMapPlacement(event).catch(showSimulationError);
});

document.addEventListener("ionm:langchange", () => {
  if (!academyData) return;
  renderAcademyShell();
  renderLearnerPanel();
  if (selectedNodeKey) {
    renderLessonDetail();
    renderSimulationPanel();
    renderWaveformPanel();
    renderEvidencePanel();
    renderPins(selectedPlanner?.pins || []);
  }
});

init();
