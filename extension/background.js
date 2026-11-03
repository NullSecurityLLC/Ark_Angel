// ArkAngel Recorder: sends what you browse to `ark-angel browse` on this computer.
//
// Nothing is recorded unless Record is on, never in private windows, never on denylisted sites, and only to
// the local ArkAngel receiver. Pages whose capture fails to send are queued and retried.
const api = globalThis.browser ?? globalThis.chrome;

const DEFAULTS = {
  server: "http://127.0.0.1:8766",
  token: "",
  recording: false,
  session: "",
  captureText: true,
  denylist: [
    "mail.google.com", "accounts.google.com", "outlook.live.com", "outlook.office.com", "login.live.com",
    "login.microsoftonline.com", "mail.yahoo.com", "mail.proton.me", "icloud.com", "paypal.com", "venmo.com",
    "chase.com", "bankofamerica.com", "wellsfargo.com", "capitalone.com", "1password.com", "bitwarden.com",
    "lastpass.com",
  ],
  matches: 0,
  recent: [],
  queue: [],
  lastError: "",
};
const MAX_QUEUE = 300;
const MAX_RECENT = 15;
const DUPLICATE_MS = 10000;
const lastSent = new Map(); // url -> time, so reloads and SPA double-events aren't logged twice

async function settings() {
  return { ...DEFAULTS, ...(await api.storage.local.get(null)) };
}

function localTime() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
}

function hostOf(url) {
  try {
    return new URL(url).hostname.toLowerCase().replace(/^www\./, "");
  } catch {
    return "";
  }
}

function denied(url, cfg) {
  const host = hostOf(url);
  if (!/^https?:/i.test(url) || !host) return true;
  if (url.startsWith(cfg.server)) return true;
  return cfg.denylist.some((d) => host === d || host.endsWith("." + d));
}

async function badge() {
  const cfg = await settings();
  let text = "";
  let color = "#6b6355";
  if (cfg.queue.length) {
    text = "!";
    color = "#b3261e";
  } else if (cfg.recording && cfg.matches) {
    text = String(Math.min(cfg.matches, 999));
    color = "#e0b25c";
  } else if (cfg.recording) {
    text = "REC";
    color = "#b3261e";
  }
  await api.action.setBadgeText({ text });
  await api.action.setBadgeBackgroundColor({ color });
}

async function post(cfg, payload) {
  const response = await fetch(cfg.server.replace(/\/$/, "") + "/capture", {
    method: "POST",
    headers: { Authorization: "Bearer " + cfg.token, "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.error || "HTTP " + response.status);
    error.permanent = response.status >= 400 && response.status < 500 && response.status !== 401;
    throw error;
  }
  return body;
}

async function remember(result, payload) {
  const cfg = await settings();
  const recent = cfg.recent;
  for (const hit of result.hits || []) {
    recent.unshift({ kind: hit.kind, value: hit.value, seenIn: hit.seen_in, title: payload.title || payload.url,
      when: payload.when });
  }
  await api.storage.local.set({ recent: recent.slice(0, MAX_RECENT),
    matches: cfg.matches + ((result.hits || []).length ? 1 : 0), lastError: "" });
}

async function send(payload) {
  const cfg = await settings();
  if (!cfg.token) return;
  try {
    await flush(cfg);
    const result = await post(cfg, payload);
    await remember(result, payload);
  } catch (error) {
    if (!error.permanent) {
      const fresh = await settings();
      await api.storage.local.set({ queue: [...fresh.queue, payload].slice(-MAX_QUEUE) });
    }
    await api.storage.local.set({ lastError: String(error.message || error) });
  }
  await badge();
}

async function flush(cfg) {
  if (!cfg.queue.length) return;
  const pending = [...cfg.queue];
  while (pending.length) {
    const result = await post(cfg, pending[0]); // throws on failure; the rest stay queued
    await remember(result, pending.shift());
    await api.storage.local.set({ queue: pending });
  }
}

async function pageContent(tabId, withText) {
  try {
    const [injected] = await api.scripting.executeScript({
      target: { tabId },
      func: (wantText) => ({
        title: document.title,
        text: wantText && document.body ? document.body.innerText.slice(0, 300000) : "",
      }),
      args: [withText],
    });
    return injected?.result || {};
  } catch {
    return {}; // pages the browser won't let extensions read (stores, settings, PDFs)
  }
}

async function capture(tab, kind = "visit", extra = {}, force = false) {
  if (!tab || !tab.url || tab.incognito) return;
  const cfg = await settings();
  if ((!cfg.recording && !force) || denied(tab.url, cfg)) return;
  const now = Date.now();
  if (kind === "visit" && !force && now - (lastSent.get(tab.url) || 0) < DUPLICATE_MS) return;
  lastSent.set(tab.url, now);
  const page = kind === "visit" ? await pageContent(tab.id, cfg.captureText) : {};
  await send({ url: tab.url, kind, when: localTime(), session: cfg.session, title: page.title || tab.title || "",
    text: page.text || "", ...extra });
}

api.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (info.status === "complete") capture(tab);
});

api.webNavigation.onHistoryStateUpdated.addListener((details) => {
  if (details.frameId !== 0) return;
  setTimeout(async () => capture(await api.tabs.get(details.tabId).catch(() => null)), 1500); // let the SPA render
});

api.runtime.onInstalled.addListener(() => {
  api.contextMenus.create({ id: "ark-clip", title: "Add selection to ArkAngel case", contexts: ["selection"] });
  api.contextMenus.create({ id: "ark-page", title: "Add this page to ArkAngel case", contexts: ["page"] });
  badge();
});

api.contextMenus.onClicked.addListener(async (info, tab) => {
  // An explicit "add this" works even when Record is off: you asked for it.
  if (info.menuItemId === "ark-clip") await capture(tab, "clip", { selection: info.selectionText || "" }, true);
  if (info.menuItemId === "ark-page") await capture(tab, "visit", {}, true);
});

api.runtime.onMessage.addListener((message, _sender, reply) => {
  (async () => {
    if (message.type === "start") {
      const session = (globalThis.crypto?.randomUUID?.() || String(Date.now())).replace(/-/g, "").slice(0, 16);
      await api.storage.local.set({ recording: true, session, matches: 0 });
      const [tab] = await api.tabs.query({ active: true, currentWindow: true });
      await capture(tab);
    } else if (message.type === "stop") {
      await api.storage.local.set({ recording: false });
    } else if (message.type === "pause-site") {
      const cfg = await settings();
      const host = hostOf(message.url || "");
      if (host && !cfg.denylist.includes(host)) await api.storage.local.set({ denylist: [...cfg.denylist, host] });
    } else if (message.type === "retry") {
      const cfg = await settings();
      try {
        await flush(cfg);
        await api.storage.local.set({ lastError: "" });
      } catch (error) {
        await api.storage.local.set({ lastError: String(error.message || error) });
      }
    } else if (message.type === "status") {
      const cfg = await settings();
      try {
        const response = await fetch(cfg.server.replace(/\/$/, "") + "/status",
          { headers: { Authorization: "Bearer " + cfg.token } });
        const body = await response.json();
        reply(response.ok ? { ok: true, ...body } : { ok: false, error: body.error || "HTTP " + response.status });
      } catch {
        reply({ ok: false, error: "ArkAngel isn't listening. Run `ark-angel browse CASE_ID`." });
      }
      return;
    }
    await badge();
    reply({ ok: true });
  })();
  return true; // async reply
});
