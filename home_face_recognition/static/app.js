"use strict";

const video = document.getElementById("video");
const overlay = document.getElementById("overlay");
const capture = document.getElementById("capture");
const stageMessage = document.getElementById("stageMessage");
const statusEl = document.getElementById("status");
const statusText = document.getElementById("statusText");
const knownBadge = document.getElementById("knownBadge");
const camDot = document.getElementById("camDot");
const nameRow = document.getElementById("nameRow");
const nameInput = document.getElementById("nameInput");
const saveFace = document.getElementById("saveFace");
const saveName = document.getElementById("saveName");
const cancelName = document.getElementById("cancelName");
const overlayCtx = overlay.getContext("2d");
const captureCtx = capture.getContext("2d");

const SCAN_INTERVAL_MS = Number(document.body.dataset.scanInterval) || 450;
const LABEL_FONT =
  '600 14px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';

let scanInFlight = false;
let saveInFlight = false;
let lastFaces = [];
let lastFrame = { width: 1, height: 1 };

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
  if (scanInFlight || document.hidden || !video.videoWidth) return;
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
    const response = await fetch("/scan", {
      method: "POST",
      headers: { "Content-Type": "image/jpeg" },
      body: blob,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    lastFaces = data.faces;
    saveFace.disabled = lastFaces.length === 0;
    updateKnownBadge(data.known);
    const anyUnknown = lastFaces.some((face) => face.name === "Unknown");
    setStatus(
      describeScan(data),
      data.detected === 0 ? "info" : anyUnknown ? "warn" : "ok"
    );
    drawFaces();
  } catch (error) {
    setStatus(`Recognition error: ${error.message}`, "error");
  } finally {
    scanInFlight = false;
  }
}

function closeNameRow() {
  nameRow.classList.remove("active");
}

async function saveCurrentFace() {
  if (saveInFlight) return;
  saveInFlight = true;
  saveName.disabled = true;
  try {
    const response = await fetch("/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: nameInput.value.trim() }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    closeNameRow();
    updateKnownBadge(data.known);
    setStatus(data.status, "ok");
  } catch (error) {
    setStatus(error.message || "Could not save face.", "error");
  } finally {
    saveInFlight = false;
    saveName.disabled = false;
  }
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
    setInterval(scanFrame, SCAN_INTERVAL_MS);
  } catch (error) {
    camDot.classList.add("off");
    stageMessage.textContent = `Could not open camera: ${error.message}`;
    setStatus(`Could not open camera: ${error.message}`, "error");
  }
}

saveFace.addEventListener("click", () => {
  nameRow.classList.add("active");
  nameInput.value = "";
  nameInput.focus();
  setStatus("Enter a name, then press Return or Save.", "info");
});

saveName.addEventListener("click", saveCurrentFace);
cancelName.addEventListener("click", closeNameRow);
nameInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") saveCurrentFace();
  if (event.key === "Escape") closeNameRow();
});
window.addEventListener("resize", drawFaces);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) drawFaces();
});

startCamera();
