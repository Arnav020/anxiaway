// The prediction track record, stored only in this browser. Shared by the record, pitch and check-in pages.
const KEY = "toward.record.v1";
const REVIEW_KEY = "toward.record.review.v1";
const DAY = 86400000;
let memory = [];
let persistent = true;

export const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};
export const OUTCOME = { yes: "Came true", partly: "Partly", no: "Didn't happen" };
export const COMPARED = { worse: "Worse than expected", as_expected: "As bad as expected", better: "Better than expected" };
export const COPED = { 0: "Struggled", 1: "Got through it", 2: "Handled it well" };

function read() {
  try {
    const raw = localStorage.getItem(KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list : [];
  } catch {
    persistent = false;
    return memory;
  }
}
function write(list) {
  memory = list;
  try {
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    persistent = false;
  }
}
export const isPersistent = () => { read(); return persistent; };
export const all = () => read();
export const get = (id) => read().find((p) => p.id === id);
export const openOnes = () => read().filter((p) => !p.outcome);
export function add(entry) {
  const now = Date.now();
  const item = { id: `p-${now.toString(36)}${Math.floor(Math.random() * 1296).toString(36)}`, created_at: now, due_at: 0, outcome: null, compared: null, coped: null, belief_after: null, practised: 0, sample: false, source: "manual", how_to_check: "", event: "", theme: "other", ...entry };
  write([...read(), item]);
  return item;
}
export function update(id, patch) {
  write(read().map((p) => (p.id === id ? { ...p, ...patch } : p)));
}
export const remove = (id) => write(read().filter((p) => p.id !== id));
export const clearSample = () => write(read().filter((p) => !p.sample));
export function clearAll() {
  write([]);
  try { localStorage.removeItem(REVIEW_KEY); } catch { /* nothing to clear */ }
}

// Clearly fictional, and always labelled on screen.
const SAMPLE = [
  ["If I ask a question in class, people will think it's stupid.", "being judged", 80, "no", "better", 2, 45, -24],
  ["I'll freeze during my viva.", "performance", 70, "partly", "better", 1, 50, -21],
  ["If I suggest an idea, my group will ignore it.", "being judged", 65, "no", "better", 2, 40, -18],
  ["I'll fail the class test.", "performance", 75, "yes", "as_expected", 1, 60, -16],
  ["If I call the office, they'll be annoyed with me.", "being judged", 60, "no", "better", 2, 30, -14],
  ["If I eat alone in the canteen, people will stare.", "being judged", 70, "no", "better", 2, 35, -12],
  ["My friend is angry because I didn't reply.", "relationships", 60, "no", "better", 2, 30, -9],
  ["My voice will shake when I present.", "performance", 85, "yes", "as_expected", 1, 65, -6],
  ["If I speak to my mentor, I'll forget what to say.", "performance", 65, "no", "better", 2, 40, -3],
];
export function loadSample(includeOpen = true) {
  const now = Date.now();
  const made = SAMPLE.map(([text, theme, belief, outcome, compared, coped, after, days], i) => ({
    id: `s-${i}`, text, theme, belief_before: belief, outcome, compared, coped, belief_after: after, sample: true, source: "sample",
    created_at: now + days * DAY, due_at: now + (days + 2) * DAY, how_to_check: "", event: "", practised: 0,
  }));
  if (includeOpen) made.push({ id: "s-due", text: "If I go to the party, I'll have nobody to talk to.", theme: "relationships", belief_before: 60, outcome: null, compared: null, coped: null, belief_after: null, sample: true, source: "sample", created_at: now - 4 * DAY, due_at: now - DAY, how_to_check: "Count whether anyone talks with you for a few minutes.", event: "", practised: 0 });
  if (includeOpen) made.push({ id: "s-open", text: "If I present on Friday, people will notice I'm nervous.", theme: "being judged", belief_before: 75, outcome: null, compared: null, coped: null, belief_after: null, sample: true, source: "sample", created_at: now - DAY, due_at: now + 5 * DAY, how_to_check: "Note whether anyone mentions it afterwards.", event: "Presenting on Friday", practised: 0 });
  write([...read().filter((p) => !p.sample), ...made]);
}

export async function post(path, body) {
  const res = await fetch(`/api/${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : "That didn't work. Your record is safe on this device. Please try again.");
  }
  return res.json();
}
// The latest entries, so a very long record can't exceed the request limit.
export const forApi = () => read().slice(-60);
const reviewKey = (list) => list.map((p) => `${p.id}:${p.outcome || ""}:${p.belief_after ?? ""}:${p.practised || 0}`).join("|");
export function cachedReview() {
  try {
    const saved = JSON.parse(localStorage.getItem(REVIEW_KEY) || "null");
    return saved && saved.key === reviewKey(read()) ? saved.result : null;
  } catch {
    return null;
  }
}
export function saveReview(result) {
  try { localStorage.setItem(REVIEW_KEY, JSON.stringify({ key: reviewKey(read()), result })); } catch { /* optional */ }
}
export function applyFollowUps(followUps) {
  Object.entries(followUps || {}).forEach(([id, when]) => { if (get(id) && !get(id).outcome) update(id, { due_at: when }); });
}

/* Shared drawing */
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export function renderBars(root, stats) {
  const row = (label, value, cls) => {
    const wrap = el("div", "fbar");
    const head = el("div", "fbar-head");
    head.append(el("span", "title-4", label), el("strong", "heading-5 mono", value === null ? "None yet" : `${value}%`));
    const track = el("div", "fbar-track");
    const fill = el("i", cls);
    fill.style.transform = "scaleX(0)";
    track.append(fill);
    wrap.append(head, track);
    requestAnimationFrame(() => requestAnimationFrame(() => { fill.style.transform = `scaleX(${(value || 0) / 100})`; }));
    return wrap;
  };
  root.replaceChildren(row("How likely it felt, on average", stats.forecast_pct, "fill-forecast"), row("How often it happened", stats.actual_pct, "fill-actual"));
}
const TOOL_LABEL = {
  safety_scan: "Safety check", get_screening_trend: "Read the anxiety checks", get_record_stats: "Read the numbers", list_open_predictions: "Read open predictions",
  list_checked_predictions: "Read checked predictions", compare_themes: "Compared themes", schedule_follow_up: "Scheduled a follow up", finish_review: "Made a decision",
};
export async function renderTrace(list, trace, animate = false) {
  list.replaceChildren();
  for (const step of trace || []) {
    const li = el("li", `trace-item${step.rejected ? " rejected" : ""}`);
    li.append(
      el("i", `dot ${step.by === "Groq" ? "dot-llm" : "dot-code"}`),
      el("span", "title-4", TOOL_LABEL[step.tool] || step.tool),
      el("span", "caption-5 muted trace-by", step.by === "Groq" ? "Agent's choice, run by fixed code" : step.by === "Scripted" ? "Scripted fallback" : "Fixed code"),
      el("span", "body-6 trace-sum", step.summary),
    );
    list.append(li);
    if (animate && !reduced) await sleep(380);
  }
}
export function segGroup(root, options, onChange) {
  root.classList.add("seg");
  root.setAttribute("role", "radiogroup");
  root.replaceChildren(...options.map(([value, label]) => {
    const b = el("button", "", label);
    b.type = "button";
    b.setAttribute("role", "radio");
    b.setAttribute("aria-checked", "false");
    b.addEventListener("click", () => {
      root.querySelectorAll("button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
      onChange(value);
    });
    return b;
  }));
}
export function downloadIcs(summary, description, when) {
  const stamp = (d) => d.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  const escape = (t) => t.replace(/[\\;,]/g, (c) => `\\${c}`).replace(/\n/g, " ");
  const start = new Date(when);
  const ics = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Toward//Anxiety check-in//EN", "BEGIN:VEVENT",
    `UID:${Date.now()}@toward`, `DTSTAMP:${stamp(new Date())}`, `DTSTART:${stamp(start)}`, `DTEND:${stamp(new Date(start.getTime() + 900000))}`,
    `SUMMARY:${escape(summary)}`, `DESCRIPTION:${escape(description)}`, "END:VEVENT", "END:VCALENDAR"].join("\r\n");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([ics], { type: "text/calendar" }));
  link.download = "toward-check.ics";
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}
