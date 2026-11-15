import { mountShell, deviceStatus } from "/js/shell.js";
import { openConnect } from "/js/connect.js";
import * as store from "/js/store.js";
import { el } from "/js/store.js";
import { gauge, maskChips, runAgent } from "/js/ui.js";
import { SCENARIOS, STATE_SHORT, fetchDemo, fetchDevice, heartOf, play } from "/js/reading.js";

const $ = (id) => document.getElementById(id);
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
mountShell("check");

const GAD_ITEMS = [
  "Feeling nervous, anxious or on edge",
  "Not being able to stop or control worrying",
  "Worrying too much about different things",
  "Trouble relaxing",
  "Being so restless that it's hard to sit still",
  "Becoming easily annoyed or irritable",
  "Feeling afraid, as if something awful might happen",
];
const GAD_OPTIONS = ["Not at all", "Several days", "More than half the days", "Nearly every day"];
const IMPACT = ["Not difficult at all", "Somewhat difficult", "Very difficult", "Extremely difficult"];
const SAMPLE_GAD = [2, 1, 2, 1, 0, 1, 2];

const form = { consent: !!store.prefs().consent_at, self_harm: null, crisis: null, heart_condition: null, gad: Array(7).fill(null), impact: null, source: "none", body: null };
let usedSample = false;
let device = { configured: false };
let cursor = null;
let scenario = "calm";
let cancelPlay = () => {};
let reading = false;

function showError(id, message) {
  $(id).textContent = message || "";
  $(id).hidden = !message;
}

/* ---------- Steps, with a real back button ---------- */
const STEPS = ["s-intro", "s-safety", "s-gad", "s-impact", "s-body"];
const PIP = { "s-safety": 1, "s-gad": 2, "s-impact": 3, "s-body": 4, "s-result": 5 };
const BACK = { "s-intro": "Home", "s-safety": "Home", "s-gad": "Safety", "s-impact": "Questions", "s-body": "Daily life" };
let current = "s-intro";
const allZero = () => form.gad.every((v) => v === 0);

function show(id, push = true) {
  [...STEPS, "s-result", "s-exit"].forEach((s) => { $(s).hidden = s !== id; });
  current = id;
  const done = id === "s-result" || id === "s-exit";
  $("check-top").hidden = done;
  $("main").classList.toggle("page-narrow", id !== "s-result");
  $("back-label").textContent = id === "s-safety" && !store.prefs().consent_at ? "Before you start" : id === "s-body" && allZero() ? "Questions" : BACK[id] || "Back";
  $("sample").hidden = id !== "s-gad";
  const pip = PIP[id] || 0;
  $("pips").hidden = !pip;
  $("pips").setAttribute("aria-valuenow", pip || 1);
  [...$("pips").children].forEach((p, i) => p.classList.toggle("on", i < pip));
  if (push) history.pushState({ step: id }, "");
  scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" });
  const heading = $(id).querySelector("h1");
  if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); }
}
function goBack() {
  if (current === "s-intro" || (current === "s-safety" && store.prefs().consent_at)) { location.href = "/home"; return; }
  if (current === "s-body" && allZero()) { show("s-gad"); return; }
  show(STEPS[STEPS.indexOf(current) - 1]);
}
$("back").addEventListener("click", goBack);
addEventListener("popstate", (e) => {
  if (current === "s-result" || current === "s-exit") { location.href = "/home"; return; }
  if (e.state?.step && STEPS.includes(e.state.step)) show(e.state.step, false);
});

/* Consent */
function setConsent(on) {
  form.consent = on;
  $("agree").setAttribute("aria-checked", String(on));
  $("intro-next").disabled = !on;
}
$("agree").addEventListener("click", () => setConsent(!form.consent));
$("s-intro").addEventListener("submit", (e) => {
  e.preventDefault();
  if (!form.consent) return;
  show("s-safety");
});

/* Safety: asked before anything else, and answered by fixed code */
const FLAGS = { "q-harm": "self_harm", "q-crisis": "crisis", "q-heart": "heart_condition" };
function setFlag(id, value) {
  form[FLAGS[id]] = value;
  $(id).querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.v === String(value))));
}
Object.keys(FLAGS).forEach((id) => $(id).querySelectorAll("button").forEach((b) => b.addEventListener("click", () => setFlag(id, b.dataset.v === "true"))));
function exitWith(title, message) {
  $("exit-title").textContent = title;
  $("exit-text").textContent = message;
  show("s-exit", false);
}
$("s-safety").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (Object.values(FLAGS).some((k) => form[k] === null)) return showError("e-safety", "Please answer all three questions.");
  showError("e-safety");
  try {
    const result = await store.api("screen", { gad_items: Array(7).fill(0), self_harm: form.self_harm, heart_condition: false, crisis: form.crisis });
    if (result.status === "crisis") return exitWith("You deserve support right now", result.exit_message);
    show("s-gad");
  } catch (err) {
    showError("e-safety", err.message);
  }
});

/* GAD-7 */
function segButtons(root, options, onPick) {
  options.forEach((option, value) => {
    const b = el("button", "", option);
    b.type = "button";
    b.setAttribute("role", "radio");
    b.setAttribute("aria-checked", "false");
    b.addEventListener("click", () => {
      root.querySelectorAll("button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
      onPick(value);
    });
    root.append(b);
  });
}
GAD_ITEMS.forEach((item, i) => {
  const li = el("li");
  const seg = el("div", "seg");
  seg.setAttribute("role", "radiogroup");
  seg.setAttribute("aria-label", item);
  segButtons(seg, GAD_OPTIONS, (v) => { form.gad[i] = v; });
  li.append(el("span", "title-3", item), seg);
  $("gad").append(li);
});
$("sample").addEventListener("click", () => {
  usedSample = true;
  document.querySelectorAll("#gad .seg").forEach((seg, i) => seg.querySelectorAll("button")[SAMPLE_GAD[i]].click());
  $("gad").lastElementChild.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
});
$("s-gad").addEventListener("submit", (e) => {
  e.preventDefault();
  if (form.gad.includes(null)) return showError("e-gad", "Please answer all seven, so the result is accurate.");
  showError("e-gad");
  // The daily life question only applies if something was reported.
  if (allZero()) { form.impact = null; show("s-body"); } else show("s-impact");
});

/* Daily life */
segButtons($("impact"), IMPACT, (v) => { form.impact = v; });
$("s-impact").addEventListener("submit", (e) => {
  e.preventDefault();
  if (form.impact === null) return showError("e-impact", "Please pick the closest answer.");
  showError("e-impact");
  show("s-body");
});

/* ---------- Body reading ---------- */
const hrSeries = [];
function setSource(name) {
  form.source = name;
  for (const key of ["device", "demo", "none"]) $(`src-${key}`).setAttribute("aria-checked", String(key === name));
  $("reading").hidden = name === "none";
  $("scenarios").parentElement.hidden = name !== "demo";
  $("read-title").textContent = name === "device" ? "Wearable reading" : "Demo reading";
  $("read-note").textContent = name === "device"
    ? "The latest 45 seconds from your wearable, in 10 second windows. Fixed code reads it. No language model sees a raw reading."
    : "45 seconds of synthetic signal in 10 second windows, shown faster than real time. Fixed code reads it. No language model sees a raw reading.";
  if (name === "none") { cancelPlay(); form.body = null; reading = false; $("body-next").disabled = false; }
  else takeReading();
}
function resetReadout() {
  hrSeries.length = 0;
  $("read-chart").replaceChildren();
  ["rs-hr", "rs-delta", "rs-eda", "rs-q"].forEach((id) => { $(id).textContent = "–"; });
  $("read-progress").style.transition = "none";
  $("read-progress").style.transform = "scaleX(0)";
  $("read-state").dataset.state = "CALIBRATING";
  $("read-state-text").textContent = "Reading";
  $("read-dot").classList.add("on");
}
async function takeReading() {
  cancelPlay();
  showError("e-body");
  reading = true;
  form.body = null;
  $("body-next").disabled = true;
  resetReadout();
  try {
    const result = form.source === "device" ? await fetchDevice(store.prefs().baseline) : await fetchDemo(scenario, cursor);
    if (form.source === "demo") cursor = result.cursor;
    const { summary, samples } = result;
    maskChips($("read-mask"), summary.mask);
    const base = summary.baseline.hr;
    cancelPlay = play(samples, 5200, (s, i) => {
      hrSeries.push(s.quality >= 0.5 ? heartOf(s) : null);
      store.lineChart($("read-chart"), [...hrSeries, ...Array(samples.length - hrSeries.length).fill(null)], { min: 60, max: 130, color: "#A8553D", fill: true, label: "Heart rate during the reading" });
      const hr = heartOf(s);
      $("rs-hr").textContent = hr === null || hr === undefined ? "–" : `${Math.round(hr)}`;
      $("rs-delta").textContent = hr === null || hr === undefined || base === null || summary.baseline.set_now ? "–" : `${hr - base >= 0 ? "+" : ""}${(hr - base).toFixed(0)}`;
      $("rs-eda").textContent = s.eda === null ? "–" : s.eda.toFixed(2);
      $("rs-q").textContent = `${Math.round(s.quality * 100)}%`;
      $("read-progress").style.transform = `scaleX(${(i + 1) / samples.length})`;
    }, () => {
      reading = false;
      form.body = summary;
      $("read-dot").classList.remove("on");
      $("read-state").dataset.state = summary.state;
      $("read-state-text").textContent = STATE_SHORT[summary.state];
      $("body-next").disabled = false;
      if (form.source === "device" && summary.baseline.set_now) store.setPrefs({ baseline: { hr: summary.baseline.hr, eda: summary.baseline.eda, at: Date.now() } });
    });
  } catch (err) {
    reading = false;
    $("read-dot").classList.remove("on");
    $("read-state-text").textContent = "No reading";
    $("body-next").disabled = false;
    showError("e-body", `${err.message} You can still see your result without a reading.`);
  }
}
$("src-none").addEventListener("click", () => setSource("none"));
$("src-demo").addEventListener("click", () => setSource("demo"));
$("src-device").addEventListener("click", () => (device.configured ? setSource("device") : openConnect()));
SCENARIOS.forEach(([value, label]) => {
  const chip = el("button", "chip", label);
  chip.type = "button";
  chip.setAttribute("role", "radio");
  chip.dataset.v = value;
  chip.addEventListener("click", () => {
    scenario = value;
    paintScenario();
    takeReading();
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
deviceStatus().then((status) => {
  device = status;
  if (!status.configured) return;
  $("src-device").dataset.off = "false";
  $("src-device-tag").textContent = "Connected";
}).catch(() => {});

/* ---------- Result ---------- */
$("s-body").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (reading) return;
  showError("e-body");
  $("body-next").disabled = true;
  try {
    const result = await store.api("check", {
      gad_items: form.gad, impact: form.impact, self_harm: form.self_harm, heart_condition: form.heart_condition, crisis: form.crisis,
      consent: true, body: form.body ? { state: form.body.state, source: form.source === "device" ? "device" : "demo" } : null, history: store.checksForApi(),
    });
    if (result.status === "crisis") return exitWith("You deserve support right now", result.exit_message);
    if (!store.prefs().consent_at) store.setPrefs({ consent_at: Date.now() });
    const body = form.body;
    store.addCheck({
      at: result.at, items: form.gad, score: result.score, band: result.band, impact: result.impact, status: result.status,
      token: result.check_token, sample: usedSample,
      body: body ? { state: body.state, title: body.title, source: body.source, hr_delta: body.hr_delta, quality: body.quality, present: body.present } : null,
      verdict: body?.verdict || null,
    });
    renderResult(result, body);
  } catch (err) {
    showError("e-body", err.message);
  } finally {
    $("body-next").disabled = false;
  }
});

const AGREE_TITLE = { none: "Answers only", untrusted: "Reading not used", baseline: "Baseline set", differ: "They differ, and that's common", agree: "They point the same way" };
function renderResult(r, body) {
  $("r-date").textContent = store.dateText(r.at, { day: "numeric", month: "long", year: "numeric" });
  $("r-title").textContent = `${r.band} anxiety symptoms on screening.`;
  $("r-sub").textContent = `You scored ${r.score} of 21 on GAD-7. It's a screening result, not a diagnosis, and it sits beside your body reading without being merged into one number.`;

  $("r-support").hidden = r.status === "ok";
  if (r.status !== "ok") {
    $("r-support-title").textContent = r.score >= 15 ? "A person is the right next step" : "One thing before practice";
    $("r-support-text").textContent = r.exit_message;
  }

  gauge($("r-gauge"), r.score, { light: true });
  $("r-concerns").replaceChildren(...(r.concerns.length
    ? r.concerns.map((c) => el("li", "", c))
    : [el("li", "muted", "You didn't report being bothered by any of these.")]));
  $("r-impact").textContent = r.impact_text ? `Effect on daily life: ${r.impact_text.toLowerCase()}.` : "";

  $("r-body").textContent = body ? body.title : "No reading";
  $("r-body-text").textContent = body ? `${body.text}${body.heart_source ? ` Heart rate came from ${body.heart_source}.` : ""}` : "You skipped the body reading, or the wearable isn't linked yet.";
  $("r-body-stats").hidden = !body || body.state === "UNKNOWN";
  if (body) {
    $("rb-delta").textContent = body.hr_delta === null ? "–" : `${body.hr_delta > 0 ? "+" : ""}${body.hr_delta} bpm`;
    $("rb-q").textContent = body.quality === null ? "–" : `${Math.round(body.quality * 100)}%`;
  }
  $("r-body-tag").hidden = !body;
  $("r-body-tag").textContent = body ? `${body.source}${body.source === "Demo stream" ? ", synthetic" : ""}` : "";

  const verdict = body?.verdict;
  $("r-model").textContent = verdict ? (verdict.label === "unavailable" ? "No trusted reading" : `Anxiety-like: ${verdict.label}`) : "Not run";
  $("r-model-text").textContent = !body
    ? "The model reads body signals, so it needs a reading. Its design takes five inputs and works with whichever are present."
    : !verdict ? "A first reading sets your baseline, so there's nothing to compare yet."
      : verdict.source === "Model" ? `From our anxiety model${verdict.probability === null ? "" : `, probability ${Math.round(verdict.probability * 100)}%`}.`
        : `A stand-in driven by the demo signal${verdict.probability === null ? "" : ` (${Math.round(verdict.probability * 100)}%)`}. Our own model is still being trained and validated, so this is not its output.`;
  $("r-model-tag").textContent = !body ? "Needs a reading" : verdict?.source === "Model" ? "From our model" : "Simulated";
  maskChips($("r-mask"), body ? body.mask : device.sensors || []);

  $("r-agree").dataset.kind = r.agreement.kind;
  $("r-agree-title").textContent = AGREE_TITLE[r.agreement.kind];
  $("r-agree-text").textContent = r.agreement.text;
  $("r-notes").replaceChildren(...r.notes.map((text) => {
    const card = el("div", "note-card");
    card.append(store.icon("info", "i20"), el("p", "body-5", text));
    return card;
  }));

  $("r-path").textContent = r.pathway.name;
  $("r-why").textContent = r.pathway.why;
  $("r-steps").replaceChildren(...r.pathway.steps.map((s) => el("li", "", s)));
  const t = r.trend;
  $("r-trend").textContent = t.change === null
    ? `This is your first check. Screening looks back two weeks, so check again in ${r.recheck_days} days to see a trend.`
    : `Compared with your last check: ${t.change > 0 ? "+" : ""}${t.change} point${Math.abs(t.change) === 1 ? "" : "s"}, which counts as ${t.direction}.`;
  $("r-items").replaceChildren(...r.items.map((item) => {
    const li = el("li");
    const pips = el("span", "gad-pips");
    pips.setAttribute("aria-hidden", "true");
    [1, 2, 3].forEach((n) => pips.append(el("i", n <= item.value ? "on" : "")));
    const text = el("div");
    text.append(el("span", "body-6", item.text), el("span", "caption-5 muted", item.answer));
    li.append(text, pips);
    return li;
  }));

  const params = new URLSearchParams(location.search);
  const practice = $("r-practice");
  practice.hidden = r.status !== "ok";
  if (params.get("next") === "session") {
    const pred = params.get("pred");
    practice.href = pred ? `/session?pred=${encodeURIComponent(pred)}` : "/session";
    practice.firstChild.textContent = "Continue to your guided session";
  }
  show("s-result", false);
  runAgent($("agent"), { force: true });
}
$("print").addEventListener("click", () => print());

setConsent(form.consent);
history.replaceState({ step: form.consent ? "s-safety" : "s-intro" }, "");
show(form.consent ? "s-safety" : "s-intro", false);
