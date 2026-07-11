"use strict";

const $ = (id) => document.getElementById(id);

const video = $("video");
const overlay = $("overlay");
const capture = $("capture");
const stageMessage = $("stageMessage");
const statusEl = $("status");
const statusText = $("statusText");
const knownBadge = $("knownBadge");
const camDot = $("camDot");
const enrollPanel = $("enrollPanel");
const qualityFeedback = $("qualityFeedback");
const nameInput = $("nameInput");
const peopleNames = $("peopleNames");
const enrollFace = $("enrollFace");
const saveName = $("saveName");
const cancelName = $("cancelName");
const peopleList = $("peopleList");
const peopleStatus = $("peopleStatus");
const settingsForm = $("settingsForm");
const settingsFields = $("settingsFields");
const settingsReset = $("settingsReset");
const settingsStatus = $("settingsStatus");
const overlayCtx = overlay.getContext("2d");
const captureCtx = capture.getContext("2d");

const LABEL_FONT =
  '600 14px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
const VIEWS = ["cameras", "people", "events", "settings"];

let settings = { scan_interval_ms: 450 };
let settingsSpec = null;
let scanTimer = null;
let scanInFlight = false;
let enrollInFlight = false;
let enrolling = false;
let lastFaces = [];
let lastFrame = { width: 1, height: 1 };
let activeView = "cameras";

// ---------- shared helpers ----------

async function api(path, options = {}) {
  const response = await fetch(`/api/v1${path}`, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function postJSON(payload) {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
}

function formatDate(iso) {
  const date = new Date(iso);
  return isNaN(date)
    ? iso
    : date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function plural(count, noun) {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

function button(label, classes) {
  const el = document.createElement("button");
  el.type = "button";
  el.className = `btn ${classes}`;
  el.textContent = label;
  return el;
}

function emptyState(text) {
  const el = document.createElement("div");
  el.className = "card empty-state muted";
  el.textContent = text;
  return el;
}

// ---------- routing ----------

function viewFromHash() {
  const name = (location.hash.match(/^#\/(\w+)/) || [])[1];
  return VIEWS.includes(name) ? name : "cameras";
}

function showView(name) {
  activeView = name;
  for (const view of VIEWS) $(`view-${view}`).hidden = view !== name;
  for (const link of document.querySelectorAll(".nav a")) {
    link.classList.toggle("active", link.dataset.view === name);
  }
  if (name === "cameras") drawFaces();
  if (name === "people") loadPeople();
  if (name === "settings") loadSettingsPage();
}

window.addEventListener("hashchange", () => showView(viewFromHash()));

// ---------- cameras ----------

function setStatus(text, kind = "info") {
  statusText.textContent = text;
  statusEl.className = `status ${kind}`;
}

function updateKnownBadge(count) {
  knownBadge.textContent = count === 1 ? "1 known face" : `${count} known faces`;
}

function sizeOverlay() {
  const rect = video.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  overlay.width = Math.max(1, Math.round(rect.width * dpr));
  overlay.height = Math.max(1, Math.round(rect.height * dpr));
  overlayCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return rect;
}

function drawFaces() {
  const rect = sizeOverlay();
  overlayCtx.clearRect(0, 0, rect.width, rect.height);
  const sx = rect.width / lastFrame.width;
  const sy = rect.height / lastFrame.height;
  for (const face of lastFaces) {
    const [x1, y1, x2, y2] = face.box;
    const known = face.name !== "Unknown";
    const color = known ? "#4ade80" : "#fbbf24";
    // The video is mirrored with scaleX(-1), so mirror the box to match.
    const rx = rect.width - x2 * sx;
    const ry = y1 * sy;
    const rw = (x2 - x1) * sx;
    const rh = (y2 - y1) * sy;
    overlayCtx.strokeStyle = color;
    overlayCtx.lineWidth = 2;
    overlayCtx.strokeRect(rx, ry, rw, rh);
    overlayCtx.font = LABEL_FONT;
    const label = face.name;
    const tw = overlayCtx.measureText(label).width;
    const lx = Math.max(8, Math.min(rect.width - tw - 24, rx));
    const ly = Math.max(8, ry - 30);
    overlayCtx.fillStyle = color;
    overlayCtx.beginPath();
    overlayCtx.roundRect(lx, ly, tw + 18, 24, 7);
    overlayCtx.fill();
    overlayCtx.fillStyle = "#0d1117";
    overlayCtx.fillText(label, lx + 9, ly + 17);
  }
}

function describeScan(data) {
  if (data.detected === 0) return "Watching for faces…";
  const names = [...new Set(lastFaces.map((face) => face.name))];
  return `In view: ${names.join(", ")}`;
}

async function scanFrame() {
  if (scanInFlight || document.hidden || activeView !== "cameras" || !video.videoWidth) return;
  scanInFlight = true;
  try {
    const maxWidth = 640;
    const scale = Math.min(1, maxWidth / video.videoWidth);
    capture.width = Math.round(video.videoWidth * scale);
    capture.height = Math.round(video.videoHeight * scale);
    captureCtx.drawImage(video, 0, 0, capture.width, capture.height);
    lastFrame = { width: capture.width, height: capture.height };
    const blob = await new Promise((resolve) =>
      capture.toBlob(resolve, "image/jpeg", 0.72)
    );
    if (!blob) return;
    const data = await api("/scan", {
      method: "POST",
      headers: { "Content-Type": "image/jpeg" },
      body: blob,
    });
    lastFaces = data.faces;
    enrollFace.disabled = lastFaces.length === 0;
    updateKnownBadge(data.known);
    const anyUnknown = lastFaces.some((face) => face.name === "Unknown");
    setStatus(
      describeScan(data),
      data.detected === 0 ? "info" : anyUnknown ? "warn" : "ok"
    );
    drawFaces();
    updateQuality();
  } catch (error) {
    setStatus(`Recognition error: ${error.message}`, "error");
  } finally {
    scanInFlight = false;
  }
}

function startScanLoop() {
  if (scanTimer) clearInterval(scanTimer);
  scanTimer = setInterval(scanFrame, settings.scan_interval_ms || 450);
}

async function startCamera() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    camDot.classList.add("off");
    stageMessage.textContent =
      "Camera access requires a secure context — open this page via localhost.";
    setStatus("Camera unavailable in this browser context.", "error");
    return;
  }
  try {
    video.srcObject = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: "user" },
      audio: false,
    });
    await video.play();
    stageMessage.classList.add("hidden");
    camDot.classList.add("live");
    setStatus("Camera ready.", "ok");
    drawFaces();
    startScanLoop();
  } catch (error) {
    camDot.classList.add("off");
    stageMessage.textContent = `Could not open camera: ${error.message}`;
    setStatus(`Could not open camera: ${error.message}`, "error");
  }
}

// ---------- enrollment ----------

function setQuality(text, kind) {
  qualityFeedback.textContent = text;
  qualityFeedback.className = `quality ${kind}`;
}

function largestFace() {
  let best = null;
  let bestArea = -1;
  for (const face of lastFaces) {
    const [x1, y1, x2, y2] = face.box;
    const area = Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
    if (area > bestArea) {
      best = face;
      bestArea = area;
    }
  }
  return best;
}

function updateQuality() {
  if (!enrolling) return;
  const face = largestFace();
  if (!face) {
    setQuality("No face in view — step into frame.", "warn");
    saveName.disabled = true;
    return;
  }
  if (!face.quality.ok) {
    setQuality(face.quality.issues.join(" "), "warn");
    saveName.disabled = true;
    return;
  }
  setQuality(
    face.name === "Unknown"
      ? "Looking good — hold still and enroll."
      : `Looking good — currently matched as ${face.name}.`,
    "ok"
  );
  saveName.disabled = enrollInFlight;
}

function openEnrollPanel() {
  enrolling = true;
  enrollPanel.classList.add("active");
  enrollFace.hidden = true;
  nameInput.value = "";
  nameInput.focus();
  updateQuality();
  loadPeopleNames();
}

function closeEnrollPanel() {
  enrolling = false;
  enrollPanel.classList.remove("active");
  enrollFace.hidden = false;
}

async function loadPeopleNames() {
  try {
    const data = await api("/people");
    peopleNames.replaceChildren(
      ...data.people.map((person) => {
        const option = document.createElement("option");
        option.value = person.name;
        return option;
      })
    );
  } catch {
    // The datalist is a nicety; enrollment works without it.
  }
}

async function enroll() {
  if (enrollInFlight) return;
  enrollInFlight = true;
  saveName.disabled = true;
  try {
    const data = await api("/enroll", postJSON({ name: nameInput.value.trim() }));
    closeEnrollPanel();
    updateKnownBadge(data.known);
    setStatus(data.status, "ok");
  } catch (error) {
    setQuality(error.message || "Could not enroll face.", "error");
  } finally {
    enrollInFlight = false;
    saveName.disabled = false;
  }
}

enrollFace.addEventListener("click", openEnrollPanel);
saveName.addEventListener("click", enroll);
cancelName.addEventListener("click", closeEnrollPanel);
nameInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    enroll();
  }
  if (event.key === "Escape") closeEnrollPanel();
});

// ---------- people ----------

function setPeopleStatus(text) {
  peopleStatus.textContent = text;
}

async function loadPeople() {
  setPeopleStatus("");
  try {
    const data = await api("/people");
    renderPeople(data.people);
  } catch (error) {
    peopleList.replaceChildren(emptyState(`Could not load people: ${error.message}`));
  }
}

function renderPeople(people) {
  if (!people.length) {
    peopleList.replaceChildren(
      emptyState("No one enrolled yet — enroll a face from the Cameras page.")
    );
    return;
  }
  peopleList.replaceChildren(...people.map(personCard));
}

function personCard(person) {
  const card = document.createElement("div");
  card.className = "card person";

  const info = document.createElement("div");
  info.className = "person-info";
  const nameEl = document.createElement("div");
  nameEl.className = "person-name";
  nameEl.textContent = person.name;
  const meta = document.createElement("div");
  meta.className = "person-meta";
  meta.textContent = `${plural(person.embedding_count, "enrollment")} · added ${formatDate(person.created_at)}`;
  info.append(nameEl, meta);

  const actions = document.createElement("div");
  actions.className = "person-actions";
  const detailsBtn = button("Details", "ghost");
  const renameBtn = button("Rename", "ghost");
  const deleteBtn = button("Delete", "ghost danger");
  actions.append(detailsBtn, renameBtn, deleteBtn);

  const row = document.createElement("div");
  row.className = "person-row";
  row.append(info, actions);

  const details = document.createElement("div");
  details.className = "person-details";
  details.hidden = true;

  card.append(row, details);

  detailsBtn.addEventListener("click", async () => {
    if (!details.hidden) {
      details.hidden = true;
      return;
    }
    await fillDetails(details, person.id);
    details.hidden = false;
  });

  renameBtn.addEventListener("click", () => startRename(card, person));

  deleteBtn.addEventListener("click", async () => {
    const what = `${person.name} and their ${plural(person.embedding_count, "enrollment")}`;
    if (!confirm(`Delete ${what}? This cannot be undone.`)) return;
    try {
      await api(`/people/${person.id}`, { method: "DELETE" });
      loadPeople();
    } catch (error) {
      setPeopleStatus(error.message);
    }
  });

  return card;
}

async function fillDetails(details, personId) {
  try {
    const person = await api(`/people/${personId}`);
    if (!person.embeddings.length) {
      const note = document.createElement("div");
      note.className = "muted";
      note.textContent = "No enrollments — this person can't be matched until you enroll their face.";
      details.replaceChildren(note);
      return;
    }
    details.replaceChildren(
      ...person.embeddings.map((embedding) => {
        const row = document.createElement("div");
        row.className = "embedding-row";
        const label = document.createElement("span");
        label.textContent = `Enrolled ${formatDate(embedding.created_at)}`;
        const remove = button("Remove", "ghost danger small");
        remove.addEventListener("click", async () => {
          try {
            await api(`/people/${personId}/embeddings/${embedding.id}`, { method: "DELETE" });
            loadPeople();
          } catch (error) {
            setPeopleStatus(error.message);
          }
        });
        row.append(label, remove);
        return row;
      })
    );
  } catch (error) {
    setPeopleStatus(error.message);
  }
}

function startRename(card, person) {
  if (card.querySelector(".rename-row")) return;
  const rename = document.createElement("div");
  rename.className = "name-row rename-row active";
  const input = document.createElement("input");
  input.value = person.name;
  input.maxLength = 64;
  const save = button("Save", "primary");
  const cancel = button("Cancel", "ghost");
  rename.append(input, save, cancel);
  card.append(rename);
  input.focus();
  input.select();

  const doRename = async () => {
    try {
      await api(`/people/${person.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: input.value }),
      });
      loadPeople();
    } catch (error) {
      setPeopleStatus(error.message);
    }
  };
  save.addEventListener("click", doRename);
  cancel.addEventListener("click", () => rename.remove());
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      doRename();
    }
    if (event.key === "Escape") rename.remove();
  });
}

// ---------- settings ----------

async function loadSettingsPage() {
  settingsStatus.textContent = "";
  try {
    const data = await api("/settings");
    settings = data.settings;
    settingsSpec = data.spec;
    renderSettingsFields();
  } catch (error) {
    settingsStatus.textContent = `Could not load settings: ${error.message}`;
  }
}

function renderSettingsFields() {
  settingsFields.replaceChildren(
    ...Object.entries(settingsSpec).map(([key, spec]) => {
      const field = document.createElement("label");
      field.className = "field";
      const title = document.createElement("span");
      title.className = "field-label";
      title.textContent = spec.label;
      const input = document.createElement("input");
      input.type = "number";
      input.name = key;
      input.min = spec.min;
      input.max = spec.max;
      input.step = spec.step;
      input.value = settings[key];
      input.required = true;
      const help = document.createElement("span");
      help.className = "field-help";
      help.textContent = spec.help;
      field.append(title, input, help);
      return field;
    })
  );
}

settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const updates = {};
  for (const input of settingsFields.querySelectorAll("input")) {
    updates[input.name] = Number(input.value);
  }
  try {
    const data = await api("/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    });
    settings = data.settings;
    startScanLoop();
    settingsStatus.textContent = "Saved.";
  } catch (error) {
    settingsStatus.textContent = error.message;
  }
});

settingsReset.addEventListener("click", () => {
  if (!settingsSpec) return;
  for (const input of settingsFields.querySelectorAll("input")) {
    input.value = settingsSpec[input.name].default;
  }
  settingsStatus.textContent = "Defaults restored — press Save to apply.";
});

// ---------- startup ----------

window.addEventListener("resize", () => {
  if (activeView === "cameras") drawFaces();
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && activeView === "cameras") drawFaces();
});

async function init() {
  showView(viewFromHash());
  try {
    const data = await api("/settings");
    settings = data.settings;
    settingsSpec = data.spec;
  } catch {
    // Defaults keep the scan loop running until the server responds.
  }
  try {
    const data = await api("/people");
    updateKnownBadge(data.people.filter((person) => person.embedding_count > 0).length);
  } catch {
    // The badge fills in on the first successful scan.
  }
  startCamera();
}

init();
