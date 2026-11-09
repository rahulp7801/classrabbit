"use strict";

const $ = (selector) => document.querySelector(selector);
let watches = [];
let events = [];
let selectedFilter = "all";
let toastTimer;
let searchRevision = 0;
let alertStream;
let lastAlertId = 0;

function el(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function toast(message, error = false) {
  clearTimeout(toastTimer);
  const node = $("#toast");
  node.textContent = message;
  node.classList.toggle("error", error);
  node.hidden = false;
  toastTimer = setTimeout(() => { node.hidden = true; }, 6500);
}

async function api(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 40000);
  try {
    const response = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", ...options.headers },
      signal: controller.signal,
    });
    if (response.status === 204) return null;
    const data = await response.json();
    if (!response.ok) {
      const message = typeof data.detail === "string" ? data.detail : "Check the term, subject area, and course number (for example 31 or 186W).";
      throw new Error(message);
    }
    return data;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("The request took too long. Please try again.");
    if (error instanceof TypeError) throw new Error("Could not reach the local app. Check that the server is running.");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function formattedTime(value) {
  return value ? new Date(value).toLocaleString([], { timeZone: "America/Los_Angeles", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) + " PT" : "Not yet checked";
}

function courseParts(item) {
  const full = item.snapshot?.course || item.label;
  const match = full.match(/(?:^|\s)([A-Z][A-Z &-]*\s+\d+[A-Z]*)\s+-\s+(.*)$/);
  return match ? { code: match[1], title: match[2] } : { code: "UCLA", title: full };
}

function safeLink(label, url, className = "") {
  const node = el("a", className, label);
  node.href = url;
  node.target = "_blank";
  node.rel = "noopener noreferrer";
  return node;
}

function openSearch() {
  $("#search-error").hidden = true;
  $("#search-dialog").showModal();
}

function renderCard(item) {
  const snapshot = item.snapshot;
  const parts = courseParts(item);
  const status = item.error || !snapshot ? "unknown" : snapshot.status;
  const card = el("article", "watch-card");
  card.dataset.testid = "watch-card";
  card.dataset.watchId = item.id;
  const top = el("div", "card-top");
  top.append(el("span", "course-code", parts.code), el("span", `status-badge ${status}`, status === "unknown" ? "Availability unknown" : `${item.stale ? "Last report · " : ""}${status[0].toUpperCase()}${status.slice(1)}`));
  const heading = el("h2", "", parts.title);
  const section = el("p", "section-meta", `${snapshot?.section || "Section"} · ${item.term} · Class ID ${item.class_id}`);
  const seats = el("div", "seats-line");
  seats.append(el("span", `seat-count ${status}`, status === "unknown" || snapshot?.available == null ? "—" : String(snapshot.available)), document.createTextNode(snapshot?.capacity && status !== "unknown" ? `of ${snapshot.capacity} seats left` : "seats available · unconfirmed"));
  const track = el("div", "progress-track");
  const fill = el("div", "progress-fill");
  fill.style.width = `${snapshot?.capacity && status !== "unknown" ? Math.min(100, (snapshot.available || 0) / snapshot.capacity * 100) : 0}%`;
  track.append(fill);
  card.append(top, heading, section, seats, track);
  if (item.error) {
    const error = el("p", "card-note error", `Check failed: ${item.error}`);
    error.setAttribute("role", "status");
    card.append(error);
    if (snapshot) card.append(el("p", "card-note", `Previous successful report: ${snapshot.status}, ${snapshot.available ?? "unknown"} seats left. This is historical data.`));
  }
  card.append(el("p", "card-note", `Last successful check: ${formattedTime(snapshot?.checked_at)}`));
  if (snapshot) {
    const details = el("details");
    details.append(el("summary", "", "Source & enrollment details"),
      el("p", "", snapshot.source_notice), el("p", "", `Waitlist: ${snapshot.waitlist}`),
      el("p", "", `Restrictions: ${snapshot.restrictions}`),
      safeLink("View UCLA section ↗", snapshot.source_url));
    card.append(details);
  }
  const actions = el("div", "card-actions");
  const refresh = el("button", "text-button", "↻ Check section");
  refresh.addEventListener("click", () => refreshWatch(item.id, refresh));
  const remove = el("button", "remove-watch", "Remove");
  remove.setAttribute("aria-label", `Remove ${parts.code} ${snapshot?.section || item.class_id}`);
  remove.addEventListener("click", () => removeWatch(item.id, remove));
  actions.append(refresh, remove, safeLink("MyUCLA ↗", snapshot?.enrollment_url || "https://my.ucla.edu/directLink.aspx?featureID=203"));
  card.append(actions);
  return card;
}

function renderDashboard() {
  $("#nav-count").textContent = String(watches.length);
  $("#total-count").textContent = String(watches.length);
  $("#filter-total").textContent = String(watches.length);
  $("#open-count").textContent = String(watches.filter(w => !w.error && w.snapshot?.status === "open").length);
  $("#closed-count").textContent = String(watches.filter(w => !w.error && ["closed", "waitlist"].includes(w.snapshot?.status)).length);
  const visible = watches.filter(w => selectedFilter === "all" || (selectedFilter === "open" ? !w.error && w.snapshot?.status === "open" : !w.error && ["closed", "waitlist"].includes(w.snapshot?.status)));
  $("#watch-cards").replaceChildren(...visible.map(renderCard));
  $("#empty-state").hidden = watches.length !== 0;
  $("#no-filter-results").hidden = watches.length === 0 || visible.length > 0;
  $("#empty-events").hidden = events.length > 0;
  $("#activity-dot").hidden = events.length === 0;
  $("#event-list").replaceChildren(...events.map(event => {
    const row = el("article", "event");
    row.dataset.testid = "reopening-event";
    const copy = el("div");
    const parts = courseParts({ snapshot: event.snapshot });
    copy.append(el("strong", "", `${parts.code} · ${event.snapshot.section} reopened`),
      el("p", "", `${event.snapshot.available} seats reported available · ${formattedTime(event.observed_at)}`));
    row.append(el("span", "event-icon", "↗"), copy, safeLink("MyUCLA ↗", event.snapshot.enrollment_url));
    return row;
  }));
}

async function loadDashboard() {
  const data = await Promise.all([api("/api/watchlist"), api("/api/events")]);
  watches = data[0];
  events = data[1];
  renderDashboard();
}

async function addWatch(url, button) {
  button.disabled = true;
  const oldText = button.textContent;
  button.textContent = "Checking section…";
  try {
    const item = await api("/api/watchlist", { method: "POST", body: JSON.stringify({ source_url: url }) });
    await loadDashboard();
    $("#search-dialog").close();
    showView("watchlist");
    toast(item.error ? "Section saved. Its availability is unknown; the app will retry later." : "Added to your watchlist. Your first check sets the baseline.");
  } catch (error) {
    $("#search-error").textContent = error.message;
    $("#search-error").hidden = false;
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

async function refreshWatch(id, button) {
  button.disabled = true;
  button.textContent = "Checking…";
  try {
    const result = await api(`/api/watchlist/${id}/refresh`, { method: "POST" });
    await loadDashboard();
    if (result.watch.error) toast("Availability is unknown. The previous report is preserved; a retry is scheduled.", true);
    else if (result.reopened) toast("A seat opening was reported. Check MyUCLA to confirm and enroll.");
    else if (result.cached) toast(`Already checked. Next source check: ${formattedTime(result.watch.next_check_at)}.`);
    else toast("Section checked. No new reopening was detected.");
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
    button.textContent = "↻ Check section";
  }
}

async function removeWatch(id, button) {
  button.disabled = true;
  try {
    await api(`/api/watchlist/${id}`, { method: "DELETE" });
    await loadDashboard();
    toast("Section removed from your watchlist.");
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
  }
}

function showView(view) {
  const activity = view === "activity";
  $("#watchlist-view").hidden = activity;
  $("#activity-view").hidden = !activity;
  $("#page-title").replaceChildren(document.createTextNode(activity ? "Your activity" : "Your watchlist"), el("span", "", "."));
  $("#page-description").textContent = activity ? "A record of the openings you’re watching for." : "The classes you want. A little closer.";
  for (const name of ["watchlist", "activity"]) {
    const nav = $(`#nav-${name}`);
    nav.classList.toggle("active", view === name);
    if (view === name) nav.setAttribute("aria-current", "page");
    else nav.removeAttribute("aria-current");
  }
}

function connectAlerts() {
  if (alertStream) alertStream.close();
  lastAlertId = Math.max(lastAlertId, ...events.map(event => event.id), 0);
  alertStream = new EventSource(`/api/events/stream?after=${lastAlertId}`);
  alertStream.addEventListener("connected", () => {
    $("#alert-connection").textContent = "In-app alerts connected · this page must stay open";
  });
  alertStream.addEventListener("reopening", message => {
    const event = JSON.parse(message.data);
    if (event.id <= lastAlertId) return;
    lastAlertId = event.id;
    const parts = courseParts({ snapshot: event.snapshot });
    toast(`A seat opening was reported: ${parts.code} ${event.snapshot.section}. Check MyUCLA to confirm and enroll.`);
    $("#toast").dataset.eventId = String(event.id);
    loadDashboard().catch(error => toast(error.message, true));
  });
  alertStream.onerror = () => {
    $("#alert-connection").textContent = "In-app alerts disconnected · reconnecting…";
  };
}

$("#add-class").addEventListener("click", openSearch);
$("#empty-add").addEventListener("click", openSearch);
$("#close-search").addEventListener("click", () => $("#search-dialog").close());
$("#nav-watchlist").addEventListener("click", () => showView("watchlist"));
$("#nav-activity").addEventListener("click", () => showView("activity"));
$("#reload-list").addEventListener("click", async () => {
  const button = $("#reload-list");
  button.disabled = true;
  try { await loadDashboard(); toast("Watchlist updated from your saved checks."); }
  catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});
document.querySelectorAll(".filter").forEach(button => button.addEventListener("click", () => {
  selectedFilter = button.dataset.filter;
  document.querySelectorAll(".filter").forEach(node => {
    node.classList.toggle("active", node === button);
    node.setAttribute("aria-pressed", String(node === button));
  });
  renderDashboard();
}));

$("#search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#search-submit");
  button.disabled = true;
  button.textContent = "Searching UCLA…";
  $("#search-error").hidden = true;
  $("#search-results").replaceChildren();
  const revision = ++searchRevision;
  try {
    const params = new URLSearchParams({ term: $("#term").value, subject: $("#subject").value.trim().toUpperCase(), course: $("#course").value.trim().toUpperCase() });
    const result = await api(`/api/sections?${params}`);
    if (revision !== searchRevision) return;
    const heading = el("p", "result-heading", `${result.sections.length} lecture section${result.sections.length === 1 ? "" : "s"} found · choose your section`);
    const nodes = result.sections.map(section => {
      const row = el("div", "search-result");
      const copy = el("div");
      copy.append(el("strong", "", section.section), el("p", "", `Class ID ${section.class_id}`));
      const watched = watches.some(w => w.class_id === section.class_id && w.term === $("#term").value);
      const add = el("button", "button secondary", watched ? "Already watching" : "Watch section");
      add.disabled = watched;
      add.addEventListener("click", () => addWatch(section.url, add));
      row.append(copy, add);
      return row;
    });
    $("#search-results").replaceChildren(heading, ...nodes);
  } catch (error) {
    if (revision !== searchRevision) return;
    $("#search-error").textContent = error.message;
    $("#search-error").hidden = false;
  } finally {
    button.disabled = false;
    button.replaceChildren(document.createTextNode("Find sections "), el("span", "", "→"));
  }
});

for (const field of ["#term", "#subject", "#course"]) {
  for (const event of ["input", "change"]) {
    $(field).addEventListener(event, () => {
      searchRevision++;
      $("#search-results").replaceChildren();
      $("#search-error").hidden = true;
    });
  }
}

$("#link-form").addEventListener("submit", event => {
  event.preventDefault();
  addWatch($("#section-url").value.trim(), $("#link-form button"));
});

async function bootstrap() {
  const now = new Date();
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/Los_Angeles", year: "numeric", month: "numeric" }).formatToParts(now);
  const year = Number(parts.find(p => p.type === "year").value);
  const month = Number(parts.find(p => p.type === "month").value);
  const defaultYear = month >= 10 ? year + 1 : year;
  const defaultSeason = month >= 10 || month < 3 ? "W" : month < 6 ? "S" : "F";
  for (let y = year + 1; y >= year - 1; y--) {
    for (const [code, name] of [["F", "Fall"], ["S", "Spring"], ["W", "Winter"]]) {
      const option = el("option", "", `${name} ${y}`);
      option.value = `${String(y).slice(-2)}${code}`;
      option.selected = y === defaultYear && code === defaultSeason;
      $("#term").append(option);
    }
  }
  try {
    const [source] = await Promise.all([api("/api/source"), loadDashboard()]);
    $("#fixture-banner").hidden = source.mode === "live";
    $("#source-mode").textContent = source.mode === "live" ? "UCLA source" : "Simulated source";
    connectAlerts();
  } catch (error) {
    toast(error.message, true);
  }
}

bootstrap();
window.addEventListener("pagehide", () => { if (alertStream) alertStream.close(); });
window.addEventListener("pageshow", event => { if (event.persisted) connectAlerts(); });
setInterval(() => { if (!document.hidden) loadDashboard().catch(error => toast(error.message, true)); }, 30000);
