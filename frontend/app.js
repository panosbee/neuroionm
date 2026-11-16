let selectedNodeKey = null;
let selectedPlanner = null;
let currentCaseId = null;
let currentCaseData = null;
let currentCaseEvents = [];
let currentCaseDebriefs = [];
let currentPins = [];
let currentMapView = "full";
let bodyMapAspect = 0.62;
let treeData = [];
let nodeIndex = new Map();
let selectedPath = [];
const startupParams = new URLSearchParams(window.location.search);
const startupNodeKey = startupParams.get("node");
const startupCaseId = startupParams.get("case_id");
const startedFromCommandCenter = startupParams.get("started") === "1";

const treeRoot = document.getElementById("tree-root");
const decisionPath = document.getElementById("decision-path");
const nodeDetail = document.getElementById("node-detail");
const pinsLayer = document.getElementById("pins-layer");
const healthStatus = document.getElementById("health-status");
const activeCase = document.getElementById("active-case");
const caseTimeline = document.getElementById("case-timeline");
const createEventButton = document.getElementById("create-event-btn");
const runDebriefButton = document.getElementById("run-debrief-btn");
const caseDebriefOutput = document.getElementById("case-debrief-output");
const caseDebriefHistory = document.getElementById("case-debrief-history");
const bodyMap = document.getElementById("bodymap");
const bodyMapImage = document.getElementById("bodymap-image");
const mapViewControls = document.getElementById("map-view-controls");
const mapViewCount = document.getElementById("map-view-count");
const copilotOutput = document.getElementById("copilot-output");
const copilotObservation = document.getElementById("copilot-observation");
const runCopilotButton = document.getElementById("run-copilot-btn");
let selectedCopilotFocus = "setup";

const t = (key, vars) => (window.I18N ? window.I18N.t(key, vars) : key);
// Localize a content field (KB/muscle/channel/pin data) to the active language.
const L = (obj, base) => (window.I18N && window.I18N.L ? window.I18N.L(obj, base) : (obj ? obj[base] : undefined));
// Resolve a namespaced i18n key, returning a fallback (or the raw key) when no translation exists.
function tf(prefix, key, fallback) {
  if (key == null || key === "") return fallback != null ? fallback : "";
  const full = `${prefix}.${key}`;
  const val = window.I18N ? window.I18N.t(full) : full;
  return val === full ? (fallback != null ? fallback : key) : val;
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

function nodeTypeLabel(type) {
  return tf("nodetype", type, type);
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

async function renderTree(nodes) {
  const normalizedNodes = Array.isArray(nodes) ? nodes : [nodes];
  const liveNode = findNode(normalizedNodes, "live");
  treeData = liveNode ? [liveNode] : normalizedNodes;
  nodeIndex = buildNodeIndex(treeData);
  const initialNodeKey = startupNodeKey && nodeIndex.has(startupNodeKey)
    ? startupNodeKey
    : nodeIndex.has("live") ? "live" : treeData[0]?.key;
  selectedPath = initialNodeKey ? nodeIndex.get(initialNodeKey)?.path || [initialNodeKey] : [];
  renderDecisionFlow();
  if (initialNodeKey) await selectNode(initialNodeKey);
}

function findNode(nodes, key) {
  for (const node of nodes) {
    if (node.key === key) return node;
    const child = findNode(node.children || [], key);
    if (child) return child;
  }
  return null;
}

function buildNodeIndex(nodes, parentKey = null, path = []) {
  const index = new Map();
  for (const node of nodes) {
    const nodePath = [...path, node.key];
    index.set(node.key, { node, parentKey, path: nodePath });
    const childIndex = buildNodeIndex(node.children || [], node.key, nodePath);
    childIndex.forEach((value, key) => index.set(key, value));
  }
  return index;
}

function renderDecisionFlow() {
  const columns = buildDecisionColumns();
  renderDecisionPath();
  treeRoot.innerHTML = columns.map(renderDecisionColumn).join("");
  treeRoot.querySelectorAll(".decision-option").forEach((button) => {
    button.addEventListener("click", () => selectNode(button.dataset.key));
  });
  treeRoot.scrollTo({ left: treeRoot.scrollWidth, behavior: "smooth" });
}

function buildDecisionColumns() {
  const columns = [{ title: "Start", nodes: treeData, depth: 0 }];
  for (const key of selectedPath) {
    const indexed = nodeIndex.get(key);
    const children = indexed?.node.children || [];
    if (children.length) {
      columns.push({ title: L(indexed.node, "title"), nodes: children, depth: columns.length });
    }
  }
  return columns;
}

function renderDecisionColumn(column) {
  return `
    <div class="decision-column">
      <div class="decision-column-title">${escapeHtml(column.title)}</div>
      <div class="decision-options">
        ${column.nodes.map((node) => renderDecisionOption(node, column.depth)).join("")}
      </div>
    </div>
  `;
}

function renderDecisionOption(node, depth) {
  const selectedAtDepth = selectedPath[depth] === node.key;
  const hasChildren = Boolean(node.children?.length);
  return `
    <button class="decision-option${selectedAtDepth ? " active" : ""}${hasChildren ? " has-children" : ""}" data-key="${escapeHtml(node.key)}">
      <span>${escapeHtml(L(node, "title"))}</span>
      <small>${escapeHtml(nodeTypeLabel(node.node_type))}</small>
    </button>
  `;
}

function renderDecisionPath() {
  if (!selectedPath.length) {
    decisionPath.innerHTML = `<span class="path-empty">${t("app.pick_start_node")}</span>`;
    return;
  }
  decisionPath.innerHTML = selectedPath.map((key, index) => {
    const item = nodeIndex.get(key)?.node;
    const arrow = index < selectedPath.length - 1 ? '<span class="path-arrow">→</span>' : "";
    return `<button class="path-node" data-key="${escapeHtml(key)}">${escapeHtml(L(item, "title") || key)}</button>${arrow}`;
  }).join("");
  decisionPath.querySelectorAll(".path-node").forEach((button) => {
    button.addEventListener("click", () => selectNode(button.dataset.key));
  });
}

async function selectNode(nodeKey) {
  if (nodeKey === "academy" || nodeKey.startsWith("academy.")) {
    window.location.href = `/academy.html?node=${encodeURIComponent(nodeKey)}`;
    return;
  }
  selectedNodeKey = nodeKey;
  selectedPath = nodeIndex.get(nodeKey)?.path || [nodeKey];
  renderDecisionFlow();
  nodeDetail.className = "node-detail";
  nodeDetail.textContent = t("app.loading");
  selectedPlanner = await api(`/api/knowledge/planner?node_key=${encodeURIComponent(nodeKey)}`);
  renderPlanner(selectedPlanner);
  teleNotifyNodeChange(nodeKey);
}

function renderPlanner(planner) {
  const { node, roots, cranial_nerves: cranialNerves, protocols, clinical_plan: clinicalPlan } = planner;
  const readinessState = clinicalPlan?.readiness?.state;
  nodeDetail.innerHTML = `
    <div class="node-heading">
      <div>
        <h2>${escapeHtml(L(node, "title"))}</h2>
        <p>${escapeHtml(L(node, "summary") || "")}</p>
      </div>
      <div class="node-type">${escapeHtml(nodeTypeLabel(node.node_type))}</div>
    </div>
    ${renderClinicalSummary(clinicalPlan, roots, cranialNerves)}
    ${renderSpecialtyPanel(node)}
    ${readinessState === "navigation" ? renderNextDecisions(node.key) : renderClinicalWorkbench(planner)}
  `;
  nodeDetail.querySelectorAll(".next-decision").forEach((button) => {
    button.addEventListener("click", () => selectNode(button.dataset.key));
  });
  renderPins(planner.pins || []);
  renderCopilotReadyState();
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
        <h3>${t("app.ssep_title", { potential: escapeHtml(montage.potential || "") })}</h3>
        <span>${t("app.recording_channels", { n: channels.length })}</span>
      </div>
      ${montage.indication ? `<p class="specialty-indication">${escapeHtml(montage.indication)}</p>` : ""}
      <div class="specialty-grid">
        <div class="specialty-card">
          <h4>${t("app.stimulation")}</h4>
          <p><strong>${t("app.stim_site")}</strong> ${escapeHtml(stim.site || "—")}</p>
          <p><strong>${t("app.stim_rate")}</strong> ${escapeHtml(stim.rate_hz || "—")} Hz</p>
          <p><strong>Pulse width:</strong> ${escapeHtml(stim.pulse_width_us || "—")} µs</p>
          <p><strong>${t("app.stim_intensity")}</strong> ${escapeHtml(stim.intensity || "—")}</p>
          ${stim.polarity ? `<p><strong>${t("app.stim_polarity")}</strong> ${escapeHtml(stim.polarity)}</p>` : ""}
        </div>
        <div class="specialty-card">
          <h4>${t("app.rec_setup")}</h4>
          <p><strong>Scalp (10-20):</strong> ${scalp.map((p) => `<span class="chip">${escapeHtml(p)}</span>`).join(" ") || "—"}</p>
          <p><strong>${t("app.ground")}</strong> ${escapeHtml(montage.ground || "—")}</p>
          <p><strong>${t("app.anesthesia")}</strong> ${escapeHtml(montage.anesthesia || "—")}</p>
        </div>
      </div>
      <div class="channel-montage-table">
        <div class="cmt-head">
          <span>${t("app.col_potential")}</span><span>Montage</span><span>${t("app.col_latency")}</span><span>${t("app.col_role")}</span>
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
      ${montage.warning ? `<div class="specialty-warning"><strong>Warning criteria:</strong> ${escapeHtml(montage.warning)}</div>` : ""}
    </section>
  `;
}

function renderScrewMapping(mapping) {
  const myotomes = mapping.recording_myotomes || [];
  return `
    <section class="clinical-section wide-section specialty-section screw-mapping">
      <div class="section-heading">
        <h3>${t("app.screw_title", { level: escapeHtml(mapping.level || "") })}</h3>
        <span>${escapeHtml(mapping.region || "")}</span>
      </div>
      <div class="specialty-grid">
        <div class="specialty-card">
          <h4>${t("app.entry_point")}</h4>
          <p>${escapeHtml(mapping.entry_point || "—")}</p>
        </div>
        <div class="specialty-card">
          <h4>${t("app.trajectory")}</h4>
          <p>${escapeHtml(mapping.trajectory || "—")}</p>
        </div>
        <div class="specialty-card danger-card">
          <h4>${t("app.at_risk")}</h4>
          <p>${escapeHtml(mapping.at_risk || "—")}</p>
        </div>
        <div class="specialty-card">
          <h4>${t("app.rec_muscles")}</h4>
          <div class="mini-tags">
            ${myotomes.map((m) => `<span>${escapeHtml(m)}</span>`).join("") || "<span>—</span>"}
          </div>
        </div>
      </div>
      ${mapping.t_emg ? `<div class="specialty-warning"><strong>Triggered EMG thresholds:</strong> ${escapeHtml(mapping.t_emg)}</div>` : ""}
    </section>
  `;
}

function renderClinicalWorkbench(planner) {
  const clinicalPlan = planner.clinical_plan;
  const protocols = planner.protocols || [];
  return `
    <div class="workbench-grid">
      ${renderCoverage(clinicalPlan)}
      ${renderGaps(clinicalPlan)}
    </div>
    ${renderChannelPlan(clinicalPlan)}
    ${renderSsepPlan(clinicalPlan)}
    ${renderProtocols(protocols)}
    ${renderTraceability(clinicalPlan)}
  `;
}

function renderNextDecisions(nodeKey) {
  const children = nodeIndex.get(nodeKey)?.node.children || [];
  return `
    <section class="clinical-section wide-section next-section">
      <div class="section-heading">
        <h3>Next Decisions</h3>
        <span>${children.length} options</span>
      </div>
      <div class="next-decision-grid">
        ${children.map(renderNextDecision).join("") || `<div class="empty-state compact">${t("app.no_next_steps")}</div>`}
      </div>
    </section>
  `;
}

function renderNextDecision(node) {
  const hasChildren = Boolean(node.children?.length);
  return `
    <button class="next-decision" data-key="${escapeHtml(node.key)}">
      <strong>${escapeHtml(L(node, "title"))}</strong>
      <span>${escapeHtml(L(node, "summary") || nodeTypeLabel(node.node_type))}</span>
      <small>${hasChildren ? "opens next branch" : "terminal node"}</small>
    </button>
  `;
}

function renderClinicalSummary(plan, roots, cranialNerves) {
  const coverage = plan?.coverage || {};
  const readiness = plan?.readiness || { state: "review", detail: "No clinical plan returned." };
  return `
    <div class="clinical-strip">
      <div class="readiness-card ${escapeHtml(readiness.state)}">
        <span>${escapeHtml(tf("readiness", readiness.state, readiness.label || readiness.state))}</span>
        <strong>${escapeHtml(readiness.detail || "")}</strong>
      </div>
      ${renderMetric("Roots/CN", [...roots, ...cranialNerves.map((item) => `CN ${item}`)].join(" · ") || "domain", "linked anatomy")}
      ${renderMetric("Channels", coverage.muscle_count ?? 0, `${coverage.mapped_muscle_count ?? 0} mapped`)}
      ${renderMetric("Pins", coverage.pin_count ?? 0, `${coverage.bilateral_muscle_count ?? 0} bilateral muscles`)}
      ${renderMetric("Protocols", coverage.protocol_count ?? 0, "linked docs")}
    </div>
  `;
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

function renderCoverage(plan) {
  const coverage = plan?.coverage || {};
  const roots = coverage.roots || [];
  const cranialNerves = coverage.cranial_nerves || [];
  const items = [
    ...roots.map((item) => ({ title: item.root, muscles: item.muscles, status: item.status })),
    ...cranialNerves.map((item) => ({ title: `CN ${item.cranial_nerve}`, muscles: item.muscles, status: item.status })),
  ];
  return `
    <section class="clinical-section">
      <div class="section-heading">
        <h3>Coverage</h3>
        <span>${items.length} linked structures</span>
      </div>
      <div class="coverage-list">
        ${items.map(renderCoverageItem).join("") || `<div class="empty-state compact">${t("app.no_coverage")}</div>`}
      </div>
    </section>
  `;
}

function renderCoverageItem(item) {
  return `
    <div class="coverage-item ${escapeHtml(item.status)}">
      <div>
        <strong>${escapeHtml(item.title)}</strong>
        <span>${escapeHtml(item.muscles.length)} linked channels</span>
      </div>
      <div class="mini-tags">
        ${item.muscles.map((muscle) => `<span>${escapeHtml(muscle)}</span>`).join("") || `<span>gap</span>`}
      </div>
    </div>
  `;
}

function renderGaps(plan) {
  const gaps = plan?.gaps || [];
  return `
    <section class="clinical-section">
      <div class="section-heading">
        <h3>Knowledge Gaps</h3>
        <span>${gaps.length} open</span>
      </div>
      <div class="gap-list">
        ${gaps.map(renderGap).join("") || `<div class="empty-state compact">${t("app.no_gaps")}</div>`}
      </div>
    </section>
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

function renderChannelPlan(plan) {
  const channels = plan?.channel_plan || [];
  return `
    <section class="clinical-section wide-section">
      <div class="section-heading">
        <h3>Channel Plan</h3>
        <span>${channels.length} muscles/electrodes</span>
      </div>
      <div class="channel-table">
        ${channels.map(renderChannel).join("") || `<div class="empty-state compact">${t("app.no_muscles")}</div>`}
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
        <span>${escapeHtml(L(channel, "placement") || "No placement in KB")}</span>
        <small>${escapeHtml(L(channel, "action") || "")}</small>
      </div>
      <div class="channel-tags">
        ${signals.map((signal) => `<span>${escapeHtml(signal)}</span>`).join("") || `<span>${escapeHtml(channel.coordinate_status)}</span>`}
      </div>
      <div class="coord-status">${escapeHtml(tf("coord", channel.coordinate_status, channel.coordinate_status))}</div>
    </div>
  `;
}

function renderSsepPlan(plan) {
  const ssep = plan?.ssep_plan;
  if (!ssep) {
    return "";
  }
  const generators = ssep.generators || {};
  const groups = [
    { key: "cortical", label: t("app.ssep_cortical"), names: generators.cortical || [] },
    { key: "subcortical", label: t("app.ssep_subcortical"), names: generators.subcortical || [] },
    { key: "peripheral", label: t("app.ssep_peripheral"), names: generators.peripheral || [] },
  ].filter((group) => group.names.length);
  const channels = ssep.channels || [];
  return `
    <section class="clinical-section wide-section ssep-section">
      <div class="section-heading">
        <h3>${escapeHtml(t("app.ssep_plan"))}</h3>
        <span>${channels.length} ${escapeHtml(t("app.ssep_potentials"))}</span>
      </div>
      <div class="ssep-generators">
        ${groups
          .map(
            (group) => `
          <div class="ssep-generator ${escapeHtml(group.key)}">
            <span>${escapeHtml(group.label)}</span>
            <strong>${group.names.map((name) => escapeHtml(name)).join(" · ")}</strong>
          </div>`
          )
          .join("")}
      </div>
      <div class="channel-table">
        ${channels.map(renderSsepChannel).join("") || `<div class="empty-state compact">${t("app.no_muscles")}</div>`}
      </div>
      ${ssep.warning ? `<div class="specialty-warning"><strong>${escapeHtml(t("app.ssep_warning"))}:</strong> ${escapeHtml(ssep.warning)}</div>` : ""}
    </section>
  `;
}

function renderSsepChannel(channel) {
  return `
    <div class="channel-row ${escapeHtml(channel.generator || "unclassified")}">
      <div class="channel-main">
        <strong>${escapeHtml(channel.name || "")}</strong>
        <span>${escapeHtml(channel.montage || "")}</span>
      </div>
      <div class="channel-detail">
        <span>${escapeHtml(channel.role || "")}</span>
        <small>${channel.latency_ms ? `${escapeHtml(channel.latency_ms)} ms` : ""}</small>
      </div>
      <div class="coord-status">${escapeHtml(tf("ssep_gen", channel.generator, channel.generator || "unclassified"))}</div>
    </div>
  `;
}

function renderProtocols(protocols) {
  return `
    <section class="clinical-section wide-section">
      <div class="section-heading">
        <h3>Protocols</h3>
        <span>${protocols.length} linked</span>
      </div>
      <div class="protocol-grid">
        ${protocols.map(renderProtocol).join("") || `<div class="empty-state compact">${t("app.no_protocol")}</div>`}
      </div>
    </section>
  `;
}

function renderProtocol(protocol) {
  const linkLabel = protocol.link_type === "domain_default" ? "domain default" : protocol.link_type === "inherited" ? "inherited" : "direct";
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

function renderTraceability(plan) {
  const sources = plan?.traceability?.sources || [];
  return `
    <section class="clinical-section wide-section trace-section">
      <div class="section-heading">
        <h3>Traceability</h3>
        <span>${sources.length} sources</span>
      </div>
      <div class="source-row">
        ${sources.map((source) => `<span>${escapeHtml(source)}</span>`).join("") || `<span>No explicit source attached</span>`}
      </div>
    </section>
  `;
}

function renderPins(pins, options = {}) {
  currentPins = pins;
  if (!options.preserveView) currentMapView = chooseBestMapView(pins);
  applyMapViewport();
  renderMapViewControls();
  const visiblePins = pins.filter((pin) => projectPin(pin));
  pinsLayer.innerHTML = visiblePins.map(renderPin).join("");
  if (mapViewCount) mapViewCount.textContent = `${visiblePins.length}/${pins.length} pins`;
}

function renderPin(pin) {
  const projected = projectPin(pin);
  if (!projected) return "";
  const { left, top, x, y } = projected;
  const signals = [...(pin.roots || []), ...(pin.cranial_nerves || []).map((item) => `CN ${item}`)];
  const context = signals.join(" · ") || [L(pin, "node_title"), pin.node_domain].filter(Boolean).join(" · ") || "No linked context";
  const alignClass = tooltipAlignment(left, top);
  const coordinateText = `${formatCoordinate(x)}%, ${formatCoordinate(y)}%`;
  const label = [L(pin, "name") || pin.lat_name, tf("side", pin.side, pin.side), pin.view].filter(Boolean).join(" · ");
  return `
    <button class="pin ${alignClass}" type="button" data-side="${escapeHtml(pin.side || "")}" aria-label="${escapeHtml(label)}" style="left:${left}%; top:${top}%;">
      <span class="pin-marker" aria-hidden="true"></span>
      <span class="pin-tooltip" role="tooltip">
        <strong>${escapeHtml(L(pin, "name") || pin.lat_name)}</strong>
        <small>${escapeHtml([pin.lat_name, tf("side", pin.side, pin.side), pin.view].filter(Boolean).join(" · "))}</small>
        <span><b>Electrode:</b> ${escapeHtml(L(pin, "placement") || "No placement stored in KB")}</span>
        <span><b>Check:</b> ${escapeHtml(L(pin, "action") || "No functional check stored in KB")}</span>
        ${renderPinMontage(L(pin, "electrode_montage"))}
        <span><b>Linked:</b> ${escapeHtml(context)}</span>
        <span><b>Map:</b> ${escapeHtml(tf("coord", pin.coordinate_status, pin.coordinate_status || "mapped"))} · ${escapeHtml(pin.coordinate_system || "percentage")} ${escapeHtml(coordinateText)}</span>
        <em>${escapeHtml(pin.coordinate_source || "coordinate source not stored")}</em>
      </span>
    </button>
  `;
}

function renderPinMontage(montage) {
  if (!montage || typeof montage !== "object") return "";
  const rows = [];
  if (montage.active) rows.push(`<span class="pin-montage-line">${escapeHtml(montage.active)}</span>`);
  if (montage.reference) rows.push(`<span class="pin-montage-line">${escapeHtml(montage.reference)}</span>`);
  if (montage.stimulus) rows.push(`<span class="pin-montage-line"><b>Stim:</b> ${escapeHtml(montage.stimulus)}</span>`);
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
  teleNotifyWorkspace({ map_view: viewKey });
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

async function createCase() {
  const title = document.getElementById("case-title").value.trim();
  const nonIdentifyingRef = document.getElementById("case-ref").value.trim();
  const output = document.getElementById("case-result");
  if (!title) {
    output.textContent = t("app.need_case_title");
    return;
  }
  const created = await api("/api/cases", {
    method: "POST",
    body: JSON.stringify({
      title,
      non_identifying_ref: nonIdentifyingRef || null,
      selected_node_keys: selectedNodeKey ? [selectedNodeKey] : [],
      procedure_key: selectedNodeKey,
    }),
  });
  currentCaseId = created.case_id;
  output.textContent = t("app.case_created", { id: created.case_id });
  renderCase(created);
}

async function loadCase(caseId) {
  const output = document.getElementById("case-result");
  output.textContent = t("app.loading_case");
  const caseData = await api(`/api/cases/${encodeURIComponent(caseId)}`);
  renderCase(caseData);
  output.textContent = startedFromCommandCenter ? t("app.case_loaded_cc") : t("app.case_loaded");
}

async function createCaseEvent() {
  const output = document.getElementById("case-result");
  const eventType = document.getElementById("event-type").value;
  const title = document.getElementById("event-title").value.trim();
  const description = document.getElementById("event-description").value.trim();
  if (!currentCaseId) {
    output.textContent = t("app.create_case_first");
    return;
  }
  if (!title) {
    output.textContent = t("app.need_event_title");
    return;
  }
  await api(`/api/cases/${encodeURIComponent(currentCaseId)}/events`, {
    method: "POST",
    body: JSON.stringify({
      event_type: eventType,
      title,
      description: description || null,
      node_key: selectedNodeKey,
      metadata: selectedPlanner?.clinical_plan ? { readiness: selectedPlanner.clinical_plan.readiness.state } : {},
    }),
  });
  document.getElementById("event-title").value = "";
  document.getElementById("event-description").value = "";
  const updatedCase = await api(`/api/cases/${encodeURIComponent(currentCaseId)}`);
  clearDebriefOutput();
  renderCase(updatedCase);
}

function renderCase(caseData) {
  currentCaseId = caseData.case_id;
  currentCaseData = caseData;
  currentCaseEvents = caseData.events || [];
  currentCaseDebriefs = caseData.debriefs || [];
  activeCase.classList.remove("hidden");
  activeCase.innerHTML = `
    <strong>${escapeHtml(caseData.title)}</strong>
    <span>${escapeHtml(caseData.non_identifying_ref || t("app.no_ref"))}</span>
    <small>${escapeHtml(caseData.case_id)}</small>
  `;
  createEventButton.disabled = false;
  if (runDebriefButton) runDebriefButton.disabled = false;
  renderTimeline(currentCaseEvents);
  renderDebriefReadyState();
  renderDebriefHistory();
  renderCopilotReadyState();
  refreshArchiveSealAvailability();
}

function renderTimeline(events) {
  caseTimeline.className = `timeline${events.length ? "" : " empty-state"}`;
  caseTimeline.innerHTML = events.map(renderTimelineEvent).join("") || t("app.no_events");
}

function renderTimelineEvent(event) {
  return `
    <div class="timeline-event">
      <div>
        <strong>${escapeHtml(event.title)}</strong>
        <span>${escapeHtml(tf("evt", event.event_type, event.event_type))}</span>
      </div>
      <p>${escapeHtml(event.description || "")}</p>
    </div>
  `;
}

async function runCaseDebrief() {
  if (!currentCaseId) {
    renderDebriefReadyState();
    return;
  }
  const nodeKey = selectedNodeKey && selectedNodeKey !== "live"
    ? selectedNodeKey
    : currentCaseData?.procedure_key || currentCaseData?.selected_node_keys?.[0] || null;
  runDebriefButton.disabled = true;
  caseDebriefOutput.textContent = "DeepSeek is reviewing the case timeline, planner and evidence...";
  try {
    const result = await api("/api/ai/case-debrief", {
      method: "POST",
      body: JSON.stringify({
        case_id: currentCaseId,
        node_key: nodeKey,
        focus: currentCaseEvents.length ? "post_case" : "timeline",
      }),
    });
    caseDebriefOutput.innerHTML = renderDebriefResult(result);
    if (result.saved_debrief?.debrief_id) {
      currentCaseDebriefs = [
        result.saved_debrief,
        ...currentCaseDebriefs.filter((item) => item.debrief_id !== result.saved_debrief.debrief_id),
      ].slice(0, 10);
      if (currentCaseData) currentCaseData.debriefs = currentCaseDebriefs;
      renderDebriefHistory(result.saved_debrief.debrief_id);
    }
  } finally {
    runDebriefButton.disabled = false;
  }
}

function clearDebriefOutput() {
  if (!caseDebriefOutput) return;
  caseDebriefOutput.className = "debrief-output small-output";
  caseDebriefOutput.innerHTML = "";
}

function renderDebriefReadyState() {
  if (!caseDebriefOutput || caseDebriefOutput.querySelector(".debrief-result")) return;
  if (!currentCaseId) {
    caseDebriefOutput.textContent = t("live.debrief_empty");
    return;
  }
  const eventText = currentCaseEvents.length === 1 ? "1 timeline event" : `${currentCaseEvents.length} timeline events`;
  caseDebriefOutput.textContent = `Ready to generate case debrief from ${eventText}.`;
}

function renderDebriefResult(result) {
  const debrief = result.debrief || {};
  const retrieval = result.retrieval || { status: "unknown", items: [] };
  return `
    <div class="debrief-result ${escapeHtml(debrief.confidence || "low")}">
      <div class="debrief-summary">
        <span>case debrief · ${escapeHtml(debrief.confidence || "low")} confidence</span>
        <strong>${escapeHtml(debrief.case_summary || "No case summary returned.")}</strong>
      </div>
      ${renderCopilotSection("Timeline Replay", debrief.timeline_replay || [], renderDebriefReplay)}
      ${renderCopilotSection("Decision Points", debrief.decision_points || [], renderDebriefDecision)}
      ${renderCopilotSection("Documentation Gaps", debrief.documentation_gaps || [], renderStringItem)}
      ${renderCopilotSection("Academy Learning", debrief.academy_learning_points || [], renderDebriefLearning)}
      ${renderCopilotSection("Follow Up", debrief.follow_up || [], renderStringItem)}
      ${renderCopilotSection("Evidence Used", debrief.evidence_used || [], renderCopilotEvidence)}
      <div class="copilot-caution">${escapeHtml(debrief.clinical_caution || "Clinical judgement required.")}</div>
      <div class="ai-evidence">
        <strong>Retrieved evidence · ${escapeHtml(retrieval.status || "unknown")}</strong>
        ${(retrieval.items || []).map(renderEvidenceItem).join("") || `<span>No indexed evidence chunks returned.</span>`}
      </div>
    </div>
  `;
}

function renderDebriefHistory(activeDebriefId = null) {
  if (!caseDebriefHistory) return;
  if (!currentCaseId) {
    caseDebriefHistory.innerHTML = "";
    return;
  }
  caseDebriefHistory.innerHTML = `
    <div class="debrief-history-title">
      <span>Debrief History</span>
      <small>${currentCaseDebriefs.length}</small>
    </div>
    <div class="debrief-history-list">
      ${currentCaseDebriefs.map((item) => renderDebriefHistoryItem(item, activeDebriefId)).join("") || `<div class="empty-state compact">No saved debriefs yet.</div>`}
    </div>
  `;
  caseDebriefHistory.querySelectorAll(".debrief-history-item").forEach((button) => {
    button.addEventListener("click", () => openSavedDebrief(button.dataset.debriefId));
  });
}

function renderDebriefHistoryItem(item, activeDebriefId) {
  const debrief = item.debrief || {};
  const created = formatDateTime(item.created_at);
  const isActive = activeDebriefId && item.debrief_id === activeDebriefId;
  return `
    <button class="debrief-history-item${isActive ? " active" : ""}" type="button" data-debrief-id="${escapeHtml(item.debrief_id)}">
      <strong>${escapeHtml(debrief.case_summary || "Saved debrief")}</strong>
      <span>${escapeHtml(created)} · ${escapeHtml(debrief.confidence || "unknown")} · ${escapeHtml(item.timeline_event_count ?? 0)} events</span>
    </button>
  `;
}

function openSavedDebrief(debriefId) {
  const snapshot = currentCaseDebriefs.find((item) => item.debrief_id === debriefId);
  if (!snapshot) return;
  caseDebriefOutput.innerHTML = renderDebriefResult({
    focus: snapshot.focus,
    node_key: snapshot.node_key,
    case_id: snapshot.case_id,
    debrief: snapshot.debrief,
    retrieval: snapshot.retrieval,
  });
  renderDebriefHistory(snapshot.debrief_id);
}

function renderDebriefReplay(item) {
  return `
    <div class="copilot-item ${escapeHtml(item.risk_level || "unknown")}">
      <strong>${escapeHtml(item.event)}</strong>
      <span>${escapeHtml(item.interpretation)}</span>
      <small>${escapeHtml(item.risk_level || "unknown")}</small>
    </div>
  `;
}

function renderDebriefDecision(item) {
  return `
    <div class="copilot-item next">
      <strong>${escapeHtml(item.moment)}</strong>
      <span>${escapeHtml(item.recommended_action)}</span>
      <small>${escapeHtml(item.rationale)}</small>
    </div>
  `;
}

function renderDebriefLearning(item) {
  return `
    <div class="copilot-item evidence">
      <strong>${escapeHtml(item.topic)}</strong>
      <span>${escapeHtml(item.why_it_matters)}</span>
      <small>${escapeHtml(item.practice_prompt)}</small>
    </div>
  `;
}

async function askAi() {
  const question = document.getElementById("ai-question").value.trim();
  const output = document.getElementById("ai-answer");
  if (!question) {
    output.textContent = t("app.ask_question_first");
    return;
  }
  output.textContent = t("app.deepseek_thinking");
  const result = await api("/api/ai/ask", {
    method: "POST",
    body: JSON.stringify({ question, node_key: selectedNodeKey, case_id: currentCaseId }),
  });
  output.innerHTML = renderAiResult(result);
}

async function runClinicalCopilot() {
  if (!selectedNodeKey || selectedNodeKey === "live") {
    copilotOutput.textContent = "Select a clinical node before running Copilot.";
    return;
  }
  const observation = copilotObservation.value.trim();
  runCopilotButton.disabled = true;
  copilotOutput.textContent = "Clinical Copilot is reasoning with planner, case timeline and evidence...";
  try {
    const result = await api("/api/ai/clinical-copilot", {
      method: "POST",
      body: JSON.stringify({
        node_key: selectedNodeKey,
        case_id: currentCaseId,
        focus: selectedCopilotFocus,
        observation: observation || null,
      }),
    });
    copilotOutput.innerHTML = renderCopilotResult(result);
  } finally {
    runCopilotButton.disabled = false;
  }
}

function renderCopilotReadyState() {
  if (!copilotOutput || copilotOutput.querySelector(".copilot-result")) return;
  if (!selectedNodeKey || selectedNodeKey === "live") {
    copilotOutput.textContent = t("live.copilot_empty");
    return;
  }
  const caseText = currentCaseId ? "active case timeline" : "selected node only";
  copilotOutput.textContent = `Ready for ${selectedCopilotFocus.replace("_", " ")} brief using ${caseText}.`;
}

function renderCopilotResult(result) {
  const copilot = result.copilot || {};
  const retrieval = result.retrieval || { status: "unknown", items: [] };
  return `
    <div class="copilot-result ${escapeHtml(copilot.confidence || "low")}">
      <div class="copilot-summary">
        <span>${escapeHtml(result.focus || selectedCopilotFocus)} · ${escapeHtml(copilot.confidence || "low")} confidence</span>
        <strong>${escapeHtml(copilot.summary || "No summary returned.")}</strong>
      </div>
      ${renderCopilotSection("Next Checks", copilot.next_checks || [], renderCopilotCheck)}
      ${renderCopilotSection("Surgeon Communication", copilot.surgeon_communication || [], renderCopilotCommunication)}
      ${renderCopilotSection("Missing Data", copilot.missing_data || [], renderStringItem)}
      ${renderCopilotSection("Watch Items", copilot.watch_items || [], renderStringItem)}
      ${renderCopilotSection("Evidence Used", copilot.evidence_used || [], renderCopilotEvidence)}
      <div class="copilot-caution">${escapeHtml(copilot.clinical_caution || "Clinical judgement required.")}</div>
      <div class="ai-evidence">
        <strong>Retrieved evidence · ${escapeHtml(retrieval.status || "unknown")}</strong>
        ${(retrieval.items || []).map(renderEvidenceItem).join("") || `<span>No indexed evidence chunks returned.</span>`}
      </div>
    </div>
  `;
}

function renderCopilotSection(title, items, renderer) {
  return `
    <section class="copilot-section">
      <div class="section-heading compact-heading">
        <h3>${escapeHtml(title)}</h3>
        <span>${items.length}</span>
      </div>
      <div class="copilot-list">
        ${items.map(renderer).join("") || `<div class="empty-state compact">No items returned.</div>`}
      </div>
    </section>
  `;
}

function renderCopilotCheck(item) {
  return `
    <div class="copilot-item ${escapeHtml(item.priority || "next")}">
      <strong>${escapeHtml(item.action)}</strong>
      <span>${escapeHtml(item.rationale)}</span>
      <small>${escapeHtml(item.priority)}</small>
    </div>
  `;
}

function renderCopilotCommunication(item) {
  return `
    <div class="copilot-item">
      <strong>${escapeHtml(item.when)}</strong>
      <span>${escapeHtml(item.message)}</span>
    </div>
  `;
}

function renderCopilotEvidence(item) {
  return `
    <div class="copilot-item evidence">
      <strong>${escapeHtml(item.title)}</strong>
      <span>${escapeHtml(item.why_it_matters)}</span>
    </div>
  `;
}

function renderStringItem(item) {
  return `<div class="copilot-item"><span>${escapeHtml(item)}</span></div>`;
}

function bindCopilotControls() {
  document.querySelectorAll(".copilot-mode").forEach((button) => {
    button.addEventListener("click", () => {
      selectedCopilotFocus = button.dataset.copilotFocus || "setup";
      document.querySelectorAll(".copilot-mode").forEach((item) => item.classList.toggle("active", item === button));
      if (copilotOutput) {
        copilotOutput.className = "copilot-output small-output";
        copilotOutput.innerHTML = "";
      }
      renderCopilotReadyState();
    });
  });
  runCopilotButton?.addEventListener("click", () => runClinicalCopilot().catch((error) => {
    runCopilotButton.disabled = false;
    copilotOutput.textContent = error.message;
  }));
}

function renderAssistantPrompts() {
  const question = document.getElementById("ai-question");
  const assistantCard = question?.closest(".ai-card");
  if (!assistantCard || !question || assistantCard.querySelector(".assistant-prompts")) return;
  const promptBar = document.createElement("div");
  promptBar.className = "assistant-prompts";
  promptBar.innerHTML = `
    <button type="button" data-prompt="Give me the first clinical checks for this selected node and active case before accepting baseline.">Setup checks</button>
    <button type="button" data-prompt="If a significant signal change occurs now, what should I check first and what should I tell the surgeon?">Signal change</button>
    <button type="button" data-prompt="Explain why these muscles, roots or cranial nerves are selected, using available evidence.">Evidence why</button>
  `;
  assistantCard.insertBefore(promptBar, question);
  promptBar.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      question.value = button.dataset.prompt;
      question.focus();
    });
  });
}

function renderAiResult(result) {
  const answer = escapeHtml(result.answer || t("app.no_answer"));
  const retrieval = result.retrieval || { status: "unknown", items: [] };
  const items = retrieval.items || [];
  return `
    <div class="ai-response">${answer}</div>
    <div class="ai-evidence">
      <strong>${t("app.knowledge_evidence", { status: escapeHtml(retrieval.status || "unknown") })}</strong>
      ${items.map(renderEvidenceItem).join("") || `<span>${t("app.no_evidence_chunks")}</span>`}
    </div>
  `;
}

function renderEvidenceItem(item) {
  const source = [item.source_collection, item.source_key].filter(Boolean).join(" · ");
  return `
    <div class="evidence-item">
      <span>${escapeHtml(item.title || item.source_key || "Knowledge chunk")}</span>
      <small>${escapeHtml(source)}${item.score ? ` · ${escapeHtml(item.score)}` : ""}</small>
    </div>
  `;
}

function formatDateTime(value) {
  if (!value) return "unknown time";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("el-GR", { dateStyle: "short", timeStyle: "short" });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

let lastSignalAlert = null;
let lastImportedRecording = null;

// Interoperability moat — canonical channel ontology + sealed archive.
let ontologyVocabulary = null;
let lastResolvedChannels = [];
const confirmedChannelMap = new Map(); // alias_key → { raw_label, mapping }

function signalEl(id) {
  return document.getElementById(id);
}

function readAlertInputs() {
  const number = (id) => {
    const value = signalEl(id).value;
    return value === "" ? null : Number(value);
  };
  const baseline = {};
  const current = {};
  const baselineAmp = number("alert-baseline-amp");
  if (baselineAmp !== null) baseline.amplitude_uv = baselineAmp;
  const currentAmp = number("alert-current-amp");
  if (currentAmp !== null) current.amplitude_uv = currentAmp;
  const baselineLat = number("alert-baseline-lat");
  if (baselineLat !== null) baseline.latency_ms = baselineLat;
  const currentLat = number("alert-current-lat");
  if (currentLat !== null) current.latency_ms = currentLat;
  const threshold = number("alert-threshold");
  if (threshold !== null) current.threshold_ma = threshold;

  const signature = {};
  const laterality = signalEl("alert-laterality").value;
  if (laterality) signature.laterality = laterality;
  const onset = signalEl("alert-onset").value;
  if (onset) signature.onset = onset;
  const scope = signalEl("alert-scope").value;
  if (scope) signature.scope = scope;
  if (signalEl("alert-after-maneuver").checked) signature.after_maneuver = true;

  const channel = signalEl("alert-channel").value.trim();
  return {
    family: signalEl("alert-family").value,
    channel_label: channel || null,
    baseline: Object.keys(baseline).length ? baseline : null,
    current: Object.keys(current).length ? current : null,
    signature: Object.keys(signature).length ? signature : null,
    node_key: selectedNodeKey && selectedNodeKey !== "live" ? selectedNodeKey : null,
    case_id: currentCaseId,
  };
}

async function runSignalAnalyze() {
  const payload = readAlertInputs();
  const output = signalEl("alert-output");
  if (!payload.baseline && !payload.current) {
    output.textContent = t("app.need_baseline_current");
    return;
  }
  output.textContent = t("app.evaluating");
  try {
    const result = await api("/api/signals/analyze", { method: "POST", body: JSON.stringify(payload) });
    lastSignalAlert = result;
    output.innerHTML = renderAlertResult(result, null);
    signalEl("alert-log-btn").disabled = !currentCaseId;
  } catch (error) {
    output.textContent = error.message;
  }
}

async function runSignalProposals() {
  const payload = readAlertInputs();
  const output = signalEl("alert-output");
  if (!payload.baseline && !payload.current) {
    output.textContent = t("app.need_baseline_proposals");
    return;
  }
  output.innerHTML = `<div class="alert-busy">${t("app.proposals_reasoning")}</div>`;
  const button = signalEl("alert-proposals-btn");
  button.disabled = true;
  try {
    const result = await api("/api/signals/ai-proposals", { method: "POST", body: JSON.stringify(payload) });
    lastSignalAlert = result.alert;
    output.innerHTML = renderAlertResult(result.alert, result);
    signalEl("alert-log-btn").disabled = !currentCaseId;
  } catch (error) {
    output.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function renderAlertResult(alert, proposalResult) {
  const severity = alert.severity || "none";
  const protocol = alert.response_protocol || {};
  const metrics = alert.metrics || {};
  const metricRows = Object.entries(metrics)
    .map(([key, value]) => `<span class="alert-metric"><b>${escapeHtml(humanMetric(key))}</b> ${escapeHtml(value)}</span>`)
    .join("") || `<span class="alert-metric">${t("app.no_quant_metric")}</span>`;
  const criteria = (alert.criteria || []).map(renderAlertCriterion).join("");
  const immediate = (protocol.immediate_actions || []).map((action) => `<li>${escapeHtml(action)}</li>`).join("");
  const differential = (protocol.differential || []).map(renderDifferentialCategory).join("");
  const proposals = proposalResult ? renderAlertProposals(proposalResult) : "";
  return `
    <div class="alert-result severity-${escapeHtml(severity)}">
      <div class="alert-head">
        <span class="alert-badge severity-${escapeHtml(severity)}">${escapeHtml(severityLabel(severity))}</span>
        <strong>${escapeHtml(alert.summary || "")}</strong>
      </div>
      <div class="alert-metrics">${metricRows}</div>
      <section class="alert-section">
        <h3>${t("app.criteria")}</h3>
        <div class="alert-criteria">${criteria || `<span class="empty-state compact">${t("app.no_criteria_assessed")}</span>`}</div>
      </section>
      <section class="alert-section">
        <h3>${t("app.immediate_actions")}</h3>
        <ul class="alert-immediate">${immediate}</ul>
      </section>
      <section class="alert-section">
        <h3>${t("app.differential_protocol")}</h3>
        <p class="alert-rationale">${escapeHtml(protocol.ordering_rationale || "")}</p>
        ${differential}
      </section>
      <div class="alert-citation">${escapeHtml(alert.citation || "")}</div>
      ${proposals}
    </div>
  `;
}

function renderAlertCriterion(item) {
  if (item.status === "not_assessed") {
    return `<div class="alert-criterion na"><b>${escapeHtml(item.label)}</b><small>not assessed</small></div>`;
  }
  const target = item.comparison === "below"
    ? `≤ ${escapeHtml(item.warning_at)}${escapeHtml(item.unit)}`
    : `≥ ${escapeHtml(item.warning_at)}${escapeHtml(item.unit)}`;
  return `
    <div class="alert-criterion ${escapeHtml(item.severity)}">
      <b>${escapeHtml(item.label)}</b>
      <span>${escapeHtml(item.value)}${escapeHtml(item.unit)}</span>
      <small>warning ${target} · ${escapeHtml(item.status)}</small>
    </div>
  `;
}

function renderDifferentialCategory(category) {
  const items = (category.items || []).map((item) => `
    <div class="diff-item">
      <strong>${escapeHtml(item.check)}</strong>
      <span>${escapeHtml(item.action)}</span>
      <small>${escapeHtml(item.distinguishing)}</small>
    </div>
  `).join("");
  return `
    <details class="diff-category">
      <summary><span>${escapeHtml(category.label)}</span><em>${escapeHtml(category.typical_signature)}</em></summary>
      ${items}
    </details>
  `;
}

function renderAlertProposals(result) {
  const proposals = result.proposals || {};
  const retrieval = result.retrieval || { status: "unknown", items: [] };
  return `
    <section class="alert-proposals ${escapeHtml(proposals.confidence || "low")}">
      <div class="section-heading compact-heading">
        <h3>AI proposals</h3>
        <span>${escapeHtml(proposals.confidence || "low")} confidence</span>
      </div>
      <div class="copilot-summary"><strong>${escapeHtml(proposals.summary || "")}</strong></div>
      ${renderCopilotSection("Next Checks", proposals.next_checks || [], renderCopilotCheck)}
      ${renderCopilotSection("Surgeon Communication", proposals.surgeon_communication || [], renderCopilotCommunication)}
      ${renderCopilotSection("Missing Data", proposals.missing_data || [], renderStringItem)}
      ${renderCopilotSection("Watch Items", proposals.watch_items || [], renderStringItem)}
      ${renderCopilotSection("Evidence Used", proposals.evidence_used || [], renderCopilotEvidence)}
      <div class="copilot-caution">${escapeHtml(proposals.clinical_caution || "")}</div>
      <div class="ai-evidence">
        <strong>Retrieved evidence · ${escapeHtml(retrieval.status || "unknown")}</strong>
        ${(retrieval.items || []).map(renderEvidenceItem).join("") || `<span>No indexed evidence chunks returned.</span>`}
      </div>
    </section>
  `;
}

function humanMetric(key) {
  const keyMap = {
    amplitude_drop_percent: "metric.amplitude_drop",
    latency_increase_percent: "metric.latency_increase",
    wave_v_latency_increase_ms: "metric.wave_v",
    threshold_ma: "metric.threshold",
    mep_threshold_increase_v: "metric.mep_threshold",
    neurotonic_train_seconds: "metric.neurotonic",
  };
  return keyMap[key] ? t(keyMap[key]) : key;
}

function severityLabel(severity) {
  return tf("severity", severity, severity);
}

async function importRecording() {
  const input = signalEl("signal-file");
  const output = signalEl("signal-import-output");
  const file = input.files && input.files[0];
  if (!file) {
    output.textContent = t("app.pick_edf");
    return;
  }
  output.textContent = t("app.importing", { name: file.name });
  const button = signalEl("import-signal-btn");
  button.disabled = true;
  try {
    const form = new FormData();
    form.append("file", file);
    const response = await fetch("/api/signals/import", { method: "POST", body: form });
    if (!response.ok) throw new Error((await response.text()) || response.statusText);
    const result = await response.json();
    lastImportedRecording = result.recording;
    output.innerHTML = renderImportResult(result);
    bindChannelUseButtons();
    await resolveImportedChannels();
  } catch (error) {
    output.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function renderImportResult(result) {
  const recording = result.recording || {};
  const channels = recording.channels || [];
  const rows = channels.map((channel, index) => `
    <tr>
      <td>${escapeHtml(channel.label)}</td>
      <td>${escapeHtml(channel.dimension)}</td>
      <td>${escapeHtml(channel.sample_rate_hz)} Hz</td>
      <td>${escapeHtml(formatPeakToPeak(channel.baseline_pp_amplitude_uv ?? channel.baseline_pp_amplitude))}</td>
      <td>${escapeHtml(formatPeakToPeak(channel.latest_pp_amplitude_uv ?? channel.latest_pp_amplitude))}</td>
      <td><button type="button" class="link-btn" data-channel-index="${index}">${t("app.use")}</button></td>
    </tr>
  `).join("");
  const annotations = (recording.annotations || []).slice(0, 8)
    .map((annotation) => `<li>${escapeHtml(annotation.onset_s)}s — ${escapeHtml(annotation.text)}</li>`)
    .join("");
  return `
    <div class="import-result">
      <div class="import-meta">
        <span><b>${escapeHtml(result.filename || "recording")}</b></span>
        <span>${escapeHtml(recording.format)} · ${escapeHtml(recording.signal_count)} ch · ${escapeHtml(recording.duration_s)}s · ${escapeHtml(recording.record_count)} records</span>
      </div>
      <table class="import-table">
        <thead><tr><th>${t("app.col_channel")}</th><th>${t("app.col_unit")}</th><th>${t("app.col_rate")}</th><th>${t("app.col_baseline_pp")}</th><th>${t("app.col_latest_pp")}</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      ${annotations ? `<div class="import-annotations"><strong>${t("app.annotations")}</strong><ul>${annotations}</ul></div>` : ""}
      <p class="card-hint">${t("app.use_hint")}</p>
    </div>
  `;
}

function formatPeakToPeak(value) {
  return value === null || value === undefined ? "—" : `${value} µV`;
}

function bindChannelUseButtons() {
  signalEl("signal-import-output").querySelectorAll("[data-channel-index]").forEach((button) => {
    button.addEventListener("click", () => useChannelInAlert(Number(button.dataset.channelIndex)));
  });
}

function useChannelInAlert(index) {
  if (!lastImportedRecording) return;
  const channel = (lastImportedRecording.channels || [])[index];
  if (!channel) return;
  const baseline = channel.baseline_pp_amplitude_uv ?? channel.baseline_pp_amplitude;
  const latest = channel.latest_pp_amplitude_uv ?? channel.latest_pp_amplitude;
  if (baseline !== null && baseline !== undefined) signalEl("alert-baseline-amp").value = baseline;
  if (latest !== null && latest !== undefined) signalEl("alert-current-amp").value = latest;
  signalEl("alert-channel").value = channel.label || "";
  signalEl("alert-output").textContent = t("app.channel_loaded", { label: channel.label });
  document.getElementById("alert-response-card")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function logAlertToTimeline() {
  if (!currentCaseId || !lastSignalAlert) return;
  const alert = lastSignalAlert;
  const output = signalEl("alert-output");
  const button = signalEl("alert-log-btn");
  button.disabled = true;
  try {
    await api(`/api/cases/${currentCaseId}/events`, {
      method: "POST",
      body: JSON.stringify({
        event_type: "signal_change",
        title: `${severityLabel(alert.severity)} — ${alert.group_label}${alert.channel_label ? ` (${alert.channel_label})` : ""}`,
        description: alert.summary,
        node_key: selectedNodeKey && selectedNodeKey !== "live" ? selectedNodeKey : null,
        metadata: {
          alert_engine: {
            severity: alert.severity,
            fired_criteria: alert.fired_criteria,
            metrics: alert.metrics,
            group: alert.group,
          },
        },
      }),
    });
    await loadCase(currentCaseId);
    output.insertAdjacentHTML("beforeend", `<div class="alert-logged">${t("app.logged_timeline")}</div>`);
  } catch (error) {
    output.insertAdjacentHTML("beforeend", `<div class="alert-logged error">${escapeHtml(error.message)}</div>`);
  } finally {
    button.disabled = false;
  }
}

function bindSignalControls() {
  signalEl("import-signal-btn")?.addEventListener("click", () => importRecording());
  signalEl("alert-evaluate-btn")?.addEventListener("click", () => runSignalAnalyze());
  signalEl("alert-proposals-btn")?.addEventListener("click", () => runSignalProposals());
  signalEl("alert-log-btn")?.addEventListener("click", () => logAlertToTimeline());
}

// ---------------------------------------------------------------------------
// Interoperability moat — canonical channel ontology + sealed case archive
// AI/heuristic PROPOSES (advisory); the human CONFIRMS (authoritative, sealed).
// ---------------------------------------------------------------------------
async function ensureOntologyVocabulary() {
  if (ontologyVocabulary) return ontologyVocabulary;
  ontologyVocabulary = await api("/api/ontology/vocabulary");
  return ontologyVocabulary;
}

async function resolveImportedChannels() {
  const host = document.getElementById("ontology-channels");
  if (!host) return;
  const channels = (lastImportedRecording && lastImportedRecording.channels) || [];
  if (!channels.length) {
    host.textContent = t("app.no_channels_recording");
    return;
  }
  host.textContent = t("app.mapping_channels");
  try {
    await ensureOntologyVocabulary();
    const payload = channels.map((channel) => ({
      index: channel.index,
      label: channel.label,
      baseline_pp_amplitude_uv: channel.baseline_pp_amplitude_uv ?? channel.baseline_pp_amplitude ?? null,
      latest_pp_amplitude_uv: channel.latest_pp_amplitude_uv ?? channel.latest_pp_amplitude ?? null,
    }));
    const result = await api("/api/ontology/resolve-channels", {
      method: "POST",
      body: JSON.stringify({ channels: payload }),
    });
    lastResolvedChannels = result.channels || [];
    renderChannelMapping(result);
  } catch (error) {
    host.textContent = error.message;
  }
}

function renderChannelMapping(result) {
  const host = document.getElementById("ontology-channels");
  if (!host) return;
  const channels = result.channels || [];
  if (!channels.length) {
    host.textContent = t("app.no_channels_to_map");
    return;
  }
  const resolvedCount = channels.filter((item) => isChannelResolved(item)).length;
  const summary = `
    <div class="onto-summary">
      <span><b>${escapeHtml(result.total ?? channels.length)}</b> ${t("app.channels_unit")}</span>
      <span class="onto-resolved"><b>${escapeHtml(resolvedCount)}</b> ${t("app.canonical_unit")}</span>
      <span class="onto-pending"><b>${escapeHtml(result.pending_confirmation ?? (channels.length - resolvedCount))}</b> ${t("app.pending_unit")}</span>
    </div>`;
  host.innerHTML = summary + channels.map((item, index) => renderChannelRow(item, index)).join("");
  bindChannelMappingRows();
}

function isChannelResolved(item) {
  const resolution = item.resolution || {};
  return resolution.source === "learned" && resolution.resolved;
}

function renderChannelRow(item, index) {
  const resolution = item.resolution || {};
  const mapping = resolution.mapping || null;
  const source = resolution.source || "heuristic";
  const learned = source === "learned" && resolution.resolved;
  const unresolved = !mapping;
  const rowClass = learned ? "resolved-learned" : unresolved ? "unresolved" : "resolved-heuristic";
  const sourceClass = learned ? "learned" : unresolved ? "unresolved" : "heuristic";
  const sourceLabel = learned ? t("app.src_learned") : unresolved ? t("app.src_unmatched") : t("app.src_ai_proposal");
  const confidence = typeof resolution.confidence === "number"
    ? `${Math.round(resolution.confidence * 100)}%`
    : "—";
  const proposalLine = mapping
    ? `<div class="onto-proposal">${t("app.proposed_prefix")}: <b>${escapeHtml(mapping.canonical_label)}</b> <span class="onto-confidence">${escapeHtml(mapping.canonical_code)} · confidence ${escapeHtml(confidence)}</span></div>`
    : `<div class="onto-proposal">${t("app.no_auto_proposal")}</div>`;
  const selected = {
    modality: mapping?.modality || "",
    anatomy: mapping?.anatomy || "",
    laterality: mapping?.laterality || "C",
  };
  const stateLine = learned
    ? `<span class="onto-state confirmed">${t("app.canonical_ready")}</span>`
    : `<span class="onto-state" data-onto-state>${t("app.confirm_to_seal")}</span>`;
  return `
    <div class="onto-row ${rowClass}" data-onto-row data-raw-label="${escapeHtml(item.raw_label)}" data-index="${escapeHtml(index)}">
      <div class="onto-row-head">
        <span class="onto-raw">${escapeHtml(item.raw_label || t("app.empty_label"))}</span>
        <span class="onto-source ${sourceClass}">${escapeHtml(sourceLabel)}</span>
      </div>
      ${proposalLine}
      <div class="onto-selects">
        <label>${t("app.modality")}
          <select data-onto-modality>${ontologyOptions("modalities", selected.modality, false)}</select>
        </label>
        <label>${t("app.anatomy")}
          <select data-onto-anatomy>${ontologyOptions("anatomy", selected.anatomy, true)}</select>
        </label>
        <label>${t("app.laterality")}
          <select data-onto-laterality>${ontologyOptions("lateralities", selected.laterality, false)}</select>
        </label>
      </div>
      <div class="onto-row-actions">
        <button type="button" class="secondary-btn onto-confirm-btn" data-onto-confirm>${t("app.confirm")}</button>
        ${ontologyAdminEligible() ? `<label class="onto-promote"><input type="checkbox" data-onto-promote> ${t("app.global_standard")}</label>` : ""}
        ${stateLine}
      </div>
    </div>`;
}

function ontologyOptions(kind, selectedCode, allowEmpty) {
  const list = (ontologyVocabulary && ontologyVocabulary[kind]) || [];
  const head = allowEmpty ? `<option value="">${t("app.none_option")}</option>` : "";
  return head + list
    .map((entry) => `<option value="${escapeHtml(entry.code)}"${entry.code === selectedCode ? " selected" : ""}>${escapeHtml(entry.label)}</option>`)
    .join("");
}

function ontologyAdminEligible() {
  // Promotion to the global standard is Admin-only; the server enforces it too.
  return !!(ontologyVocabulary && ontologyVocabulary.can_promote_global);
}

function bindChannelMappingRows() {
  document.querySelectorAll("[data-onto-row]").forEach((row) => {
    row.querySelector("[data-onto-confirm]")?.addEventListener("click", () => confirmChannelMapping(row));
  });
}

async function confirmChannelMapping(row) {
  const rawLabel = row.dataset.rawLabel || "";
  const modality = row.querySelector("[data-onto-modality]")?.value || "";
  const anatomy = row.querySelector("[data-onto-anatomy]")?.value || "";
  const laterality = row.querySelector("[data-onto-laterality]")?.value || "C";
  const promote = row.querySelector("[data-onto-promote]")?.checked || false;
  const state = row.querySelector("[data-onto-state]");
  const button = row.querySelector("[data-onto-confirm]");
  if (!modality) {
    if (state) {
      state.textContent = t("app.pick_modality_first");
      state.classList.add("error");
    }
    return;
  }
  if (button) button.disabled = true;
  try {
    const result = await api("/api/ontology/confirm", {
      method: "POST",
      body: JSON.stringify({
        raw_label: rawLabel,
        modality,
        anatomy: anatomy || null,
        laterality,
        promote_global: promote,
      }),
    });
    const mapping = result.mapping;
    confirmedChannelMap.set(normalizeAliasKey(rawLabel), { raw_label: rawLabel, mapping });
    row.classList.remove("unresolved", "resolved-heuristic");
    row.classList.add("resolved-learned");
    const sourceBadge = row.querySelector(".onto-source");
    if (sourceBadge) {
      sourceBadge.className = "onto-source learned";
      sourceBadge.textContent = t("app.confirmed_badge");
    }
    if (state) {
      state.classList.remove("error");
      state.classList.add("confirmed");
      state.textContent = promote && result.promoted_global
        ? t("app.confirmed_global", { label: mapping.canonical_label })
        : t("app.confirmed_label", { label: mapping.canonical_label });
    }
    refreshArchiveSealAvailability();
  } catch (error) {
    if (state) {
      state.classList.add("error");
      state.textContent = error.message;
    }
  } finally {
    if (button) button.disabled = false;
  }
}

function normalizeAliasKey(raw) {
  // Mirror of the server's normalize_label (lowercase, collapse separators/space).
  return String(raw || "")
    .trim()
    .toLowerCase()
    .replace(/[-._/\\|]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function confirmedChannelMapPayload() {
  // The channel_map that gets sealed: learned-resolved channels + human confirmations.
  const byKey = new Map();
  for (const item of lastResolvedChannels) {
    if (isChannelResolved(item) && item.resolution.mapping) {
      byKey.set(normalizeAliasKey(item.raw_label), {
        raw_label: item.raw_label,
        index: item.index,
        canonical_code: item.resolution.mapping.canonical_code,
        canonical_label: item.resolution.mapping.canonical_label,
        mapping: item.resolution.mapping,
        source: "learned",
      });
    }
  }
  for (const [key, value] of confirmedChannelMap) {
    byKey.set(key, {
      raw_label: value.raw_label,
      canonical_code: value.mapping.canonical_code,
      canonical_label: value.mapping.canonical_label,
      mapping: value.mapping,
      source: "confirmed",
    });
  }
  return [...byKey.values()];
}

// ---- Sealed case archive (immutable, hash-chained system of record) --------
function bindArchiveControls() {
  document.getElementById("archive-seal-btn")?.addEventListener("click", () => sealActiveCase());
  document.getElementById("archive-verify-btn")?.addEventListener("click", () => verifyActiveCaseChain());
  document.getElementById("archive-refresh-btn")?.addEventListener("click", () => loadSealedArchive());
}

function refreshArchiveSealAvailability() {
  const sealBtn = document.getElementById("archive-seal-btn");
  const verifyBtn = document.getElementById("archive-verify-btn");
  const output = document.getElementById("archive-output");
  const hasCase = !!currentCaseId;
  if (sealBtn) sealBtn.disabled = !hasCase;
  if (verifyBtn) verifyBtn.disabled = !hasCase;
  if (output && !hasCase) {
    output.textContent = t("live.archive_empty");
    output.dataset.sealState = "info";
  } else if (output && output.dataset.sealState !== "result") {
    const mapped = confirmedChannelMapPayload().length;
    output.textContent = mapped
      ? t("app.seal_ready", { mapped })
      : t("app.seal_ready_hint");
    output.dataset.sealState = "info";
  }
}

async function sealActiveCase() {
  const output = document.getElementById("archive-output");
  if (!currentCaseId) {
    if (output) output.textContent = t("live.no_active_case");
    return;
  }
  const button = document.getElementById("archive-seal-btn");
  if (button) button.disabled = true;
  if (output) {
    output.textContent = t("app.sealing_case");
    output.dataset.sealState = "result";
  }
  try {
    const result = await api("/api/archive/seal", {
      method: "POST",
      body: JSON.stringify({
        case_id: currentCaseId,
        channel_map: confirmedChannelMapPayload(),
        reason: null,
      }),
    });
    if (output) output.innerHTML = renderSealResult(result);
    await loadSealedArchive();
  } catch (error) {
    if (output) output.textContent = error.message;
  } finally {
    if (button) button.disabled = false;
  }
}

function renderSealResult(result) {
  const seal = result.seal || {};
  const created = result.created !== false;
  const code = seal.content_hash || result.content_hash || "";
  const note = created
    ? t("app.sealed_new_version", { version: seal.version ?? result.version ?? 1 })
    : t("app.content_unchanged", { version: seal.version ?? 1 });
  return `
    <div class="seal-result verified">
      <div class="seal-head">
        <strong>${escapeHtml(note)}</strong>
        <span class="seal-verdict ok">${t("app.sealed_badge")}</span>
      </div>
      <div class="seal-meta">
        <span><b>${t("app.lbl_case")}</b>${escapeHtml(seal.case_id || currentCaseId)}</span>
        <span><b>${t("app.lbl_version")}</b>${escapeHtml(seal.version ?? result.version ?? 1)}</span>
        <span><b>${t("app.lbl_algo")}</b>${escapeHtml(seal.hash_algorithm || "sha256")}</span>
      </div>
      <div class="seal-hash"><b>${t("app.content_hash")}</b>${escapeHtml(code)}</div>
    </div>`;
}

async function verifyActiveCaseChain() {
  const output = document.getElementById("archive-output");
  if (!currentCaseId) return;
  const button = document.getElementById("archive-verify-btn");
  if (button) button.disabled = true;
  if (output) {
    output.textContent = t("app.verifying_integrity");
    output.dataset.sealState = "result";
  }
  try {
    const result = await api(`/api/archive/verify/${encodeURIComponent(currentCaseId)}`);
    if (output) output.innerHTML = renderVerifyResult(result);
  } catch (error) {
    if (output) output.textContent = error.message;
  } finally {
    if (button) button.disabled = false;
  }
}

function renderVerifyResult(result) {
  const ok = !!result.ok;
  const links = result.links || [];
  const linkRows = links.map((link) => `
    <span><b>v${escapeHtml(link.version)}</b>${link.ok ? t("app.link_intact") : escapeHtml(link.reason || t("app.link_tampered"))}</span>
  `).join("");
  return `
    <div class="seal-result ${ok ? "verified" : "broken"}">
      <div class="seal-head">
        <strong>${ok ? t("app.chain_intact") : t("app.chain_tampered")}</strong>
        <span class="seal-verdict ${ok ? "ok" : "fail"}">${ok ? t("app.verified_badge") : t("app.tampered_badge")}</span>
      </div>
      <div class="seal-meta">${linkRows || `<span>${t("app.no_sealed_version")}</span>`}</div>
    </div>`;
}

async function loadSealedArchive() {
  const host = document.getElementById("archive-list");
  if (!host) return;
  host.textContent = t("common.loading");
  try {
    const result = await api("/api/archive/seals");
    renderArchiveList(result.seals || []);
  } catch (error) {
    host.innerHTML = `<div class="archive-empty">${escapeHtml(error.message)}</div>`;
  }
}

function renderArchiveList(seals) {
  const host = document.getElementById("archive-list");
  if (!host) return;
  if (!seals.length) {
    host.innerHTML = `<div class="archive-empty">${t("app.no_sealed_cases")}</div>`;
    return;
  }
  host.innerHTML = seals.map(renderArchiveItem).join("");
  host.querySelectorAll("[data-verify-case]").forEach((button) => {
    button.addEventListener("click", () => verifyArchivedCase(button.dataset.verifyCase, button));
  });
}

function renderArchiveItem(seal) {
  const sealedAt = seal.sealed_at ? String(seal.sealed_at).replace("T", " ").slice(0, 19) : "—";
  return `
    <div class="archive-item" data-case="${escapeHtml(seal.case_id)}">
      <div class="archive-item-head">
        <span class="archive-item-title">${escapeHtml(seal.case_id)}</span>
        <span class="archive-chain">v${escapeHtml(seal.version)}</span>
      </div>
      <div class="archive-item-meta">${escapeHtml(seal.sealed_by || "—")} · ${escapeHtml(sealedAt)}</div>
      <div class="archive-item-meta">${t("app.hash_prefix")} ${escapeHtml((seal.content_hash || "").slice(0, 24))}…</div>
      <div class="archive-item-actions">
        <button type="button" class="link-btn" data-verify-case="${escapeHtml(seal.case_id)}">${t("app.verify_chain")}</button>
      </div>
    </div>`;
}

async function verifyArchivedCase(caseId, button) {
  if (!caseId) return;
  const container = button.closest(".archive-item");
  if (button) button.disabled = true;
  try {
    const result = await api(`/api/archive/verify/${encodeURIComponent(caseId)}`);
    const existing = container.querySelector(".seal-result");
    if (existing) existing.remove();
    container.insertAdjacentHTML("beforeend", renderVerifyResult(result));
  } catch (error) {
    container.insertAdjacentHTML("beforeend", `<div class="archive-empty">${escapeHtml(error.message)}</div>`);
  } finally {
    if (button) button.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Tele-IONM — live remote supervision over native WebSockets (no mock, real-time)
// ---------------------------------------------------------------------------
let teleSocket = null;
let teleConnectionId = null;
let teleIdentity = null;
let teleSelfEligible = false;
let teleAvailable = false;
let teleSession = null;
let teleRole = null;
let teleExperts = [];
const teleIncoming = new Map();
let teleApplyingRemote = false;
let telePointerMode = false;
let teleHeartbeatTimer = null;
let teleReconnectTimer = null;
let teleReconnectDelay = 1000;
let teleManualClose = false;

// --- WebRTC live media (Phase 2: voice + screen share) --------------------- //
let telePeer = null;                 // RTCPeerConnection (created lazily)
let teleLocalStream = null;          // MediaStream carrying our local tracks
let teleRemoteStream = null;         // MediaStream aggregating the peer's tracks
let teleIceServers = null;           // cached ICE config from the backend
let teleIcePromise = null;           // in-flight ICE config fetch (dedup)
let telePolite = false;              // perfect-negotiation role (consultant = polite)
let teleMakingOffer = false;         // guard while we create/set a local offer
let teleIgnoreOffer = false;         // glare handling for the impolite peer
let teleMicOn = false;               // is our microphone currently published
let teleMicLive = false;             // push-to-talk: are we actively transmitting now
let teleScreenOn = false;            // is our screen currently published
let teleMicTrack = null;             // active local audio track
let teleScreenTrack = null;          // active local screen-video track
let teleMicSender = null;            // RTCRtpSender for the audio track
let teleScreenSender = null;         // RTCRtpSender for the screen track
let telePeerMedia = { audio: false, screen: false }; // peer's announced state
let teleDataChannel = null;          // RTCDataChannel for trace+verdict sharing (#2)

function teleEl(id) {
  return document.getElementById(id);
}

function teleRoleLabel(role, accessLevel) {
  if (role === "admin") return t("role.admin");
  const map = { educator: "role.educator", clinician: "role.clinician", admin: "role.admin" };
  return map[accessLevel] ? t(map[accessLevel]) : t("role.expert");
}

function setTeleStatus(text, state) {
  const el = teleEl("tele-status");
  if (!el) return;
  el.textContent = text;
  el.className = `tele-status tele-status-${state}`;
}

function setTeleFeedback(text) {
  const el = teleEl("tele-feedback");
  if (el) el.textContent = text || "";
}

function teleSend(payload) {
  if (teleSocket && teleSocket.readyState === WebSocket.OPEN) {
    teleSocket.send(JSON.stringify(payload));
    return true;
  }
  return false;
}

function connectTele() {
  if (teleSocket && (teleSocket.readyState === WebSocket.OPEN || teleSocket.readyState === WebSocket.CONNECTING)) return;
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  setTeleStatus(t("live.tele_connecting"), "offline");
  try {
    teleSocket = new WebSocket(`${scheme}://${location.host}/api/tele/ws`);
  } catch (error) {
    scheduleTeleReconnect();
    return;
  }
  teleSocket.addEventListener("open", () => {
    teleReconnectDelay = 1000;
    setTeleStatus("online", "online");
    startTeleHeartbeat();
    if (teleAvailable) teleSend({ type: "set_available", available: true });
    if (selectedNodeKey) teleSend({ type: "set_node", node_key: selectedNodeKey });
    // Transport reliability (#6): if a consult was live when the socket dropped,
    // re-attach to it immediately so the clinician never loses supervision.
    if (teleSession && teleSession.status && teleSession.status !== "ended") {
      teleSend({ type: "resume", session_id: teleSession.session_id });
    }
  });
  teleSocket.addEventListener("message", (event) => {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch (error) {
      return;
    }
    handleTeleMessage(message);
  });
  teleSocket.addEventListener("close", () => {
    stopTeleHeartbeat();
    setTeleStatus("offline", "offline");
    if (!teleManualClose) scheduleTeleReconnect();
  });
  teleSocket.addEventListener("error", () => {
    try {
      teleSocket.close();
    } catch (error) {
      /* ignored */
    }
  });
}

function scheduleTeleReconnect() {
  if (teleReconnectTimer) return;
  teleReconnectTimer = setTimeout(() => {
    teleReconnectTimer = null;
    teleReconnectDelay = Math.min(teleReconnectDelay * 1.6, 15000);
    connectTele();
  }, teleReconnectDelay);
}

function startTeleHeartbeat() {
  stopTeleHeartbeat();
  teleHeartbeatTimer = setInterval(() => teleSend({ type: "heartbeat" }), 15000);
}

function stopTeleHeartbeat() {
  if (teleHeartbeatTimer) {
    clearInterval(teleHeartbeatTimer);
    teleHeartbeatTimer = null;
  }
}

function handleTeleMessage(message) {
  switch (message.type) {
    case "welcome":
      teleConnectionId = message.connection_id;
      teleIdentity = message.identity || null;
      teleSelfEligible = !!message.identity?.expert_eligible;
      if (teleSelfEligible) teleEl("tele-availability-row")?.removeAttribute("hidden");
      break;
    case "presence":
      teleExperts = Array.isArray(message.experts) ? message.experts : [];
      renderTeleExperts();
      break;
    case "availability":
      teleAvailable = !!message.available;
      if (teleEl("tele-available")) teleEl("tele-available").checked = teleAvailable;
      break;
    case "incoming_request":
      if (message.session && message.session.initiator_id !== teleIdentity?.user_id) {
        teleIncoming.set(message.session.session_id, message.session);
        renderTeleIncoming();
      }
      break;
    case "request_closed":
      if (teleIncoming.delete(message.session_id)) renderTeleIncoming();
      break;
    case "support_requested":
      teleSession = message.session;
      teleRole = "initiator";
      teleEl("tele-idle")?.classList.add("hidden");
      teleEl("tele-pending")?.classList.remove("hidden");
      setTeleFeedback("");
      break;
    case "request_accepted":
      teleSession = message.session;
      teleRole = "initiator";
      openTeleSession();
      break;
    case "session_started":
      teleSession = message.session;
      teleRole = "consultant";
      openTeleSession();
      break;
    case "request_cancelled":
      resetTeleSession(t("app.request_cancelled"));
      break;
    case "consent":
      if (teleSession) teleSession.consent_screen_share = !!message.granted;
      updateConsentState(!!message.granted, message.by);
      // Revoking consent must immediately stop any active screen share.
      if (!message.granted && teleScreenOn) {
        teleStopScreen();
        teleSendMediaState();
      }
      updateTeleMediaUI();
      break;
    case "workspace_update":
      if (message.sender?.user_id !== teleIdentity?.user_id) applyRemoteWorkspace(message.patch);
      break;
    case "chat":
      appendTeleChat(message.sender, message.text, message.created_at);
      break;
    case "annotation":
      if (message.sender?.user_id !== teleIdentity?.user_id) showTeleAnnotation(message.annotation, message.sender);
      break;
    case "webrtc_signal":
      handleWebrtcSignal(message.signal, message.from);
      break;
    case "media_state":
      if (message.from?.user_id !== teleIdentity?.user_id) {
        const wasActive = telePeerMedia.audio || telePeerMedia.screen;
        telePeerMedia = { audio: !!message.audio, screen: !!message.screen };
        const isActive = telePeerMedia.audio || telePeerMedia.screen;
        if (isActive && !wasActive) {
          appendTeleSystem(t("app.tele_peer_started", { name: message.from?.display_name || t("app.partner") }));
        } else if (!isActive && wasActive) {
          appendTeleSystem(t("app.tele_peer_stopped", { name: message.from?.display_name || t("app.partner") }));
        }
        updateTeleMediaUI();
      }
      break;
    case "participant_left":
      appendTeleSystem(t("app.participant_left", { name: message.user?.display_name || t("app.the_partner") }));
      teleHandlePeerLeft();
      break;
    case "resumable_session":
      // Fresh socket (e.g. page reload) with no in-memory session: restore it (#6).
      if (!teleSession && message.session) {
        teleSend({ type: "resume", session_id: message.session.session_id });
      }
      break;
    case "session_resumed":
      teleSession = message.session;
      teleRole = message.role === "consultant" ? "consultant" : "initiator";
      openTeleSession();
      appendTeleSystem(t("app.session_resumed"));
      break;
    case "participant_rejoined":
      if (message.user?.user_id !== teleIdentity?.user_id) {
        appendTeleSystem(t("app.participant_rejoined", { name: message.user?.display_name || t("app.the_partner") }));
        // Re-announce our media so the peer can re-establish tracks after a drop.
        if (teleMicOn || teleScreenOn) teleSendMediaState();
      }
      break;
    case "session_ended":
      handleSessionEnded(message);
      break;
    case "error":
      setTeleFeedback(message.detail || t("app.tele_error"));
      break;
    case "heartbeat_ack":
    default:
      break;
  }
}

function renderTeleExperts() {
  const host = teleEl("tele-experts");
  if (!host) return;
  if (!teleExperts.length) {
    host.innerHTML = `<div class="tele-empty">${t("app.no_experts_online")}</div>`;
  } else {
    host.innerHTML = `<div class="tele-experts-title">${t("app.experts_available", { n: teleExperts.length })}</div>` +
      teleExperts.map((expert) => `
        <div class="tele-expert">
          <span class="tele-expert-dot" aria-hidden="true"></span>
          <span class="tele-expert-name">${escapeHtml(expert.display_name)}</span>
          <span class="tele-expert-role">${escapeHtml(teleRoleLabel(expert.role, expert.access_level))}</span>
        </div>`).join("");
  }
  const button = teleEl("tele-request-btn");
  if (button) button.disabled = teleExperts.length === 0 || !!teleSession;
}

function renderTeleIncoming() {
  const host = teleEl("tele-incoming");
  if (!host) return;
  const items = [...teleIncoming.values()];
  updateTeleTabBadge();
  if (!items.length || teleSession) {
    host.innerHTML = "";
    return;
  }
  host.innerHTML = items.map((session) => `
    <div class="tele-request-card" data-session="${escapeHtml(session.session_id)}">
      <div class="tele-request-head">${t("app.request_from")} <strong>${escapeHtml(session.initiator_name)}</strong></div>
      ${session.request_message ? `<div class="tele-request-msg">${escapeHtml(session.request_message)}</div>` : ""}
      ${session.node_key ? `<div class="tele-request-node">${t("app.node_label")} ${escapeHtml(session.node_key)}</div>` : ""}
      <div class="tele-request-actions">
        <button class="primary-btn tele-accept" type="button" data-session="${escapeHtml(session.session_id)}">${t("app.accept")}</button>
        <button class="ghost-btn tele-decline" type="button" data-session="${escapeHtml(session.session_id)}">${t("app.dismiss")}</button>
      </div>
    </div>`).join("");
  host.querySelectorAll(".tele-accept").forEach((button) => {
    button.addEventListener("click", () => acceptRequest(button.dataset.session));
  });
  host.querySelectorAll(".tele-decline").forEach((button) => {
    button.addEventListener("click", () => declineRequest(button.dataset.session));
  });
}

function requestSupport() {
  if (!teleExperts.length || teleSession) return;
  const message = teleEl("tele-request-message")?.value.trim() || null;
  if (!teleSend({ type: "request_support", node_key: selectedNodeKey, case_id: currentCaseId, message })) {
    setTeleFeedback(t("app.no_connection_retry"));
  }
}

function cancelRequest() {
  if (teleSession) teleSend({ type: "cancel", session_id: teleSession.session_id });
}

function acceptRequest(sessionId) {
  if (sessionId) teleSend({ type: "accept", session_id: sessionId });
}

function declineRequest(sessionId) {
  if (!sessionId) return;
  teleSend({ type: "decline", session_id: sessionId });
  teleIncoming.delete(sessionId);
  renderTeleIncoming();
}

function openTeleSession() {
  teleEl("tele-idle")?.classList.add("hidden");
  teleEl("tele-pending")?.classList.add("hidden");
  teleIncoming.clear();
  renderTeleIncoming();
  teleEl("tele-session")?.classList.remove("hidden");
  setTeleFeedback("");
  const peerName = teleRole === "initiator" ? teleSession.consultant_name : teleSession.initiator_name;
  if (teleEl("tele-peer")) teleEl("tele-peer").textContent = peerName || t("app.partner");
  if (teleEl("tele-session-role")) teleEl("tele-session-role").textContent = teleRole === "initiator" ? t("app.you_requester") : t("app.you_expert");
  if (teleEl("tele-chat")) teleEl("tele-chat").innerHTML = "";
  appendTeleSystem(t("app.connection_active", { name: peerName || t("app.partner_acc") }));
  if (teleEl("tele-chat-input")) teleEl("tele-chat-input").disabled = false;
  if (teleEl("tele-end-btn")) teleEl("tele-end-btn").textContent = t("app.end_session");
  const consentChk = teleEl("tele-consent");
  if (consentChk) consentChk.checked = !!teleSession.consent_screen_share;
  updateConsentState(!!teleSession.consent_screen_share, null);
  if (teleEl("tele-link-case-btn")) teleEl("tele-link-case-btn").disabled = !currentCaseId;
  telePointerMode = false;
  updatePointerButton();
  // Perfect-negotiation roles: the consultant is the polite peer, the initiator
  // is impolite. This deterministically resolves simultaneous-offer glare.
  telePolite = teleRole === "consultant";
  teleInitMediaUI();
  // Push my current workspace so the peer immediately mirrors my view.
  if (selectedNodeKey) teleSend({ type: "workspace_update", session_id: teleSession.session_id, patch: { node_key: selectedNodeKey, map_view: currentMapView } });
}

function handleSessionEnded(message) {
  if (teleSession) teleSession.status = "ended";
  else teleSession = message.session || { session_id: message.session_id, status: "ended" };
  teleTeardownMedia();
  appendTeleSystem(t("app.session_ended_msg"));
  if (teleEl("tele-chat-input")) teleEl("tele-chat-input").disabled = true;
  if (teleEl("tele-end-btn")) teleEl("tele-end-btn").textContent = t("app.close");
  if (teleEl("tele-link-case-btn")) teleEl("tele-link-case-btn").disabled = !currentCaseId;
  updateTeleTabBadge();
}

function resetTeleSession(feedback) {
  teleTeardownMedia();
  teleSession = null;
  teleRole = null;
  telePointerMode = false;
  clearTeleAnnotations();
  updatePointerButton();
  teleEl("tele-session")?.classList.add("hidden");
  teleEl("tele-pending")?.classList.add("hidden");
  teleEl("tele-idle")?.classList.remove("hidden");
  if (teleEl("tele-chat-input")) teleEl("tele-chat-input").disabled = false;
  if (teleEl("tele-end-btn")) teleEl("tele-end-btn").textContent = t("app.end_session");
  if (teleEl("tele-consent")) teleEl("tele-consent").checked = false;
  renderTeleExperts();
  renderTeleIncoming();
  setTeleFeedback(feedback || "");
}

function endTeleSession() {
  if (teleSession && teleSession.status !== "ended") {
    teleSend({ type: "end", session_id: teleSession.session_id });
  } else {
    resetTeleSession();
  }
}

async function linkSessionToCase() {
  const sessionId = teleSession?.session_id;
  if (!sessionId || !currentCaseId) return;
  try {
    await api(`/api/tele/sessions/${encodeURIComponent(sessionId)}/link-case`, {
      method: "POST",
      body: JSON.stringify({ case_id: currentCaseId }),
    });
    setTeleFeedback(t("app.session_linked"));
    if (currentCaseId) await loadCase(currentCaseId);
  } catch (error) {
    setTeleFeedback(error.message);
  }
}

function setConsent(granted) {
  if (teleSession) teleSend({ type: "consent", session_id: teleSession.session_id, granted: !!granted });
}

function updateConsentState(granted, by) {
  const el = teleEl("tele-consent-state");
  if (!el) return;
  el.textContent = granted ? t("app.consent_granted") + (by ? ` (${by})` : "") : t("app.consent_not_yet");
  el.classList.toggle("granted", !!granted);
}

function setTeleAvailability(value) {
  teleAvailable = !!value;
  teleSend({ type: "set_available", available: teleAvailable });
}

function appendTeleChat(sender, text, createdAt) {
  const log = teleEl("tele-chat");
  if (!log) return;
  const mine = sender?.user_id === teleIdentity?.user_id;
  const time = createdAt ? new Date(createdAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
  const row = document.createElement("div");
  row.className = `tele-msg ${mine ? "tele-msg-self" : "tele-msg-peer"}`;
  row.innerHTML = `
    <span class="tele-msg-author">${escapeHtml(mine ? t("app.you") : (sender?.display_name || t("app.partner")))}</span>
    <span class="tele-msg-text">${escapeHtml(text)}</span>
    <span class="tele-msg-time">${escapeHtml(time)}</span>`;
  log.appendChild(row);
  log.scrollTop = log.scrollHeight;
}

function appendTeleSystem(text) {
  const log = teleEl("tele-chat");
  if (!log) return;
  const row = document.createElement("div");
  row.className = "tele-msg tele-msg-system";
  row.textContent = text;
  log.appendChild(row);
  log.scrollTop = log.scrollHeight;
}

function teleNotifyNodeChange(nodeKey) {
  if (!teleSocket || teleSocket.readyState !== WebSocket.OPEN) return;
  teleSend({ type: "set_node", node_key: nodeKey });
  if (teleSession && teleSession.status !== "ended" && !teleApplyingRemote) {
    teleSend({ type: "workspace_update", session_id: teleSession.session_id, patch: { node_key: nodeKey } });
  }
}

function teleNotifyWorkspace(patch) {
  if (!teleSession || teleSession.status === "ended" || teleApplyingRemote) return;
  if (!teleSocket || teleSocket.readyState !== WebSocket.OPEN) return;
  teleSend({ type: "workspace_update", session_id: teleSession.session_id, patch });
}

async function applyRemoteWorkspace(patch) {
  if (!patch) return;
  teleApplyingRemote = true;
  try {
    if (patch.node_key && patch.node_key !== selectedNodeKey && nodeIndex.has(patch.node_key)) {
      await selectNode(patch.node_key);
    }
    if (patch.map_view && patch.map_view !== currentMapView && mapViewDefinitions[patch.map_view]) {
      setMapView(patch.map_view);
    }
    flashSyncIndicator();
  } finally {
    teleApplyingRemote = false;
  }
}

function flashSyncIndicator() {
  const el = teleEl("tele-sync-indicator");
  if (!el) return;
  el.classList.add("synced");
  setTimeout(() => el.classList.remove("synced"), 900);
}

function togglePointerMode() {
  telePointerMode = !telePointerMode;
  updatePointerButton();
}

function updatePointerButton() {
  const button = teleEl("tele-pointer-btn");
  if (!button) return;
  button.textContent = t("app.pointer", { state: telePointerMode ? t("app.on") : t("app.off") });
  button.setAttribute("aria-pressed", telePointerMode ? "true" : "false");
  button.classList.toggle("active", telePointerMode);
  if (bodyMap) bodyMap.classList.toggle("tele-pointer-active", telePointerMode);
}

function onBodyMapClickForTele(event) {
  if (!teleSession || teleSession.status === "ended") return;
  if (!telePointerMode && !event.shiftKey) return;
  if (!bodyMap) return;
  const rect = bodyMap.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const x = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
  const y = Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height));
  const annotation = { x: Number(x.toFixed(4)), y: Number(y.toFixed(4)), kind: "point", label: null };
  showTeleAnnotation(annotation, { display_name: t("app.you"), user_id: teleIdentity?.user_id });
  teleSend({ type: "annotation", session_id: teleSession.session_id, annotation });
}

function showTeleAnnotation(annotation, sender) {
  const layer = teleEl("tele-annotations-layer");
  if (!layer || !annotation) return;
  const x = Number(annotation.x);
  const y = Number(annotation.y);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return;
  const mine = sender?.user_id === teleIdentity?.user_id;
  const dot = document.createElement("div");
  dot.className = `tele-annotation ${mine ? "tele-annotation-self" : "tele-annotation-peer"}`;
  dot.style.left = `${x * 100}%`;
  dot.style.top = `${y * 100}%`;
  const label = annotation.label || sender?.display_name;
  if (label) {
    const tag = document.createElement("span");
    tag.className = "tele-annotation-label";
    tag.textContent = label;
    dot.appendChild(tag);
  }
  layer.appendChild(dot);
  setTimeout(() => dot.remove(), 8000);
}

function clearTeleAnnotations() {
  const layer = teleEl("tele-annotations-layer");
  if (layer) layer.innerHTML = "";
}

// --------------------------------------------------------------------------- //
// WebRTC live media (Phase 2): voice + screen share over the tele signalling
// channel using the Perfect Negotiation pattern. The initiator is "impolite"
// and the consultant is "polite", which deterministically resolves offer glare.
// --------------------------------------------------------------------------- //
async function teleFetchIceServers() {
  if (teleIceServers) return teleIceServers;
  if (!teleIcePromise) {
    teleIcePromise = api("/api/tele/ice-config")
      .then((data) => {
        teleIceServers = Array.isArray(data?.ice_servers) ? data.ice_servers : [];
        return teleIceServers;
      })
      .finally(() => {
        teleIcePromise = null;
      });
  }
  return teleIcePromise;
}

function teleEnsureLocalStream() {
  if (!teleLocalStream) teleLocalStream = new MediaStream();
  return teleLocalStream;
}

function teleSignalSessionId() {
  return teleSession && teleSession.status !== "ended" ? teleSession.session_id : null;
}

async function teleEnsurePeer() {
  if (telePeer) return telePeer;
  if (!teleSession || teleSession.status === "ended") return null;
  if (typeof RTCPeerConnection === "undefined") {
    setTeleFeedback(t("app.tele_media_unsupported"));
    return null;
  }
  let iceServers;
  try {
    iceServers = await teleFetchIceServers();
  } catch (error) {
    setTeleFeedback(t("app.tele_media_unavailable"));
    return null;
  }
  // A concurrent path may have created the peer while we awaited the ICE config.
  if (telePeer) return telePeer;
  const peer = new RTCPeerConnection({ iceServers });
  telePeer = peer;

  peer.onnegotiationneeded = async () => {
    try {
      teleMakingOffer = true;
      await peer.setLocalDescription();
      const sessionId = teleSignalSessionId();
      if (sessionId && peer.localDescription) {
        teleSend({
          type: "webrtc_signal",
          session_id: sessionId,
          signal: { kind: peer.localDescription.type, sdp: peer.localDescription.sdp },
        });
      }
    } catch (error) {
      console.warn("tele negotiation failed", error);
    } finally {
      teleMakingOffer = false;
    }
  };

  peer.onicecandidate = ({ candidate }) => {
    const sessionId = teleSignalSessionId();
    if (!sessionId) return;
    teleSend({
      type: "webrtc_signal",
      session_id: sessionId,
      signal: { kind: "candidate", candidate: candidate ? candidate.toJSON() : null },
    });
  };

  peer.ontrack = (event) => {
    if (!teleRemoteStream) teleRemoteStream = new MediaStream();
    teleRemoteStream.addTrack(event.track);
    const remoteVideo = teleEl("tele-remote-video");
    if (remoteVideo && remoteVideo.srcObject !== teleRemoteStream) remoteVideo.srcObject = teleRemoteStream;
    event.track.addEventListener("ended", () => {
      try {
        teleRemoteStream?.removeTrack(event.track);
      } catch (error) {
        /* ignored */
      }
      updateTeleMediaUI();
    });
    updateTeleMediaUI();
  };

  peer.onconnectionstatechange = () => {
    if (peer.connectionState === "failed") {
      try {
        peer.restartIce();
      } catch (error) {
        /* ignored */
      }
    }
    updateTeleMediaUI();
  };

  // Trace + verdict sharing (#2): a reliable, ordered data channel carries
  // waveform measurement snapshots and the deterministic engine's verdict to the
  // peer with sub-second latency once the WebRTC connection is up. The impolite
  // peer (initiator) creates it; the polite peer (consultant) receives it.
  peer.ondatachannel = (event) => teleBindDataChannel(event.channel);
  if (!telePolite) {
    teleBindDataChannel(peer.createDataChannel("ionm-data", { ordered: true }));
  }

  return peer;
}

function teleBindDataChannel(channel) {
  if (!channel) return;
  teleDataChannel = channel;
  channel.onmessage = (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch (error) {
      return;
    }
    if (payload && payload.kind === "trace") teleRenderSharedTrace(payload);
  };
  channel.onclose = () => {
    if (teleDataChannel === channel) teleDataChannel = null;
  };
}

function teleRenderSharedTrace(payload) {
  // The peer shared a read-only snapshot of their waveform + verdict. Render it
  // verbatim from the deterministic engine result — never re-compute locally.
  const output = signalEl("alert-output");
  if (output && payload.alert) {
    output.innerHTML = renderAlertResult(payload.alert, payload.proposals || null);
    if (typeof signalEl("alert-log-btn") !== "undefined" && signalEl("alert-log-btn")) {
      signalEl("alert-log-btn").disabled = !currentCaseId;
    }
  }
  appendTeleSystem(t("app.tele_trace_shared", { name: payload.from?.display_name || t("app.partner") }));
  flashSyncIndicator();
}

function teleShareTrace() {
  if (!teleSession || teleSession.status === "ended") return;
  if (!lastSignalAlert) {
    setTeleFeedback(t("app.tele_trace_unavailable"));
    return;
  }
  if (!teleDataChannel || teleDataChannel.readyState !== "open") {
    setTeleFeedback(t("app.tele_media_unavailable"));
    return;
  }
  const payload = {
    kind: "trace",
    alert: lastSignalAlert,
    from: { user_id: teleIdentity?.user_id, display_name: teleIdentity?.display_name },
    at: new Date().toISOString(),
  };
  try {
    teleDataChannel.send(JSON.stringify(payload));
    appendTeleSystem(t("app.tele_trace_shared", { name: t("app.you") }));
    setTeleFeedback("");
  } catch (error) {
    setTeleFeedback(t("app.tele_media_unavailable"));
  }
}

async function handleWebrtcSignal(signal, from) {
  // Audience "session" echoes our own frames back to us; ignore them.
  if (!signal || from?.user_id === teleIdentity?.user_id) return;
  const peer = await teleEnsurePeer();
  if (!peer) return;
  try {
    if (signal.kind === "offer" || signal.kind === "answer") {
      const description = { type: signal.kind, sdp: signal.sdp };
      const offerCollision = signal.kind === "offer" && (teleMakingOffer || peer.signalingState !== "stable");
      teleIgnoreOffer = !telePolite && offerCollision;
      if (teleIgnoreOffer) return;
      await peer.setRemoteDescription(description);
      if (signal.kind === "offer") {
        await peer.setLocalDescription();
        const sessionId = teleSignalSessionId();
        if (sessionId && peer.localDescription) {
          teleSend({
            type: "webrtc_signal",
            session_id: sessionId,
            signal: { kind: peer.localDescription.type, sdp: peer.localDescription.sdp },
          });
        }
      }
    } else if (signal.kind === "candidate" && signal.candidate) {
      try {
        await peer.addIceCandidate(signal.candidate);
      } catch (error) {
        if (!teleIgnoreOffer) throw error;
      }
    }
  } catch (error) {
    console.warn("tele webrtc signal failed", error);
  }
}

async function teleArmMic() {
  // Acquire the microphone once and add it to the peer connection MUTED. Audio
  // only flows while the user holds the push-to-talk control (#4), preventing
  // accidental open-mic in the OR.
  if (teleMicTrack) return true;
  if (!navigator.mediaDevices?.getUserMedia) {
    setTeleFeedback(t("app.tele_media_unsupported"));
    return false;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
  } catch (error) {
    setTeleFeedback(t("app.tele_mic_denied"));
    return false;
  }
  const track = stream.getAudioTracks()[0];
  if (!track) return false;
  const peer = await teleEnsurePeer();
  if (!peer) {
    track.stop();
    return false;
  }
  track.enabled = false; // muted until push-to-talk is held
  teleMicTrack = track;
  teleMicSender = peer.addTrack(track, teleEnsureLocalStream());
  teleLocalStream.addTrack(track);
  teleMicOn = true;
  teleMicLive = false;
  track.addEventListener("ended", () => {
    teleStopMic();
    teleSendMediaState();
    updateTeleMediaUI();
  });
  setTeleFeedback("");
  updateTeleMediaUI();
  return true;
}

async function telePttDown() {
  if (!teleSession || teleSession.status === "ended") return;
  if (teleMicLive) return;
  const ready = await teleArmMic();
  if (!ready || !teleMicTrack) return;
  teleMicTrack.enabled = true;
  teleMicLive = true;
  teleSendMediaState();
  updateTeleMediaUI();
}

function telePttUp() {
  if (!teleMicLive) return;
  if (teleMicTrack) teleMicTrack.enabled = false;
  teleMicLive = false;
  teleSendMediaState();
  updateTeleMediaUI();
}

function teleStopMic() {
  if (teleMicSender && telePeer) {
    try {
      telePeer.removeTrack(teleMicSender);
    } catch (error) {
      /* ignored */
    }
  }
  if (teleMicTrack) {
    try {
      teleMicTrack.stop();
    } catch (error) {
      /* ignored */
    }
    if (teleLocalStream) {
      try {
        teleLocalStream.removeTrack(teleMicTrack);
      } catch (error) {
        /* ignored */
      }
    }
  }
  teleMicSender = null;
  teleMicTrack = null;
  teleMicOn = false;
  teleMicLive = false;
}

async function teleToggleScreen() {
  if (!teleSession || teleSession.status === "ended") return;
  if (teleScreenOn) {
    teleStopScreen();
    teleSendMediaState();
    updateTeleMediaUI();
    return;
  }
  if (!teleSession.consent_screen_share) {
    setTeleFeedback(t("app.tele_need_consent"));
    return;
  }
  if (!navigator.mediaDevices?.getDisplayMedia) {
    setTeleFeedback(t("app.tele_media_unsupported"));
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
  } catch (error) {
    setTeleFeedback(t("app.tele_screen_cancelled"));
    return;
  }
  const track = stream.getVideoTracks()[0];
  if (!track) return;
  const peer = await teleEnsurePeer();
  if (!peer) {
    track.stop();
    return;
  }
  teleScreenTrack = track;
  teleScreenSender = peer.addTrack(track, teleEnsureLocalStream());
  teleLocalStream.addTrack(track);
  teleScreenOn = true;
  const localVideo = teleEl("tele-local-video");
  if (localVideo) localVideo.srcObject = teleLocalStream;
  // The browser's own "Stop sharing" control ends the track directly.
  track.addEventListener("ended", () => {
    teleStopScreen();
    teleSendMediaState();
    updateTeleMediaUI();
  });
  setTeleFeedback("");
  teleSendMediaState();
  updateTeleMediaUI();
}

function teleStopScreen() {
  if (teleScreenSender && telePeer) {
    try {
      telePeer.removeTrack(teleScreenSender);
    } catch (error) {
      /* ignored */
    }
  }
  if (teleScreenTrack) {
    try {
      teleScreenTrack.stop();
    } catch (error) {
      /* ignored */
    }
    if (teleLocalStream) {
      try {
        teleLocalStream.removeTrack(teleScreenTrack);
      } catch (error) {
        /* ignored */
      }
    }
  }
  teleScreenSender = null;
  teleScreenTrack = null;
  teleScreenOn = false;
  const localVideo = teleEl("tele-local-video");
  if (localVideo) localVideo.srcObject = null;
}

function teleSendMediaState() {
  const sessionId = teleSignalSessionId();
  if (!sessionId) return;
  teleSend({ type: "media_state", session_id: sessionId, audio: teleMicLive, screen: teleScreenOn });
}

function teleHandlePeerLeft() {
  telePeerMedia = { audio: false, screen: false };
  if (teleRemoteStream) {
    teleRemoteStream.getTracks().forEach((track) => {
      try {
        teleRemoteStream.removeTrack(track);
      } catch (error) {
        /* ignored */
      }
    });
  }
  const remoteVideo = teleEl("tele-remote-video");
  if (remoteVideo) remoteVideo.srcObject = null;
  updateTeleMediaUI();
}

function teleTeardownMedia() {
  teleStopMic();
  teleStopScreen();
  if (teleDataChannel) {
    try {
      teleDataChannel.close();
    } catch (error) {
      /* ignored */
    }
    teleDataChannel = null;
  }
  if (telePeer) {
    telePeer.onnegotiationneeded = null;
    telePeer.onicecandidate = null;
    telePeer.ontrack = null;
    telePeer.onconnectionstatechange = null;
    try {
      telePeer.close();
    } catch (error) {
      /* ignored */
    }
  }
  telePeer = null;
  if (teleRemoteStream) {
    teleRemoteStream.getTracks().forEach((track) => {
      try {
        track.stop();
      } catch (error) {
        /* ignored */
      }
    });
  }
  teleRemoteStream = null;
  teleLocalStream = null;
  teleMakingOffer = false;
  teleIgnoreOffer = false;
  telePeerMedia = { audio: false, screen: false };
  const remoteVideo = teleEl("tele-remote-video");
  if (remoteVideo) remoteVideo.srcObject = null;
  const localVideo = teleEl("tele-local-video");
  if (localVideo) localVideo.srcObject = null;
  updateTeleMediaUI();
}

function teleInitMediaUI() {
  teleMicOn = false;
  teleMicLive = false;
  teleScreenOn = false;
  telePeerMedia = { audio: false, screen: false };
  updateTeleMediaUI();
}

function updateTeleMediaUI() {
  const live = !!(teleSession && teleSession.status !== "ended");
  const micBtn = teleEl("tele-mic-btn");
  if (micBtn) {
    micBtn.textContent = teleMicLive ? t("app.tele_ptt_live") : t("app.tele_ptt");
    micBtn.classList.toggle("active", teleMicLive);
    micBtn.setAttribute("aria-pressed", teleMicLive ? "true" : "false");
    micBtn.title = t("app.tele_ptt_hint");
    micBtn.disabled = !live;
  }
  const screenBtn = teleEl("tele-screen-btn");
  if (screenBtn) {
    const allowed = !!(teleSession && teleSession.consent_screen_share);
    screenBtn.textContent = teleScreenOn ? t("app.tele_screen_on") : t("app.tele_screen_off");
    screenBtn.classList.toggle("active", teleScreenOn);
    screenBtn.setAttribute("aria-pressed", teleScreenOn ? "true" : "false");
    screenBtn.disabled = !live || (!allowed && !teleScreenOn);
    screenBtn.title = allowed || teleScreenOn ? "" : t("app.tele_need_consent");
  }
  const shareTraceBtn = teleEl("tele-share-trace-btn");
  if (shareTraceBtn) shareTraceBtn.disabled = !live;
  const remoteVideo = teleEl("tele-remote-video");
  const hasRemoteVideo = !!(teleRemoteStream && teleRemoteStream.getVideoTracks().length);
  if (remoteVideo) remoteVideo.hidden = !hasRemoteVideo;
  const localVideo = teleEl("tele-local-video");
  if (localVideo) localVideo.hidden = !teleScreenOn;
  const stage = teleEl("tele-video-stage");
  if (stage) {
    stage.classList.toggle("has-media", hasRemoteVideo || teleScreenOn);
    stage.classList.toggle("has-remote", hasRemoteVideo);
  }
  const status = teleEl("tele-media-status");
  if (status) {
    const parts = [];
    if (telePeerMedia.audio) parts.push(t("app.tele_peer_voice"));
    if (telePeerMedia.screen) parts.push(t("app.tele_peer_screen"));
    if (!parts.length && live) parts.push(t("app.tele_media_idle"));
    status.textContent = parts.join(" · ");
  }
}

function bindTeleControls() {
  if (!teleEl("tele-card")) return;
  teleEl("tele-available")?.addEventListener("change", (event) => setTeleAvailability(event.target.checked));
  teleEl("tele-request-btn")?.addEventListener("click", () => requestSupport());
  teleEl("tele-cancel-btn")?.addEventListener("click", () => cancelRequest());
  teleEl("tele-consent")?.addEventListener("change", (event) => setConsent(event.target.checked));
  teleEl("tele-pointer-btn")?.addEventListener("click", () => togglePointerMode());
  const micBtn = teleEl("tele-mic-btn");
  if (micBtn) {
    // Push-to-talk (#4): transmit only while the control is held.
    micBtn.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      try { micBtn.setPointerCapture(event.pointerId); } catch (error) { /* ignored */ }
      telePttDown();
    });
    const release = () => telePttUp();
    micBtn.addEventListener("pointerup", release);
    micBtn.addEventListener("pointercancel", release);
    micBtn.addEventListener("pointerleave", release);
  }
  // Spacebar push-to-talk while a live session is open and not typing in a field.
  window.addEventListener("keydown", (event) => {
    if (event.code !== "Space" || event.repeat) return;
    if (!teleSession || teleSession.status === "ended") return;
    const tag = (document.activeElement?.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || document.activeElement?.isContentEditable) return;
    event.preventDefault();
    telePttDown();
  });
  window.addEventListener("keyup", (event) => {
    if (event.code !== "Space") return;
    telePttUp();
  });
  teleEl("tele-share-trace-btn")?.addEventListener("click", () => teleShareTrace());
  teleEl("tele-screen-btn")?.addEventListener("click", () => teleToggleScreen());
  teleEl("tele-end-btn")?.addEventListener("click", () => endTeleSession());
  teleEl("tele-link-case-btn")?.addEventListener("click", () => linkSessionToCase());
  teleEl("tele-chat-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const input = teleEl("tele-chat-input");
    const text = input?.value.trim();
    if (!text || !teleSession || teleSession.status === "ended") return;
    teleSend({ type: "chat", session_id: teleSession.session_id, text });
    input.value = "";
  });
  if (bodyMap) bodyMap.addEventListener("click", onBodyMapClickForTele);
  window.addEventListener("beforeunload", () => {
    teleManualClose = true;
    teleTeardownMedia();
    try {
      teleSocket?.close();
    } catch (error) {
      /* ignored */
    }
  });
}

function bindWorkPanelTabs() {
  const panel = document.querySelector(".work-panel");
  if (!panel) return;
  panel.querySelectorAll("button[data-work-tab]").forEach((button) => {
    button.addEventListener("click", () => setWorkPanelTab(button.dataset.workTab));
  });
}

function setWorkPanelTab(tabKey) {
  const panel = document.querySelector(".work-panel");
  if (!panel || !panel.querySelector(`[data-work-pane="${tabKey}"]`)) return;
  panel.dataset.activeTab = tabKey;
  panel.querySelectorAll("button[data-work-tab]").forEach((button) => {
    const active = button.dataset.workTab === tabKey;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
}

function updateTeleTabBadge() {
  const badge = document.getElementById("tele-tab-badge");
  if (!badge) return;
  const incoming = teleIncoming.size;
  const live = !!(teleSession && teleSession.status !== "ended");
  if (incoming > 0) {
    badge.textContent = String(incoming);
    badge.hidden = false;
    badge.classList.add("attention");
  } else if (live) {
    badge.textContent = "•";
    badge.hidden = false;
    badge.classList.remove("attention");
  } else {
    badge.hidden = true;
    badge.classList.remove("attention");
  }
}

async function init() {
  initialiseBodyMap();
  if (caseDebriefOutput) caseDebriefOutput.textContent = t("live.debrief_empty");
  if (copilotOutput) copilotOutput.textContent = t("live.copilot_empty");
  try {
    const health = await api("/api/health");
    healthStatus.textContent = health.mongo_connected ? t("health.mongo_on") : t("health.mongo_off");
    healthStatus.className = `status ${health.mongo_connected ? "ok" : "error"}`;
    const tree = await api("/api/knowledge/tree");
    await renderTree(tree);
    if (startupCaseId) await loadCase(startupCaseId);
    bindCopilotControls();
    bindSignalControls();
    bindTeleControls();
    bindWorkPanelTabs();
    bindArchiveControls();
    refreshArchiveSealAvailability();
    loadSealedArchive();
    connectTele();
    renderAssistantPrompts();
  } catch (error) {
    healthStatus.textContent = t("health.api_error");
    healthStatus.className = "status error";
    nodeDetail.textContent = error.message;
  }
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

document.getElementById("create-case-btn").addEventListener("click", () => createCase().catch((error) => {
  document.getElementById("case-result").textContent = error.message;
}));
createEventButton.addEventListener("click", () => createCaseEvent().catch((error) => {
  document.getElementById("case-result").textContent = error.message;
}));
runDebriefButton?.addEventListener("click", () => runCaseDebrief().catch((error) => {
  runDebriefButton.disabled = false;
  caseDebriefOutput.textContent = error.message;
}));
document.getElementById("ask-ai-btn").addEventListener("click", () => askAi().catch((error) => {
  document.getElementById("ai-answer").textContent = error.message;
}));

document.addEventListener("ionm:langchange", () => {
  renderDecisionFlow();
  renderMapViewControls();
  if (selectedNodeKey) {
    selectNode(selectedNodeKey);
  } else {
    renderPins(currentPins);
  }
  if (currentCaseData) {
    renderCase(currentCaseData);
  } else if (caseDebriefOutput) {
    caseDebriefOutput.textContent = t("live.debrief_empty");
  }
  renderTimeline(currentCaseEvents);
  renderDebriefHistory();
  renderTeleExperts();
  renderTeleIncoming();
  refreshArchiveSealAvailability();
});

init();
