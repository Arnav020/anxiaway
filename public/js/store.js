// Everything Toward remembers about a person lives here, in their own browser. The server keeps nothing.
const CHECKS = "toward.checks.v1";
const PREFS = "toward.prefs.v1";
const DAY = 86400000;
let memoryChecks = [];
let memoryPrefs = {};

function load(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return key === CHECKS ? memoryChecks : memoryPrefs;
  }
}
function save(key, value) {
  if (key === CHECKS) memoryChecks = value; else memoryPrefs = value;
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode: kept for this page only */ }
}

export const bandOf = (score) => (score >= 15 ? "Severe" : score >= 10 ? "Moderate" : score >= 5 ? "Mild" : "Minimal");
export const BODY_TITLE = {
  CALIBRATING: "Baseline set", LOW: "Near your baseline", ENGAGED: "Above your baseline",
  HIGH: "Well above your baseline", LIMIT: "Far above your baseline", UNKNOWN: "Not trusted",
};
export const RECHECK_DAYS = 14;
export const SESSION_HOURS = 24;

/* Checks */
export function allChecks() {
  const list = load(CHECKS, []);
  return (Array.isArray(list) ? list : []).slice().sort((a, b) => a.at - b.at);
}
export const latestCheck = () => allChecks().at(-1) || null;
export function addCheck(entry) {
  const item = { id: `c-${Date.now().toString(36)}`, sample: false, ...entry };
  save(CHECKS, [...allChecks(), item].slice(-120));
  return item;
}
export const clearSampleChecks = () => save(CHECKS, allChecks().filter((c) => !c.sample));
export const clearChecks = () => save(CHECKS, []);
// Only what the fixed-code trend needs. Individual answers stay on this device.
export const checksForApi = () => allChecks().slice(-60).map((c) => ({ at: c.at, score: c.score, impact: c.impact ?? null, body_state: c.body?.state ?? null, sample: !!c.sample }));
export function daysSince(ms) { return Math.max(0, Math.round((Date.now() - ms) / DAY)); }
// A guided session rests on a recent check, because the safety questions come first.
export function sessionCheck() {
  const c = latestCheck();
  if (!c || !c.token || c.status !== "ok") return null;
  return Date.now() - c.at <= SESSION_HOURS * 3600000 ? c : null;
}

// Clearly fictional, and always labelled on screen.
const SAMPLE_CHECKS = [
  [-70, [2, 2, 2, 2, 1, 2, 2], 2, null],
  [-56, [2, 2, 2, 2, 1, 1, 2], 2, null],
  [-42, [2, 1, 2, 2, 1, 1, 1], 1, "ENGAGED"],
  [-28, [1, 1, 2, 2, 1, 1, 1], 1, "ENGAGED"],
  [-14, [1, 1, 1, 1, 1, 1, 1], 1, "LOW"],
  [-2, [1, 1, 1, 1, 0, 1, 1], 0, "LOW"],
];
export function loadSampleChecks() {
  const now = Date.now();
  const made = SAMPLE_CHECKS.map(([days, items, impact, state], i) => {
    const score = items.reduce((a, b) => a + b, 0);
    return {
      id: `sc-${i}`, at: now + days * DAY, items, score, band: bandOf(score), impact, status: "ok", sample: true, token: null,
      body: state ? { state, title: BODY_TITLE[state], source: "Demo stream", hr_delta: state === "LOW" ? 2.1 : 11.8, present: ["ECG", "PPG", "EDA", "IMU"] } : null,
      verdict: state ? { label: state === "LOW" ? "low" : "moderate", probability: state === "LOW" ? 0.21 : 0.56, source: "Simulated" } : null,
    };
  });
  save(CHECKS, [...allChecks().filter((c) => !c.sample), ...made]);
}

/* Preferences */
export const prefs = () => ({ ...load(PREFS, {}) });
export function setPrefs(patch) { save(PREFS, { ...prefs(), ...patch }); }

/* Small shared helpers */
export const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};
export const icon = (name, size = "") => {
  const i = el("i", `icon ${size}`.trim());
  i.style.setProperty("--i", `url(/icons/${name}.svg)`);
  return i;
};
export const dateText = (ms, opts = { day: "numeric", month: "short" }) => new Date(ms).toLocaleDateString(undefined, opts);
export function agoText(ms) {
  const d = daysSince(ms);
  return d === 0 ? "today" : d === 1 ? "yesterday" : `${d} days ago`;
}
export async function api(path, body) {
  const res = await fetch(`/api/${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : "That didn't work. Please try again.");
  }
  return res.json();
}

/* Drawing: one line chart used for scores and signals */
const NS = "http://www.w3.org/2000/svg";
export function lineChart(root, values, { min, max, color = "#2F3E52", fill = false, label = "", dots = false } = {}) {
  const w = 300, h = 100, pad = 8;
  const known = values.filter((v) => v !== null && v !== undefined);
  const lo = min ?? Math.min(...known, 0);
  const hi = max ?? Math.max(...known, 1);
  const x = (i) => pad + (values.length < 2 ? (w - pad * 2) / 2 : (i / (values.length - 1)) * (w - pad * 2));
  const y = (v) => pad + (1 - (v - lo) / (hi - lo || 1)) * (h - pad * 2);
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", label);
  let d = "";
  let open = false;
  values.forEach((v, i) => {
    if (v === null || v === undefined) { open = false; return; }
    d += `${open ? "L" : "M"}${x(i).toFixed(1)} ${y(Math.max(lo, Math.min(hi, v))).toFixed(1)}`;
    open = true;
  });
  if (fill && known.length > 1) {
    const area = document.createElementNS(NS, "path");
    area.setAttribute("d", `${d}L${x(values.length - 1).toFixed(1)} ${h}L${x(0).toFixed(1)} ${h}Z`);
    area.setAttribute("fill", color);
    area.setAttribute("opacity", ".12");
    svg.append(area);
  }
  const path = document.createElementNS(NS, "path");
  path.setAttribute("d", d);
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", color);
  path.setAttribute("stroke-width", 2.5);
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  path.setAttribute("vector-effect", "non-scaling-stroke");
  svg.append(path);
  root.replaceChildren(svg);
  if (dots) {
    values.forEach((v, i) => {
      if (v === null || v === undefined) return;
      const dot = el("i", "chart-dot");
      dot.style.left = `${(x(i) / w) * 100}%`;
      dot.style.top = `${(y(v) / h) * 100}%`;
      dot.style.background = color;
      root.append(dot);
    });
  }
}
