const logoutButtons = document.querySelectorAll("[data-logout]");
const sessionNameTargets = document.querySelectorAll("[data-session-name]");
const sessionT = (key) => (window.I18N ? window.I18N.t(key) : key);

logoutButtons.forEach((button) => {
  button.addEventListener("click", async () => {
    button.setAttribute("disabled", "disabled");
    await fetch("/api/admin/logout", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    }).catch(() => {});
    window.location.assign("/login.html");
  });
});

if (sessionNameTargets.length) loadSessionLabel().catch(() => {
  sessionNameTargets.forEach((target) => {
    target.textContent = sessionT("session.active");
  });
});

async function loadSessionLabel() {
  const response = await fetch("/api/admin/session", { credentials: "include" });
  if (!response.ok) throw new Error("Session unavailable");
  const session = await response.json();
  const label = session.admin?.display_name || session.admin?.username || sessionT("session.active");
  sessionNameTargets.forEach((target) => {
    target.textContent = label;
  });
}