const api = globalThis.browser ?? globalThis.chrome;
const $ = (id) => document.getElementById(id);

function ask(type, extra = {}) {
  return new Promise((resolve) => api.runtime.sendMessage({ type, ...extra }, resolve));
}

async function render() {
  const cfg = await api.storage.local.get(null);
  $("server").value = cfg.server || "http://127.0.0.1:8766";
  $("token").value = cfg.token || "";
  $("captureText").checked = cfg.captureText !== false;
  if (cfg.denylist) $("denylist").value = cfg.denylist.join("\n");
  $("state").textContent = cfg.recording ? "recording" : "off";
  $("state").classList.toggle("on", Boolean(cfg.recording));
  $("record").textContent = cfg.recording ? "Stop" : "Record";
  $("setup").open = !cfg.token;

  const queued = (cfg.queue || []).length;
  $("retry").hidden = !queued;
  $("retry").textContent = `Send ${queued} queued capture(s)`;
  $("error").hidden = !cfg.lastError;
  $("error").textContent = cfg.lastError ? `Last send failed: ${cfg.lastError}` : "";

  const list = $("matches");
  list.replaceChildren();
  for (const hit of cfg.recent || []) {
    const item = document.createElement("li");
    const what = document.createElement("strong");
    what.textContent = `${hit.kind} ${hit.value}`;
    const where = document.createElement("span");
    where.className = "muted";
    where.textContent = ` on ${hit.title}, also in ${(hit.seenIn || []).join(", ")}`;
    item.append(what, where);
    list.append(item);
  }
  if (!list.children.length) list.innerHTML = '<li class="muted">None yet.</li>';

  if (cfg.token) {
    const status = await ask("status");
    $("case").textContent = status.ok
      ? `Case ${status.case}: ${status.title} · ${status.captured} captured this run`
      : status.error;
    $("case").className = status.ok ? "" : "error";
  }
}

$("record").addEventListener("click", async () => {
  const { recording } = await api.storage.local.get("recording");
  if (!recording && api.permissions?.request) {
    // Firefox asks for site access on first use; it needs this click to do it.
    const granted = await api.permissions.request({ origins: ["<all_urls>"] }).catch(() => true);
    if (!granted) return;
  }
  await ask(recording ? "stop" : "start");
  render();
});

$("pause").addEventListener("click", async () => {
  const [tab] = await api.tabs.query({ active: true, currentWindow: true });
  if (tab) await ask("pause-site", { url: tab.url });
  render();
});

$("retry").addEventListener("click", async () => {
  await ask("retry");
  render();
});

$("save").addEventListener("click", async () => {
  await api.storage.local.set({
    server: $("server").value.trim().replace(/\/$/, "") || "http://127.0.0.1:8766",
    token: $("token").value.trim(),
    captureText: $("captureText").checked,
    denylist: $("denylist").value.split("\n").map((s) => s.trim().toLowerCase().replace(/^www\./, "")).filter(Boolean),
  });
  render();
});

render();
