const t = (key, vars) => (window.I18N ? window.I18N.t(key, vars) : key);

function accessLevelLabel(level) {
  const value = t("admin.al_" + String(level || ""));
  return value.startsWith("admin.") ? String(level || "") : value;
}

function statusLabel(status) {
  const value = t("admin.st_" + String(status || ""));
  return value.startsWith("admin.") ? String(status || "") : value;
}

const adminStatus = document.getElementById("admin-status");
const loginPanel = document.getElementById("login-panel");
const adminWorkspace = document.getElementById("admin-workspace");
const loginForm = document.getElementById("login-form");
const loginOutput = document.getElementById("login-output");
const logoutBtn = document.getElementById("logout-btn");
const adminProfile = document.getElementById("admin-profile");
const adminMetrics = document.getElementById("admin-metrics");
const userForm = document.getElementById("user-form");
const userPasswordInput = document.getElementById("user-password");
const generateUserPasswordBtn = document.getElementById("generate-user-password");
const userOutput = document.getElementById("user-output");
const accountPasswordForm = document.getElementById("account-password-form");
const accountUsernameForm = document.getElementById("account-username-form");
const accountNewPasswordInput = document.getElementById("account-new-password");
const accountGeneratePasswordBtn = document.getElementById("account-generate-password");
const accountPasswordOutput = document.getElementById("account-password-output");
const accountUsernameOutput = document.getElementById("account-username-output");
const usersList = document.getElementById("users-list");
const usersCount = document.getElementById("users-count");
const knowledgeForm = document.getElementById("knowledge-form");
const knowledgeSubmit = document.getElementById("knowledge-submit");
const knowledgeOutput = document.getElementById("knowledge-output");
const documentsList = document.getElementById("documents-list");
const documentsCount = document.getElementById("documents-count");
const bodymapAssetSummary = document.getElementById("bodymap-asset-summary");
const curationMuscleSelect = document.getElementById("curation-muscle-select");
const curationPinSelect = document.getElementById("curation-pin-select");
const curationAnchorControls = document.getElementById("curation-anchor-controls");
const curationPreviewUpper = document.getElementById("curation-preview-upper");
const curationApplyPreview = document.getElementById("curation-apply-preview");
const curationClearPreview = document.getElementById("curation-clear-preview");
const curationPreviewCount = document.getElementById("curation-preview-count");
const curationPreviewSummary = document.getElementById("curation-preview-summary");
const curationViewControls = document.getElementById("curation-view-controls");
const curationPinDetail = document.getElementById("curation-pin-detail");
const curationSave = document.getElementById("curation-save");
const curationDiscard = document.getElementById("curation-discard");
const curationReset = document.getElementById("curation-reset");
const curationExport = document.getElementById("curation-export");
const curationOutput = document.getElementById("curation-output");
const curationMap = document.getElementById("curation-map");
const curationPinsLayer = document.getElementById("curation-pins-layer");
const curationCoordinateReadout = document.getElementById("curation-coordinate-readout");

let currentSession = null;
let currentUsers = [];
let currentDocuments = [];
let bodymapAsset = null;
let curationCoordinates = [];
let selectedCurationLatName = null;
let selectedCurationPinIndex = 0;
let curationView = "full";
let dirtyCurationLatName = null;
let originalCurationCoordinates = new Map();
let activeCalibrationPreview = null;
let selectedCalibrationPreviewLatName = null;

const c4c5CalibrationAnchors = ["Deltoid", "Biceps brachii"];
const c4c5PosteriorCalibrationAnchors = ["Triceps brachii", "Extensor carpi radialis"];
const c4c5UpperLimbCalibrationTargets = [
  { latName: "Brachioradialis", pinView: "anterior", mapView: "upper_limb", viewLabel: "anterior/volar", mode: "anchor_transform" },
  { latName: "Flexor carpi radialis", pinView: "anterior", mapView: "upper_limb", viewLabel: "anterior/volar", mode: "anchor_transform" },
  { latName: "Flexor digitorum profundus", pinView: "anterior", mapView: "upper_limb", viewLabel: "anterior/volar", mode: "anchor_transform" },
  { latName: "Abductor pollicis brevis", pinView: "anterior", mapView: "upper_limb", viewLabel: "anterior hand", mode: "anchor_transform" },
  { latName: "Abductor digiti minimi", pinView: "anterior", mapView: "upper_limb", viewLabel: "anterior hand", mode: "anchor_transform" },
  { latName: "Triceps brachii", pinView: "posterior", mapView: "posterior_upper_limb", viewLabel: "posterior arm", mode: "posterior_anchor" },
  { latName: "Extensor carpi radialis", pinView: "posterior", mapView: "posterior_upper_limb", viewLabel: "posterior forearm", mode: "posterior_anchor" },
  { latName: "Extensor digitorum", pinView: "posterior", mapView: "posterior_upper_limb", viewLabel: "posterior forearm", mode: "posterior_anchor_transform" },
  { latName: "First dorsal interosseous", pinView: "posterior", mapView: "posterior_upper_limb", viewLabel: "posterior hand", mode: "posterior_anchor_transform" },
];

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "include",
    headers: options.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(extractError(body) || response.statusText);
  }
  return response.json();
}

function extractError(body) {
  try {
    const parsed = JSON.parse(body);
    return parsed.detail || body;
  } catch (error) {
    return body;
  }
}

async function init() {
  bindEvents();
  try {
    currentSession = await api("/api/admin/session");
    await loadAdminData();
    renderAuthenticated();
  } catch (error) {
    renderLoggedOut();
  }
}

function bindEvents() {
  loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    loginOutput.textContent = "";
    const payload = Object.fromEntries(new FormData(loginForm).entries());
    try {
      currentSession = await api("/api/admin/login", { method: "POST", body: JSON.stringify(payload) });
      loginForm.reset();
      await loadAdminData();
      renderAuthenticated();
    } catch (error) {
      loginOutput.textContent = error.message;
    }
  });

  logoutBtn.addEventListener("click", async () => {
    await api("/api/admin/logout", { method: "POST", body: JSON.stringify({}) });
    currentSession = null;
    currentUsers = [];
    currentDocuments = [];
    bodymapAsset = null;
    curationCoordinates = [];
    originalCurationCoordinates = new Map();
    dirtyCurationLatName = null;
    activeCalibrationPreview = null;
    selectedCalibrationPreviewLatName = null;
    renderLoggedOut();
  });

  userForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    userOutput.textContent = "";
    const payload = Object.fromEntries(new FormData(userForm).entries());
    try {
      await api("/api/admin/users", { method: "POST", body: JSON.stringify(payload) });
      userForm.reset();
      await loadUsers();
      renderUsers();
      renderMetrics();
      userOutput.textContent = t("admin.js.user_created");
    } catch (error) {
      userOutput.textContent = error.message;
    }
  });

  generateUserPasswordBtn?.addEventListener("click", () => {
    userPasswordInput.value = generatePassword();
    userPasswordInput.focus();
    userPasswordInput.select();
  });

  accountGeneratePasswordBtn?.addEventListener("click", () => {
    accountNewPasswordInput.value = generatePassword();
    accountNewPasswordInput.focus();
    accountNewPasswordInput.select();
  });

  accountPasswordForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    accountPasswordOutput.textContent = "";
    const payload = Object.fromEntries(new FormData(accountPasswordForm).entries());
    try {
      await api("/api/admin/account/password", { method: "POST", body: JSON.stringify(payload) });
      accountPasswordForm.reset();
      accountPasswordOutput.textContent = t("account.pw_changed");
    } catch (error) {
      accountPasswordOutput.textContent = error.message;
    }
  });

  accountUsernameForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    accountUsernameOutput.textContent = "";
    const payload = Object.fromEntries(new FormData(accountUsernameForm).entries());
    try {
      const result = await api("/api/admin/account/username", { method: "POST", body: JSON.stringify(payload) });
      accountUsernameForm.reset();
      const updated = result?.account || null;
      if (updated && currentSession) {
        currentSession.admin = updated;
        renderProfile();
      }
      accountUsernameOutput.textContent = t("account.username_changed", { username: updated?.username || payload.new_username });
    } catch (error) {
      accountUsernameOutput.textContent = error.message;
    }
  });

  usersList.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-reset-password]");
    if (!button) return;
    resetUserPassword(button.dataset.resetPassword).catch((error) => {
      userOutput.textContent = error.message;
    });
  });

  knowledgeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    knowledgeOutput.textContent = t("admin.js.ocr_progress");
    knowledgeSubmit.disabled = true;
    try {
      await api("/api/admin/knowledge/documents", { method: "POST", body: new FormData(knowledgeForm) });
      knowledgeForm.reset();
      await loadDocuments();
      renderDocuments();
      renderMetrics();
      knowledgeOutput.textContent = t("admin.js.doc_indexed");
    } catch (error) {
      knowledgeOutput.textContent = error.message;
    } finally {
      knowledgeSubmit.disabled = false;
    }
  });

  curationMuscleSelect.addEventListener("change", () => {
    if (!confirmDiscardUnsavedMove()) {
      curationMuscleSelect.value = selectedCurationLatName || "";
      return;
    }
    selectCurationTarget(curationMuscleSelect.value);
  });

  curationAnchorControls?.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-anchor-target]");
    if (!button) return;
    if (!confirmDiscardUnsavedMove()) return;
    selectCurationTarget(button.dataset.anchorTarget, { preferredView: button.dataset.anchorView || "upper_limb" });
  });

  curationPreviewUpper?.addEventListener("click", () => {
    previewUpperLimbCalibration();
  });

  curationApplyPreview?.addEventListener("click", () => {
    applySelectedCalibrationPreview();
  });

  curationClearPreview?.addEventListener("click", () => {
    clearCalibrationPreview(t("admin.js.preview_cleared"));
  });

  curationPinSelect.addEventListener("change", () => {
    selectedCurationPinIndex = Number(curationPinSelect.value || 0);
    const pin = selectedCurationPin();
    if (pin?.view) curationView = pin.view;
    renderCurationPanel();
  });

  curationSave.addEventListener("click", () => saveCurationCoordinate().catch((error) => {
    curationOutput.textContent = error.message;
  }));

  curationDiscard.addEventListener("click", () => {
    discardUnsavedCurationMove();
  });

  curationReset.addEventListener("click", () => resetSelectedCurationTarget().catch((error) => {
    curationOutput.textContent = error.message;
  }));

  curationExport.addEventListener("click", () => exportCurationCoordinates().catch((error) => {
    curationOutput.textContent = error.message;
  }));

  curationMap.addEventListener("click", (event) => {
    moveSelectedPin(event).catch((error) => {
      curationOutput.textContent = error.message;
    });
  });
}

async function loadAdminData() {
  await Promise.all([loadUsers(), loadDocuments(), loadCurationData()]);
}

async function loadUsers() {
  const payload = await api("/api/admin/users");
  currentUsers = payload.items || [];
}

async function loadDocuments() {
  const payload = await api("/api/admin/knowledge/documents");
  currentDocuments = payload.items || [];
}

async function loadCurationData() {
  const payload = await api("/api/admin/bodymap/coordinates");
  bodymapAsset = payload.asset || null;
  curationCoordinates = payload.items || [];
  originalCurationCoordinates = new Map(
    curationCoordinates.map((item) => [item.lat_name, cloneCoordinates(item.coordinates || [])])
  );
  dirtyCurationLatName = null;
  activeCalibrationPreview = null;
  selectedCalibrationPreviewLatName = null;
  selectedCurationLatName = validCurationTarget(selectedCurationLatName)
    ? selectedCurationLatName
    : preferredDefaultCurationTarget();
  if (c4c5CalibrationAnchors.includes(selectedCurationLatName)) curationView = "upper_limb";
}

function renderAuthenticated() {
  adminStatus.textContent = t("admin.js.connected");
  adminStatus.className = "status ok";
  loginPanel.classList.add("hidden");
  adminWorkspace.classList.remove("hidden");
  renderProfile();
  renderMetrics();
  renderUsers();
  renderDocuments();
  renderCurationPanel();
}

function renderLoggedOut() {
  adminStatus.textContent = t("admin.js.login_status");
  adminStatus.className = "status";
  loginPanel.classList.remove("hidden");
  adminWorkspace.classList.add("hidden");
  loginOutput.textContent = "";
}

function renderProfile() {
  const admin = currentSession?.admin || {};
  adminProfile.innerHTML = `
    <strong>${escapeHtml(admin.display_name || t("admin.sidebar_title"))}</strong>
    <span>${escapeHtml(admin.username || "")}</span>
    <small>${escapeHtml(admin.role || "")}</small>
  `;
}

function renderMetrics() {
  const indexedDocuments = currentDocuments.filter((document) => document.status === "indexed").length;
  const chunks = currentDocuments.reduce((sum, document) => sum + Number(document.chunk_count || 0), 0);
  const curated = curationCoordinates.filter((item) => item.curation_status === "reviewed" || item.curation_status === "production_curated").length;
  adminMetrics.innerHTML = `
    ${renderMetric(t("admin.js.m_users"), currentUsers.length, t("admin.js.m_records"))}
    ${renderMetric(t("admin.js.m_documents"), currentDocuments.length, `${indexedDocuments} ${t("admin.js.m_indexed")}`)}
    ${renderMetric(t("admin.js.m_chunks"), chunks, t("admin.js.m_rag"))}
    ${renderMetric(t("admin.js.m_coords"), curationCoordinates.length, `${curated} ${t("admin.js.m_curated")}`)}
  `;
}

function renderUsers() {
  usersCount.textContent = `${currentUsers.length} ${t("admin.js.m_records")}`;
  if (!currentUsers.length) {
    usersList.className = "admin-list empty-state compact";
    usersList.textContent = t("admin.no_users");
    return;
  }
  usersList.className = "admin-list";
  usersList.innerHTML = currentUsers.map((user) => `
    <article class="admin-list-item">
      <div>
        <strong>${escapeHtml(user.full_name || user.username)}</strong>
        <span>${escapeHtml([user.email, user.phone].filter(Boolean).join(" · "))}</span>
        <small>${escapeHtml([user.professional_title, user.specialty, user.organization].filter(Boolean).join(" · "))}</small>
      </div>
      <div class="admin-tags">
        <span>${escapeHtml(accessLevelLabel(user.access_level))}</span>
        <span>${escapeHtml(statusLabel(user.status))}</span>
        <span>${escapeHtml(user.login_enabled ? t("admin.js.login_enabled") : t("admin.js.profile_only"))}</span>
        <span>${escapeHtml(user.has_password ? t("admin.js.password_set") : t("admin.js.no_password"))}</span>
        <button class="secondary-btn" type="button" data-reset-password="${escapeHtml(user.user_id)}">${escapeHtml(t("admin.js.reset_password"))}</button>
      </div>
    </article>
  `).join("");
}

async function resetUserPassword(userId) {
  const user = currentUsers.find((item) => item.user_id === userId);
  if (!user) throw new Error(t("admin.js.user_not_found"));
  const password = generatePassword();
  if (!window.confirm(t("admin.js.reset_password_confirm", { name: user.full_name || user.username }))) return;
  await api(`/api/admin/users/${encodeURIComponent(userId)}/password`, {
    method: "POST",
    body: JSON.stringify({ password }),
  });
  await loadUsers();
  renderUsers();
  userOutput.textContent = t("admin.js.new_password", { user: user.username, password });
}

function generatePassword(length = 14) {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%";
  const values = new Uint32Array(length);
  crypto.getRandomValues(values);
  return Array.from(values, (value) => alphabet[value % alphabet.length]).join("");
}

function renderDocuments() {
  documentsCount.textContent = `${currentDocuments.length} ${t("admin.js.documents_lc")}`;
  if (!currentDocuments.length) {
    documentsList.className = "admin-list empty-state compact";
    documentsList.textContent = t("admin.no_documents");
    return;
  }
  documentsList.className = "admin-list";
  documentsList.innerHTML = currentDocuments.map((document) => `
    <article class="admin-list-item">
      <div>
        <strong>${escapeHtml(document.title)}</strong>
        <span>${escapeHtml([document.domain, document.original_filename].filter(Boolean).join(" · "))}</span>
        <small>${escapeHtml(`${document.page_count || 0} ${t("admin.js.pages")} · ${document.chunk_count || 0} ${t("admin.js.chunks_lc")} · ${document.character_count || 0} ${t("admin.js.chars")}`)}</small>
      </div>
      <div class="admin-tags">
        <span>${escapeHtml(document.status)}</span>
        ${(document.tags || []).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}
      </div>
    </article>
  `).join("");
}

function renderCurationPanel() {
  if (!bodymapAsset || !curationCoordinates.length) {
    bodymapAssetSummary.className = "curation-asset-summary empty-state compact";
    bodymapAssetSummary.textContent = t("admin.js.no_coord_data");
    return;
  }
  const item = selectedCurationItem() || curationCoordinates[0];
  selectedCurationLatName = item?.lat_name || null;
  selectedCurationPinIndex = Math.min(selectedCurationPinIndex, Math.max((item?.coordinates || []).length - 1, 0));
  const pin = selectedCurationPin();
  if (pin && !isPointInsideView(pin.x, pin.y, currentCurationBounds())) curationView = pin.view || "full";

  bodymapAssetSummary.className = "curation-asset-summary";
  bodymapAssetSummary.innerHTML = `
    <strong>${escapeHtml(bodymapAsset.title || t("admin.js.bodymap_asset"))}</strong>
    <span>${escapeHtml([bodymapAsset.width && `${bodymapAsset.width}x${bodymapAsset.height}`, bodymapAsset.license, bodymapAsset.status].filter(Boolean).join(" · "))}</span>
    <small>${escapeHtml(bodymapAsset.source || "")}</small>
  `;

  renderCurationAnchorControls();
  renderCalibrationPreviewControls();

  curationMuscleSelect.innerHTML = sortedCurationCoordinates().map((coordinate) => `
    <option value="${escapeHtml(coordinate.lat_name)}" ${coordinate.lat_name === selectedCurationLatName ? "selected" : ""}>
      ${escapeHtml(coordinate.name_el || coordinate.lat_name)} · ${escapeHtml(coordinate.coordinate_count || 0)} pins · ${escapeHtml(curationStatusLabel(coordinate))}
    </option>
  `).join("");

  curationPinSelect.innerHTML = (item.coordinates || []).map((coordinate, index) => `
    <option value="${index}" ${index === selectedCurationPinIndex ? "selected" : ""}>
      ${escapeHtml(index + 1)} · ${escapeHtml(coordinate.side)} · ${escapeHtml(coordinate.view)} · ${escapeHtml(formatCoordinate(coordinate.x))}, ${escapeHtml(formatCoordinate(coordinate.y))}
    </option>
  `).join("");

  renderCurationViewControls();
  renderCurationPinDetail(item, pin);
  renderCurationSafetyControls(item);
  applyCurationViewport();
  renderCurationPins();
}

function renderCurationAnchorControls() {
  if (!curationAnchorControls) return;
  curationAnchorControls.querySelectorAll("button[data-anchor-target]").forEach((button) => {
    button.classList.toggle("active", button.dataset.anchorTarget === selectedCurationLatName);
  });
}

function renderCalibrationPreviewControls() {
  if (!curationPreviewSummary || !curationPreviewCount || !curationApplyPreview || !curationClearPreview) return;
  const proposals = activeCalibrationPreview?.proposals || [];
  const selectedProposal = proposalForLatName(selectedCurationLatName);
  curationPreviewCount.textContent = proposals.length
    ? `${proposals.length} ${t("admin.js.targets")} · ${proposalPinCount(proposals)} ${t("admin.js.pins_lc")}`
    : t("admin.no_preview");
  curationApplyPreview.disabled = !selectedProposal || Boolean(dirtyCurationLatName);
  curationClearPreview.disabled = !activeCalibrationPreview;
  if (!activeCalibrationPreview) {
    curationPreviewSummary.className = "calibration-preview-summary empty-state compact";
    curationPreviewSummary.textContent = t("admin.no_active_preview");
    return;
  }
  curationPreviewSummary.className = "calibration-preview-summary";
  curationPreviewSummary.innerHTML = proposals.map((proposal) => `
    <button class="calibration-preview-item${proposal.latName === selectedCalibrationPreviewLatName ? " active" : ""}" type="button" data-preview-target="${escapeHtml(proposal.latName)}" data-proposal-view="${escapeHtml(proposal.mapView)}">
      <strong>${escapeHtml(proposal.nameEl || proposal.latName)}</strong>
      <span>${escapeHtml(proposalDeltaText(proposal))}</span>
    </button>
  `).join("");
  curationPreviewSummary.querySelectorAll("button[data-preview-target]").forEach((button) => {
    button.addEventListener("click", () => selectCalibrationPreviewTarget(button.dataset.previewTarget));
  });
}

function renderCurationViewControls() {
  curationViewControls.innerHTML = Object.entries(curationViewDefinitions()).map(([key, view]) => `
    <button class="map-view-btn${key === curationView ? " active" : ""}" type="button" data-curation-view="${escapeHtml(key)}">${escapeHtml(t(view.i18nKey))}</button>
  `).join("");
  curationViewControls.querySelectorAll("button[data-curation-view]").forEach((button) => {
    button.addEventListener("click", () => {
      curationView = button.dataset.curationView;
      renderCurationPanel();
    });
  });
}

function renderCurationPinDetail(item, pin) {
  if (!pin) {
    curationPinDetail.textContent = t("admin.js.no_pin");
    return;
  }
  curationPinDetail.innerHTML = `
    <strong>${escapeHtml(item.name_el || item.lat_name)}</strong>
    <span>${escapeHtml(item.lat_name)} · ${escapeHtml(pin.side)} · ${escapeHtml(pin.view)}</span>
    <span>${escapeHtml(item.placement || t("admin.js.no_placement"))} / ${escapeHtml(item.action || t("admin.js.no_action"))}</span>
    <span>${escapeHtml(t("admin.js.current"))}: ${escapeHtml(formatCoordinate(pin.x))}%, ${escapeHtml(formatCoordinate(pin.y))}% · ${escapeHtml(curationStatusLabel(item))}${dirtyCurationLatName === item.lat_name ? ` · ${t("admin.js.unsaved_move")}` : ""}</span>
    <span>${escapeHtml(t("admin.js.registry"))} · ${escapeHtml(sourceLabel(item))}</span>
  `;
  curationCoordinateReadout.textContent = `${formatCoordinate(pin.x)}%, ${formatCoordinate(pin.y)}%`;
}

function renderCurationSafetyControls(item) {
  const hasDirtyMove = dirtyCurationLatName === item?.lat_name;
  curationSave.disabled = !hasDirtyMove;
  curationDiscard.disabled = !hasDirtyMove;
  curationReset.disabled = !item?.lat_name;
}

function renderCurationPins() {
  const item = selectedCurationItem();
  const coordinates = item?.coordinates || [];
  const savedPinMarkup = coordinates.map((pin, index) => {
    const projected = projectCurationPoint(Number(pin.x), Number(pin.y));
    if (!projected) return "";
    return `
      <button class="curation-pin${index === selectedCurationPinIndex ? " active" : ""}" type="button" data-pin-index="${index}" data-side="${escapeHtml(pin.side)}" style="left:${projected.left}%; top:${projected.top}%;" aria-label="${escapeHtml(item.lat_name)} ${escapeHtml(pin.side)} ${escapeHtml(pin.view)}">
        <span>${escapeHtml(pin.side)}</span>
      </button>
    `;
  }).join("");
  const previewPinMarkup = calibrationPreviewPinsForCurrentView().map((previewPin) => {
    const projected = projectCurationPoint(Number(previewPin.x), Number(previewPin.y));
    if (!projected) return "";
    return `
      <button class="calibration-preview-pin${previewPin.latName === selectedCalibrationPreviewLatName ? " active" : ""}" type="button" data-preview-target="${escapeHtml(previewPin.latName)}" data-preview-side="${escapeHtml(previewPin.side)}" style="left:${projected.left}%; top:${projected.top}%;" aria-label="${escapeHtml(previewPin.latName)} ${escapeHtml(previewPin.side)} preview">
        <span>${escapeHtml(previewPin.side)}</span>
      </button>
    `;
  }).join("");
  curationPinsLayer.innerHTML = `${previewPinMarkup}${savedPinMarkup}`;
  curationPinsLayer.querySelectorAll(".curation-pin").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      selectedCurationPinIndex = Number(button.dataset.pinIndex || 0);
      renderCurationPanel();
    });
  });
  curationPinsLayer.querySelectorAll(".calibration-preview-pin").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      selectCalibrationPreviewTarget(button.dataset.previewTarget, button.dataset.previewSide);
    });
  });
}

async function moveSelectedPin(event) {
  const item = selectedCurationItem();
  const pin = selectedCurationPin();
  if (!item || !pin) return;
  const point = eventToCurationPoint(event);
  if (!point) return;
  pin.x = point.x;
  pin.y = point.y;
  if (curationView === "anterior" || curationView === "posterior") pin.view = curationView;
  if (curationView === "upper_limb") pin.view = "anterior";
  if (curationView === "posterior_upper_limb") pin.view = "posterior";
  dirtyCurationLatName = item.lat_name;
  curationOutput.textContent = t("admin.js.coord_moved");
  renderCurationPanel();
}

async function saveCurationCoordinate() {
  const item = selectedCurationItem();
  if (!item) return;
  const payload = {
    lat_name: item.lat_name,
    coordinates: item.coordinates.map(publicPinPayload),
    curation_status: "reviewed",
    notes: `Curated on ${new Date().toISOString()} against ${bodymapAsset?.asset || "body.jpg"}`,
  };
  const result = await api("/api/admin/bodymap/coordinates", { method: "PATCH", body: JSON.stringify(payload) });
  const index = curationCoordinates.findIndex((coordinate) => coordinate.lat_name === item.lat_name);
  if (index >= 0) curationCoordinates[index] = { ...curationCoordinates[index], ...result.coordinate };
  originalCurationCoordinates.set(item.lat_name, cloneCoordinates(result.coordinate.coordinates || []));
  dirtyCurationLatName = null;
  refreshCalibrationPreview();
  curationOutput.textContent = t("admin.js.coord_saved");
  renderMetrics();
  renderCurationPanel();
}

function discardUnsavedCurationMove() {
  const item = selectedCurationItem();
  if (!item || dirtyCurationLatName !== item.lat_name) return;
  item.coordinates = cloneCoordinates(originalCurationCoordinates.get(item.lat_name) || item.coordinates || []);
  dirtyCurationLatName = null;
  refreshCalibrationPreview();
  curationOutput.textContent = t("admin.js.move_discarded");
  renderCurationPanel();
}

async function resetSelectedCurationTarget() {
  const item = selectedCurationItem();
  if (!item) return;
  const confirmed = window.confirm(t("admin.js.reset_target_confirm", { lat: item.lat_name }));
  if (!confirmed) return;
  const result = await api("/api/admin/bodymap/coordinates/reset", {
    method: "POST",
    body: JSON.stringify({ lat_name: item.lat_name }),
  });
  const index = curationCoordinates.findIndex((coordinate) => coordinate.lat_name === item.lat_name);
  if (index >= 0) curationCoordinates[index] = { ...curationCoordinates[index], ...result.coordinate };
  originalCurationCoordinates.set(item.lat_name, cloneCoordinates(result.coordinate.coordinates || []));
  dirtyCurationLatName = null;
  refreshCalibrationPreview();
  curationOutput.textContent = t("admin.js.target_restored");
  renderMetrics();
  renderCurationPanel();
}

async function exportCurationCoordinates() {
  const payload = await api("/api/admin/bodymap/export");
  const blob = new Blob([JSON.stringify(payload.coordinates, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "ionm-coords-curated.json";
  link.click();
  URL.revokeObjectURL(url);
  curationOutput.textContent = t("admin.js.exported", { count: payload.count });
}

function selectedCurationItem() {
  return curationCoordinates.find((coordinate) => coordinate.lat_name === selectedCurationLatName) || null;
}

function selectedCurationPin() {
  return selectedCurationItem()?.coordinates?.[selectedCurationPinIndex] || null;
}

function selectCurationTarget(latName, options = {}) {
  if (!validCurationTarget(latName)) return;
  selectedCurationLatName = latName;
  selectedCurationPinIndex = Number.isInteger(options.pinIndex) ? options.pinIndex : 0;
  const pin = selectedCurationItem()?.coordinates?.[0];
  curationView = options.preferredView || preferredCurationViewForTarget(latName, pin);
  renderCurationPanel();
}

function preferredCurationViewForTarget(latName, pin) {
  if (c4c5CalibrationAnchors.includes(latName)) return "upper_limb";
  if (c4c5PosteriorCalibrationAnchors.includes(latName)) return "posterior_upper_limb";
  const previewTarget = c4c5UpperLimbCalibrationTargets.find((target) => target.latName === latName);
  return previewTarget?.mapView || pin?.view || "full";
}

function preferredDefaultCurationTarget() {
  return c4c5CalibrationAnchors.find(validCurationTarget) || curationCoordinates[0]?.lat_name || null;
}

function validCurationTarget(latName) {
  return Boolean(latName && curationCoordinates.some((coordinate) => coordinate.lat_name === latName));
}

function sortedCurationCoordinates() {
  const anchorOrder = new Map([...c4c5CalibrationAnchors, ...c4c5PosteriorCalibrationAnchors].map((latName, index) => [latName, index]));
  return [...curationCoordinates].sort((first, second) => {
    const firstAnchor = anchorOrder.has(first.lat_name);
    const secondAnchor = anchorOrder.has(second.lat_name);
    if (firstAnchor || secondAnchor) {
      return (anchorOrder.get(first.lat_name) ?? 99) - (anchorOrder.get(second.lat_name) ?? 99);
    }
    return String(first.name_el || first.lat_name).localeCompare(String(second.name_el || second.lat_name), "el");
  });
}

function previewUpperLimbCalibration() {
  if (dirtyCurationLatName) {
    curationOutput.textContent = t("admin.js.preview_save_first", { lat: dirtyCurationLatName });
    return;
  }
  const preview = buildC4C5UpperLimbPreview();
  if (!preview.proposals.length) {
    activeCalibrationPreview = null;
    selectedCalibrationPreviewLatName = null;
    curationOutput.textContent = preview.issue || t("admin.js.preview_none");
    renderCurationPanel();
    return;
  }
  activeCalibrationPreview = preview;
  selectedCalibrationPreviewLatName = proposalForLatName(selectedCurationLatName)?.latName || preview.proposals[0].latName;
  curationView = "upper_limb";
  curationOutput.textContent = t("admin.js.preview_ready", { targets: preview.proposals.length, pins: proposalPinCount(preview.proposals) });
  renderCurationPanel();
}

function buildC4C5UpperLimbPreview() {
  const anteriorTransforms = {
    L: buildSideCalibrationTransform("L"),
    R: buildSideCalibrationTransform("R"),
  };
  const posteriorTransforms = {
    L: buildPosteriorSideCalibrationTransform("L"),
    R: buildPosteriorSideCalibrationTransform("R"),
  };
  const transforms = { anterior: anteriorTransforms, posterior: posteriorTransforms };
  const missingSides = Object.entries(anteriorTransforms).filter(([, transform]) => !transform).map(([side]) => side);
  if (missingSides.length) {
    return { regionKey: "c4c5_upper_limb", proposals: [], issue: t("admin.js.missing_anchors", { sides: missingSides.join(", ") }) };
  }
  const proposals = c4c5UpperLimbCalibrationTargets.map((target) => buildTargetCalibrationProposal(target, transforms)).filter(Boolean);
  return {
    regionKey: "c4c5_upper_limb",
    label: "C4-C5 upper limb view-aware",
    defaultView: "upper_limb",
    anchors: c4c5CalibrationAnchors,
    proposals,
    transforms,
    createdAt: new Date().toISOString(),
  };
}

function buildSideCalibrationTransform(side) {
  const seedDeltoid = seedPinFor("Deltoid", side, "anterior");
  const seedBiceps = seedPinFor("Biceps brachii", side, "anterior");
  const currentDeltoid = currentPinFor("Deltoid", side, "anterior");
  const currentBiceps = currentPinFor("Biceps brachii", side, "anterior");
  if (!seedDeltoid || !seedBiceps || !currentDeltoid || !currentBiceps) return null;
  return computeSimilarityTransform(seedDeltoid, seedBiceps, currentDeltoid, currentBiceps);
}

function buildPosteriorSideCalibrationTransform(side) {
  const seedTriceps = seedPinFor("Triceps brachii", side, "posterior");
  const seedEcr = seedPinFor("Extensor carpi radialis", side, "posterior");
  const currentTriceps = currentPinFor("Triceps brachii", side, "posterior");
  const currentEcr = currentPinFor("Extensor carpi radialis", side, "posterior");
  if (!seedTriceps || !seedEcr || !currentTriceps || !currentEcr) return null;
  return computeSimilarityTransform(seedTriceps, seedEcr, currentTriceps, currentEcr);
}

function buildTargetCalibrationProposal(target, transforms) {
  const latName = target.latName;
  const item = curationItemByLatName(latName);
  if (!item) return null;
  const seedPins = item.seed_coordinates || [];
  const coordinates = seedPins.map((seedPin) => {
    if (seedPin.view !== target.pinView || !["L", "R"].includes(seedPin.side)) return null;
    const currentPin = matchingPin(item.coordinates || [], seedPin);
    const effectiveMode = effectiveProposalMode(target);
    const transformed = effectiveMode === "anchor_transform"
      ? transformCalibrationPoint(transforms.anterior[seedPin.side], seedPin)
      : effectiveMode === "posterior_anchor_transform"
        ? transformCalibrationPoint(transforms.posterior[seedPin.side], seedPin)
        : { x: roundCoordinate(seedPin.x), y: roundCoordinate(seedPin.y) };
    if (!transformed) return null;
    return {
      view: target.pinView,
      side: seedPin.side,
      x: transformed.x,
      y: transformed.y,
      currentX: Number(currentPin?.x ?? seedPin.x),
      currentY: Number(currentPin?.y ?? seedPin.y),
      mode: effectiveMode,
    };
  }).filter(Boolean);
  if (!coordinates.length) return null;
  const proposalMode = coordinates[0]?.mode || target.mode;
  return {
    latName,
    nameEl: item.name_el,
    coordinates,
    pinView: target.pinView,
    mapView: target.mapView,
    viewLabel: target.viewLabel,
    mode: proposalMode,
    configuredMode: target.mode,
    averageDelta: averageProposalDelta(coordinates),
  };
}

function computeSimilarityTransform(seedA, seedB, currentA, currentB) {
  const seedX = Number(seedB.x) - Number(seedA.x);
  const seedY = Number(seedB.y) - Number(seedA.y);
  const currentX = Number(currentB.x) - Number(currentA.x);
  const currentY = Number(currentB.y) - Number(currentA.y);
  const denominator = seedX * seedX + seedY * seedY;
  if (denominator < 0.0001) return null;
  const a = (currentX * seedX + currentY * seedY) / denominator;
  const b = (currentY * seedX - currentX * seedY) / denominator;
  return {
    seedOrigin: { x: Number(seedA.x), y: Number(seedA.y) },
    currentOrigin: { x: Number(currentA.x), y: Number(currentA.y) },
    a,
    b,
  };
}

function transformCalibrationPoint(transform, pin) {
  if (!transform) return null;
  const dx = Number(pin.x) - transform.seedOrigin.x;
  const dy = Number(pin.y) - transform.seedOrigin.y;
  return {
    x: roundCoordinate(clampPercentage(transform.currentOrigin.x + transform.a * dx - transform.b * dy)),
    y: roundCoordinate(clampPercentage(transform.currentOrigin.y + transform.b * dx + transform.a * dy)),
  };
}

function effectiveProposalMode(target) {
  if (target.mode !== "posterior_anchor_transform") return target.mode;
  return posteriorAnchorsReady() ? "posterior_anchor_transform" : "seed_view_baseline";
}

function posteriorAnchorsReady() {
  return c4c5PosteriorCalibrationAnchors.every((latName) => {
    const item = curationItemByLatName(latName);
    return item?.source === "admin_bodymap_curation" && ["reviewed", "production_curated"].includes(item?.curation_status);
  });
}

function selectCalibrationPreviewTarget(latName, side = null) {
  const proposal = proposalForLatName(latName);
  if (!proposal) return;
  if (!confirmDiscardUnsavedMove()) return;
  selectedCalibrationPreviewLatName = latName;
  const item = curationItemByLatName(latName);
  const pinIndex = side
    ? Math.max((item?.coordinates || []).findIndex((pin) => pin.side === side), 0)
    : 0;
  selectCurationTarget(latName, { preferredView: proposal.mapView || activeCalibrationPreview?.defaultView || "upper_limb", pinIndex });
}

function applySelectedCalibrationPreview() {
  const proposal = proposalForLatName(selectedCurationLatName);
  const item = selectedCurationItem();
  if (!proposal || !item) return;
  if (dirtyCurationLatName) {
    curationOutput.textContent = t("admin.js.apply_save_first", { lat: dirtyCurationLatName });
    renderCurationPanel();
    return;
  }
  item.coordinates = applyProposalToCoordinates(item.coordinates || [], proposal.coordinates).map(publicPinPayload);
  dirtyCurationLatName = item.lat_name;
  selectedCalibrationPreviewLatName = item.lat_name;
  curationView = proposal.mapView || activeCalibrationPreview?.defaultView || "upper_limb";
  curationOutput.textContent = t("admin.js.loaded_from_preview", { lat: item.lat_name });
  renderCurationPanel();
}

function applyProposalToCoordinates(currentCoordinates, proposalCoordinates) {
  const usedKeys = new Set();
  const updated = currentCoordinates.map((pin) => {
    const proposal = proposalCoordinates.find((candidate) => !usedKeys.has(pinKey(candidate)) && candidate.side === pin.side && candidate.view === pin.view)
      || proposalCoordinates.find((candidate) => !usedKeys.has(pinKey(candidate)) && candidate.side === pin.side);
    if (!proposal) return publicPinPayload(pin);
    usedKeys.add(pinKey(proposal));
    return publicPinPayload({ ...pin, view: proposal.view, x: proposal.x, y: proposal.y, side: proposal.side });
  });
  proposalCoordinates.forEach((proposal) => {
    if (!usedKeys.has(pinKey(proposal))) updated.push(publicPinPayload(proposal));
  });
  return updated;
}

function clearCalibrationPreview(message = "") {
  activeCalibrationPreview = null;
  selectedCalibrationPreviewLatName = null;
  if (message) curationOutput.textContent = message;
  renderCurationPanel();
}

function refreshCalibrationPreview() {
  if (!activeCalibrationPreview) return;
  const previousSelection = selectedCalibrationPreviewLatName;
  const preview = buildC4C5UpperLimbPreview();
  if (!preview.proposals.length) {
    activeCalibrationPreview = null;
    selectedCalibrationPreviewLatName = null;
    return;
  }
  activeCalibrationPreview = preview;
  selectedCalibrationPreviewLatName = proposalForLatName(previousSelection)?.latName
    || proposalForLatName(selectedCurationLatName)?.latName
    || preview.proposals[0].latName;
}

function calibrationPreviewPinsForCurrentView() {
  return (activeCalibrationPreview?.proposals || []).flatMap((proposal) =>
    proposal.coordinates.map((coordinate) => ({ ...coordinate, latName: proposal.latName, nameEl: proposal.nameEl }))
  );
}

function proposalForLatName(latName) {
  return (activeCalibrationPreview?.proposals || []).find((proposal) => proposal.latName === latName) || null;
}

function proposalPinCount(proposals) {
  return proposals.reduce((sum, proposal) => sum + proposal.coordinates.length, 0);
}

function proposalDeltaText(proposal) {
  const modeLabels = {
    anchor_transform: t("admin.js.md_anterior_fit"),
    posterior_anchor: t("admin.js.md_posterior_anchor"),
    posterior_anchor_transform: t("admin.js.md_posterior_fit"),
    seed_view_baseline: t("admin.js.md_seed_view"),
  };
  const mode = modeLabels[proposal.mode] || t("admin.js.md_anchor_fit");
  return `${proposal.coordinates.length} ${t("admin.js.pins_lc")} · ${proposal.viewLabel || proposal.pinView} · ${mode} · Δ ${formatCoordinate(proposal.averageDelta)}%`;
}

function curationStatusLabel(item) {
  if (item?.source === "admin_bodymap_curation" && ["reviewed", "production_curated"].includes(item?.curation_status)) {
    return t("admin.js.cs_reviewed");
  }
  if (item?.curation_status === "draft") return t("admin.js.cs_draft");
  if (item?.source === "admin_bodymap_curation") return item?.curation_status || t("admin.js.cs_admin_draft");
  return t("admin.js.cs_needs");
}

function sourceLabel(item) {
  if (item?.source === "admin_bodymap_curation") return t("admin.js.src_admin");
  if (String(item?.source || "").includes("normalized")) return t("admin.js.src_legacy");
  return `${t("admin.js.src_prefix")}${item?.source || t("common.unknown")}`;
}

function averageProposalDelta(coordinates) {
  if (!coordinates.length) return 0;
  const total = coordinates.reduce((sum, coordinate) => {
    const dx = Number(coordinate.x) - Number(coordinate.currentX);
    const dy = Number(coordinate.y) - Number(coordinate.currentY);
    return sum + Math.sqrt(dx * dx + dy * dy);
  }, 0);
  return total / coordinates.length;
}

function curationItemByLatName(latName) {
  return curationCoordinates.find((coordinate) => coordinate.lat_name === latName) || null;
}

function seedPinFor(latName, side, view) {
  return matchingPin(curationItemByLatName(latName)?.seed_coordinates || [], { side, view });
}

function currentPinFor(latName, side, view) {
  return matchingPin(curationItemByLatName(latName)?.coordinates || [], { side, view });
}

function matchingPin(pins, reference) {
  return (pins || []).find((pin) => pin.side === reference.side && pin.view === reference.view)
    || (pins || []).find((pin) => pin.side === reference.side)
    || null;
}

function pinKey(pin) {
  return `${pin.view}:${pin.side}`;
}

function publicPinPayload(pin) {
  return {
    view: pin.view,
    x: roundCoordinate(pin.x),
    y: roundCoordinate(pin.y),
    side: pin.side,
  };
}

function roundCoordinate(value) {
  return Number(Number(value).toFixed(2));
}

function clampPercentage(value) {
  return Math.max(0, Math.min(100, Number(value)));
}

function confirmDiscardUnsavedMove() {
  if (!dirtyCurationLatName) return true;
  const confirmed = window.confirm(t("admin.js.discard_confirm", { lat: dirtyCurationLatName }));
  if (!confirmed) return false;
  const item = curationCoordinates.find((coordinate) => coordinate.lat_name === dirtyCurationLatName);
  if (item) item.coordinates = cloneCoordinates(originalCurationCoordinates.get(item.lat_name) || item.coordinates || []);
  dirtyCurationLatName = null;
  refreshCalibrationPreview();
  curationOutput.textContent = t("admin.js.move_discarded");
  return true;
}

function cloneCoordinates(coordinates) {
  return (coordinates || []).map((coordinate) => ({ ...coordinate }));
}

function curationViewDefinitions() {
  const bounds = bodymapAsset?.view_bounds || {};
  return {
    full: { i18nKey: "view.full", bounds: { x1: 0, y1: 0, x2: 100, y2: 100 } },
    anterior: { i18nKey: "view.anterior", bounds: bounds.anterior || { x1: 31, y1: 2, x2: 65, y2: 47 } },
    posterior: { i18nKey: "view.posterior", bounds: bounds.posterior || { x1: 31, y1: 52, x2: 66, y2: 96 } },
    cranial: { i18nKey: "view.cranial", bounds: bounds.cranial || { x1: 36, y1: 1, x2: 62, y2: 18 } },
    upper_limb: { i18nKey: "view.upper_limb", bounds: bounds.upper_limb || { x1: 22, y1: 8, x2: 73, y2: 32 } },
    posterior_upper_limb: { i18nKey: "view.post_upper", bounds: bounds.posterior_upper_limb || { x1: 30, y1: 58, x2: 68, y2: 76 } },
    lumbosacral: { i18nKey: "view.lumbosacral", bounds: bounds.lumbosacral || { x1: 27, y1: 23, x2: 70, y2: 70 } },
    lower_limb: { i18nKey: "view.lower_limb", bounds: bounds.lower_limb || { x1: 26, y1: 31, x2: 69, y2: 96 } },
  };
}

function currentCurationBounds() {
  return (curationViewDefinitions()[curationView] || curationViewDefinitions().full).bounds;
}

function applyCurationViewport() {
  const bounds = currentCurationBounds();
  const width = bounds.x2 - bounds.x1;
  const height = bounds.y2 - bounds.y1;
  const assetRatio = Number(bodymapAsset?.width || 1304) / Number(bodymapAsset?.height || 3033);
  curationMap.style.aspectRatio = String(assetRatio * (width / height));
  curationMap.style.backgroundSize = `${formatCoordinate(10000 / width)}% ${formatCoordinate(10000 / height)}%`;
  curationMap.style.backgroundPosition = `${formatCoordinate(backgroundOffset(bounds.x1, width))}% ${formatCoordinate(backgroundOffset(bounds.y1, height))}%`;
}

function projectCurationPoint(x, y) {
  const bounds = currentCurationBounds();
  if (!isPointInsideView(x, y, bounds)) return null;
  const width = bounds.x2 - bounds.x1;
  const height = bounds.y2 - bounds.y1;
  return { left: ((x - bounds.x1) / width) * 100, top: ((y - bounds.y1) / height) * 100 };
}

function eventToCurationPoint(event) {
  const rect = curationMap.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const left = ((event.clientX - rect.left) / rect.width) * 100;
  const top = ((event.clientY - rect.top) / rect.height) * 100;
  if (left < 0 || left > 100 || top < 0 || top > 100) return null;
  const bounds = currentCurationBounds();
  return {
    x: Number((bounds.x1 + (left / 100) * (bounds.x2 - bounds.x1)).toFixed(2)),
    y: Number((bounds.y1 + (top / 100) * (bounds.y2 - bounds.y1)).toFixed(2)),
  };
}

function isPointInsideView(x, y, bounds) {
  return Number.isFinite(Number(x)) && Number.isFinite(Number(y)) && x >= bounds.x1 && x <= bounds.x2 && y >= bounds.y1 && y <= bounds.y2;
}

function backgroundOffset(start, size) {
  if (size >= 100) return 0;
  return (start / (100 - size)) * 100;
}

function formatCoordinate(value) {
  return Number(value).toFixed(1).replace(/\.0$/, "");
}

function renderMetric(label, value, detail) {
  return `
    <div class="metric-card">
      <strong>${escapeHtml(value)}</strong>
      <span>${escapeHtml(label)}</span>
      <small>${escapeHtml(detail)}</small>
    </div>
  `;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

document.addEventListener("ionm:langchange", () => {
  if (currentSession) renderAuthenticated(); else renderLoggedOut();
});

init();
