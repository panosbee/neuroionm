const loginForm = document.getElementById("login-form");
const loginOutput = document.getElementById("login-output");
const nextInput = document.getElementById("login-next");

const nextPath = safeNextPath(new URLSearchParams(window.location.search).get("next"));
if (nextInput) nextInput.value = nextPath;
if (loginForm) loginForm.action = `/api/admin/login?next=${encodeURIComponent(nextPath)}`;

checkExistingSession().catch(() => {});

loginForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginOutput.textContent = window.I18N ? window.I18N.t("login.checking") : "Έλεγχος στοιχείων...";
  const payload = Object.fromEntries(new FormData(loginForm).entries());
  try {
    const response = await fetch("/api/admin/login", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: payload.username, password: payload.password }),
    });
    if (!response.ok) throw new Error(await extractError(response));
    const data = await response.json().catch(() => ({}));
    window.location.assign(landingFor(data?.admin?.role));
  } catch (error) {
    loginOutput.textContent = error.message;
  }
});

function landingFor(role) {
  // Owners default to the control-plane console unless an explicit next was set.
  if (role === "owner" && nextPath === "/") return "/owner.html";
  return nextPath;
}

async function checkExistingSession() {
  const response = await fetch("/api/admin/session", { credentials: "include" });
  if (response.ok) window.location.assign(nextPath);
}

async function extractError(response) {
  const body = await response.text();
  try {
    const parsed = JSON.parse(body);
    return parsed.detail || response.statusText;
  } catch (error) {
    return body || response.statusText;
  }
}

function safeNextPath(value) {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return "/";
  try {
    const parsed = new URL(value, window.location.origin);
    if (parsed.origin !== window.location.origin || parsed.pathname === "/login.html") return "/";
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch (error) {
    return "/";
  }
}