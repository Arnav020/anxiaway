import { mountShell, deviceStatus } from "/js/shell.js";
import { wirePhotos } from "/js/lightbox.js";
import * as store from "/js/store.js";
import { el } from "/js/store.js";
import { maskChips } from "/js/ui.js";
import { SCENARIOS, STATE_SHORT, fetchDemo, fetchDevice, heartOf } from "/js/reading.js";

const $ = (id) => document.getElementById(id);
mountShell("vitals");
wirePhotos();

const KEEP = 90;
const STATE_TEXT = {
  LOW: "Heart rate is near the baseline. Nothing here would change what the agent plans.",
  ENGAGED: "Heart rate is clearly above the baseline. The agent holds its plan and keeps watching.",
  HIGH: "Heart rate has stayed well above the baseline. The agent gets more careful, and never pushes harder on a high reading.",
  LIMIT: "Heart rate has stayed far above the baseline. In a guided session, fixed code would pause here.",
  UNKNOWN: "Movement or a weak signal, so this stretch isn't trusted and isn't used. Nothing is guessed.",
  CALIBRATING: "Setting a baseline from the first reading.",
};
const series = { hr: [], eda: [], motion: [], q: [] };
let device = { configured: false, window_s: 10, stride_s: 5 };
let scenario = "calm";
let cursor = null;
let playing = false;
let queue = [];
let pending = null;
let timer = null;
let seconds = 0;

$("contract").textContent = `GET  TOWARD_DEVICE_API_URL
{
  "samples": [
    { "t": 0, "ecg_hr": 78.2, "ppg_hr": 77.9, "eda": 4.01, "motion": 0.03, "quality": 0.95 }
  ],
  "verdict": { "label": "low | moderate | high", "probability": 0.72 },
  "present": ["ECG", "PPG", "EDA", "IMU"]
}`;

for (let i = 0; i < 17; i += 1) $("windows").append(el("i"));

function push(key, value) {
  series[key].push(value);
  if (series[key].length > KEEP) series[key].shift();
}
function draw() {
  const pad = (list) => [...Array(KEEP - list.length).fill(null), ...list];
  store.lineChart($("ch-hr"), pad(series.hr), { min: 60, max: 130, color: "#A8553D", fill: true, label: "Heart rate over the last 90 seconds" });
  store.lineChart($("ch-eda"), pad(series.eda), { min: 3.6, max: 5.2, color: "#3E7A66", fill: true, label: "Skin conductance over the last 90 seconds" });
  store.lineChart($("ch-motion"), pad(series.motion), { min: 0, max: 0.6, color: "#2F3E52", fill: true, label: "Movement over the last 90 seconds" });
  store.lineChart($("ch-q"), pad(series.q), { min: 0, max: 100, color: "#A9772B", fill: true, label: "Signal quality over the last 90 seconds" });
}
function showSample(s) {
  const hr = heartOf(s);
  const trusted = s.quality >= 0.5;
  push("hr", trusted && hr !== null && hr !== undefined ? hr : null);
  push("eda", s.eda);
  push("motion", s.motion);
  push("q", s.quality * 100);
  $("sig-hr").textContent = hr === null || hr === undefined ? "–" : Math.round(hr);
  $("sig-hr-src").textContent = s.ecg_hr !== null ? "ECG" : s.ppg_hr !== null ? "PPG, ECG lost" : "No sensor";
  $("sig-eda").textContent = s.eda === null ? "–" : s.eda.toFixed(2);
  $("sig-motion").textContent = s.motion === null ? "–" : s.motion.toFixed(2);
  $("sig-q").textContent = Math.round(s.quality * 100);
  seconds += 1;
  const windows = seconds >= device.window_s ? Math.floor((seconds - device.window_s) / device.stride_s) + 1 : 0;
  $("win-count").textContent = `${windows} window${windows === 1 ? "" : "s"}`;
  [...$("windows").children].forEach((w, i) => w.classList.toggle("on", i < windows % 18));
  draw();
}
function showSummary(summary) {
  $("live-state").dataset.state = summary.state;
  $("live-state-text").textContent = STATE_SHORT[summary.state];
  $("live-text").textContent = STATE_TEXT[summary.state];
  maskChips($("v-mask"), summary.mask);
}
async function load() {
  const result = device.configured ? await fetchDevice(store.prefs().baseline) : await fetchDemo(scenario, cursor);
  if (!device.configured) cursor = result.cursor;
  return result;
}
async function tick() {
  if (!playing) return;
  if (!queue.length) {
    if (pending) { showSummary(pending); pending = null; }
    try {
      const result = await load();
      queue = result.samples.slice();
      pending = result.summary;
    } catch (err) {
      $("live-text").textContent = err.message;
      return setPlaying(false);
    }
    if (!playing) return;
  }
  showSample(queue.shift());
  timer = setTimeout(tick, device.configured ? 1000 : 140);
}
function setPlaying(on) {
  playing = on;
  clearTimeout(timer);
  $("live-dot").classList.toggle("on", on);
  $("live-toggle").textContent = on ? "Pause" : device.configured ? "Show live signals" : "Play demo stream";
  if (on) {
    $("live-state-text").textContent = "Reading";
    tick();
  } else if (!pending) {
    $("live-state-text").textContent = "Paused";
  }
}
$("live-toggle").addEventListener("click", () => setPlaying(!playing));

SCENARIOS.forEach(([value, label]) => {
  const chip = el("button", "chip", label);
  chip.type = "button";
  chip.setAttribute("role", "radio");
  chip.dataset.v = value;
  chip.addEventListener("click", () => {
    scenario = value;
    paintScenario();
    queue = [];
    pending = null;
    if (!playing) setPlaying(true);
  });
  $("scenarios").append(chip);
});
function paintScenario() {
  $("scenarios").querySelectorAll(".chip").forEach((c) => {
    c.classList.toggle("is-on", c.dataset.v === scenario);
    c.setAttribute("aria-checked", String(c.dataset.v === scenario));
  });
}
paintScenario();
draw();

deviceStatus().then((status) => {
  device = status;
  maskChips($("v-mask"), status.sensors.map((s) => ({ ...s, present: s.on_device })));
  $("contract-status").textContent = status.configured ? "The device API is set on this server." : "The device API isn't set on this server yet, so the wearable option shows as integrating soon.";
  if (!status.configured) return;
  $("v-status").textContent = "Connected";
  $("v-sub").textContent = "Your wearable is linked. These are your own signals, read by fixed code and compared with your own baseline.";
  $("live-source").textContent = "Wearable";
  $("scenario-box").hidden = true;
  $("live-toggle").textContent = "Show live signals";
}).catch(() => {
  maskChips($("v-mask"), ["ECG", "PPG", "EDA", "IMU", "EEG"].map((sensor) => ({ sensor, text: "", on_device: sensor !== "EEG", present: sensor !== "EEG" })));
});
document.addEventListener("visibilitychange", () => { if (document.hidden && playing) setPlaying(false); });
