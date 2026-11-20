import { Panel } from "/js/panel.js";
import { Voices } from "/js/voice.js";
import { openConnect } from "/js/connect.js";
import * as ledger from "/js/ledger.js";
import * as store from "/js/store.js";
import { mountShell } from "/js/shell.js";

// A guided session rests on a check from the last 24 hours, because the safety questions come first.
const check = store.sessionCheck();
if (!check) location.replace(`/practice?need=check${location.search ? `&${location.search.slice(1)}` : ""}`);
mountShell("practice");

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const setIcon = (el, name) => el.style.setProperty("--i", `url(/icons/${name}.svg)`);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const SYMPTOMS = ["Racing heart", "Sweating", "Shaky", "Short of breath", "Tense muscles", "Butterflies", "Dry mouth", "Mind going blank", "None of these"];
const STATE_TEXT = {
  CALIBRATING: ["Baseline", "The first response starts the trace."],
  LOW: ["Low", "Little change from baseline. The next step gets a little harder."],
  ENGAGED: ["Engaged", "Clear change from baseline. The level holds and the session continues."],
  HIGH: ["High", "The level holds. High arousal alone doesn't lower difficulty, and stopping stays one tap away."],
  LIMIT: ["Limit", "Safety limit reached. The Governor paused the session."],
  UNKNOWN: ["Unknown", "Movement or a poor signal, so this reading isn't trusted or used."],
};
const NODE_NAMES = {
  governor: "Safety Governor", signal: "Signal Layer", model: "Anxiety model", policy: "Difficulty policy",
  director: "Director", scene: "Guide agent", check: "Output check", planner: "Care planner",
  safety: "Safety agent", verifier: "Verifier agent",
};
const INJECT_LABELS = { auto: "Next in script", calm: "Calm", rising: "Rising", high: "High", limit: "Safety limit", movement: "Movement", sensor_loss: "ECG lost" };
const SAMPLE_ANSWERS = [
  "I notice my chest feels tight and I want to find a reason to leave.",
  "I expect everyone to notice that I don't know what to say.",
  "My anxiety is telling me this will go badly and that I should get out now.",
  "I would take a breath, stay where I am and say what I actually think.",
  "I suppose I would just carry on, even if it isn't perfect.",
  "Maybe it would mean this isn't as dangerous as it feels.",
];
const SAMPLE_PROFILE = { event: "Going somewhere crowded", days: 7, pred: "If I stay in the crowd, my anxiety will get so bad that I'll have to leave.", belief: 80 };

/* ---------- State ---------- */
const form = { outcome: null, symptoms: new Set() };
const voices = new Voices();
let session = null;
let token = null;
let room = null;
let series = [];
let trailShown = 0;
let injection = "auto";
let soundOn = true;
let inFlight = false;
let stopToken = 0;
let speakToken = 0;
let answerOpenAt = null;
let firstInputAt = null;
let useVoice = false;
voices.onAmp = (v) => room?.setAmp(v);
voices.getToken = () => token;
$("connect-app").addEventListener("click", openConnect);
let usedSample = false;
let fromPrediction = null;
let savedToRecord = false;

async function api(path, body) {
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
const accept = (view) => { session = view; token = view.token; };
function showError(id, message) {
  $(id).textContent = message || "";
  $(id).hidden = !message;
}

/* ---------- Wizard, with a real back button on every step ---------- */
const STEPS = ["w-event", "w-fear", "w-connect", "w-plan"];
const BACK_LABEL = { "w-event": "Practice", "w-fear": "Situation", "w-connect": "Prediction", "w-plan": "Edit answers" };
const PROGRESS = { "w-event": 1, "w-fear": 2, "w-connect": 3 };
let current = "w-event";

function showStep(id, push = true) {
  [...STEPS, "w-loading"].forEach((s) => { $(s).hidden = s !== id; });
  current = id;
  $("wiz-back-label").textContent = BACK_LABEL[id] || "Back";
  $("wiz-back").hidden = id === "w-loading";
  $("wiz-sample").hidden = id !== "w-event";
  const bar = $("wiz-bar");
  const progress = PROGRESS[id];
  bar.hidden = !progress;
  bar.setAttribute("aria-valuenow", progress || 3);
  [...bar.children].forEach((pip, i) => pip.classList.toggle("on", i < (progress || 0)));
  $("wizard").classList.toggle("wide", id === "w-plan");
  if (push) history.pushState({ step: id }, "");
  scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" });
  const heading = $(id).querySelector("h1");
  if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); }
}
function goBack() {
  const index = STEPS.indexOf(current);
  if (index <= 0) location.href = "/practice";
  else if (current === "w-plan") showStep("w-event");
  else showStep(STEPS[index - 1]);
}
$("wiz-back").addEventListener("click", goBack);
addEventListener("popstate", (e) => {
  if (!$("stage").hidden) {
    history.pushState({ stage: true }, "");
    openLeave();
    return;
  }
  // After a session, back mustn't land on a blank state.
  if (!$("debrief").hidden) { history.pushState({ stage: true }, ""); return; }
  if (!$("report").hidden || !$("exit").hidden) { location.href = "/home"; return; }
  if (e.state?.step && !$("wizard").hidden && e.state.step !== "w-loading") showStep(e.state.step, false);
});
history.replaceState({ step: "w-event" }, "");

const daysText = (d) => (Number(d) === 1 ? "Tomorrow" : `In ${d} days`);
$("f-days").addEventListener("input", (e) => { $("o-days").textContent = daysText(e.target.value); });
$("f-belief").addEventListener("input", (e) => { $("o-belief").textContent = `${e.target.value}%`; });
$("f-after").addEventListener("input", (e) => { $("o-after").textContent = `${e.target.value}%`; });
// An untouched slider is "not rated", never a default 5.
let peakRated = false;
$("f-peak").addEventListener("input", (e) => { peakRated = true; $("o-peak").textContent = `${e.target.value} of 10`; });
function chipsFill(container, input) {
  container.querySelectorAll(".chip").forEach((chip) => chip.addEventListener("click", () => {
    input.value = chip.textContent;
    container.querySelectorAll(".chip").forEach((c) => c.classList.toggle("is-on", c === chip));
  }));
  input.addEventListener("input", () => container.querySelectorAll(".chip").forEach((c) => c.classList.remove("is-on")));
}
chipsFill($("event-chips"), $("f-event"));
chipsFill($("fear-chips"), $("f-pred"));

$("w-event").addEventListener("submit", (e) => {
  e.preventDefault();
  if ($("f-event").value.trim().length < 2) return showError("e-event", "Describe the situation, or pick one above.");
  showError("e-event");
  showStep("w-fear");
});
$("w-fear").addEventListener("submit", (e) => {
  e.preventDefault();
  if ($("f-pred").value.trim().length < 5) return showError("e-fear", "Write what your anxiety says will happen, or pick one above.");
  showError("e-fear");
  showStep("w-connect");
});

function marker(node, score) { node.style.left = `${Math.min(100, (score / 21) * 100)}%`; }
$("wiz-sample").addEventListener("click", () => {
  usedSample = true;
  const p = SAMPLE_PROFILE;
  $("f-event").value = p.event;
  $("f-days").value = p.days;
  $("o-days").textContent = daysText(p.days);
  $("f-pred").value = p.pred;
  $("f-belief").value = p.belief;
  $("o-belief").textContent = `${p.belief}%`;
  showStep("w-connect");
});

/* Build the plan */
$("w-connect").addEventListener("submit", async (e) => {
  e.preventDefault();
  showError("e-connect");
  showStep("w-loading", false);
  const started = performance.now();
  try {
    const view = await api("session", {
      event: $("f-event").value.trim(),
      days_until: Number($("f-days").value),
      prediction: $("f-pred").value.trim(),
      belief_before: Number($("f-belief").value),
      check_token: check.token,
      history: ledger.forApi(),
    });
    accept(view);
    if (view.status === "planned") renderPlan(performance.now() - started);
    else routeExit();
  } catch (err) {
    showStep("w-connect", false);
    showError("e-connect", err.message);
  }
});
function renderPlan(ms) {
  const plan = session.plan;
  const source = (session.trail.find((t) => t.who.startsWith("Ladder Planner")) || { who: "" }).who;
  $("plan-meta").textContent = source.includes("Groq") ? `· written live by Groq in ${(ms / 1000).toFixed(1)} s` : "· from the scripted planner";
  $("plan-path").textContent = session.pathway.name;
  $("plan-why").textContent = session.pathway.why;
  $("plan-record").hidden = !session.record_note;
  $("plan-record").textContent = session.record_note ? `From your track record: ${session.record_note}` : "";
  $("plan-gad").textContent = `${session.gad.score}/21 · ${session.gad.band}`;
  $("plan-event").textContent = session.event;
  const b = session.basis || {};
  const used = [`your GAD-7 range and strongest concerns`];
  if (b.impact) used.push(`how daily life has been (${b.impact.toLowerCase()})`);
  if (b.body) used.push(`the ${b.body_demo ? "demo " : ""}body reading at your check (${b.body.toLowerCase()})`);
  if (session.record_note) used.push("your prediction record");
  $("plan-basis-note").textContent = `This plan used ${used.join(", ")}.${b.gentle?.length ? ` It starts gently because ${b.gentle.join(" and ")}. That rule is fixed code, not the planner's choice.` : ""}`;
  $("plan-said").textContent = session.prediction;
  $("plan-test").textContent = plan.how_to_know || "Write down what actually happened, as plainly as you can.";
  $("ladder").replaceChildren(...plan.steps.map((step, i) => {
    const li = el("li", `rung rung-${i + 1}`);
    li.style.animationDelay = `${i * 90}ms`;
    li.append(el("span", "rung-day mono", `${i === 0 ? "Today · " : ""}Day ${step.day} · Level ${step.difficulty}`), el("strong", "title-3", step.title), el("span", "body-6 muted", step.test));
    return li;
  }));
  showStep("w-plan");
}

/* ---------- Guided session ---------- */
$("enter-btn").addEventListener("click", () => {
  voices.unlock();
  document.body.classList.add("in-stage");
  $("wizard").hidden = true;
  $("stage").hidden = false;
  history.pushState({ stage: true }, "");
  room = room || new Panel($("room-panel"));
  $("read-text").textContent = session.read_aloud;
  $("calibrate").hidden = false;
  $("cal-btn").hidden = false;
  $("cal-progress").hidden = true;
  $("data-label").textContent = session.data_label;
  buildInject();
  fitRoom();
});
$("cal-btn").addEventListener("click", async () => {
  voices.unlock();
  $("cal-btn").hidden = true;
  $("cal-progress").hidden = false;
  requestAnimationFrame(() => { $("cal-fill").style.transform = "scaleX(1)"; });
  try {
    const [view] = await Promise.all([api("calibrate", { token }), sleep(reduced ? 200 : 4000)]);
    accept(view);
    series = [];
    trailShown = 0;
    $("trail").replaceChildren();
    $("ev-pred").textContent = session.plan.testable_version;
    drawChart($("chart"));
    setState("CALIBRATING");
    renderTrail();
    renderLedger();
    playPipeline(view.pipeline);
    $("calibrate").hidden = true;
    $("dock").hidden = false;
    fitRoom();
    await panelTurn();
  } catch (err) {
    $("cal-btn").hidden = false;
    $("cal-progress").hidden = true;
    $("read-text").textContent = err.message;
  }
});

function setVerdict(verdict) {
  const node = $("hud-verdict");
  if (!verdict) { node.dataset.v = "unavailable"; $("hud-verdict-text").textContent = "Reading starts soon"; return; }
  node.dataset.v = verdict.label;
  const label = verdict.label === "unavailable" ? "No trusted reading" : `Anxiety-like: ${verdict.label}`;
  $("hud-verdict-text").textContent = `${label} · ${verdict.source.toLowerCase()}`;
}
function typewrite(node, text, durationMs, mine) {
  if (reduced) { node.textContent = text; return; }
  const words = text.split(" ");
  const each = Math.max(40, durationMs / words.length);
  node.textContent = "";
  words.forEach((w, i) => setTimeout(() => {
    if (mine === speakToken) node.textContent = words.slice(0, i + 1).join(" ");
  }, i * each));
}
function stopSpeaking() {
  speakToken += 1;
  voices.stop();
}
async function panelTurn() {
  const q = session.question;
  if (!q) return;
  const mine = ++speakToken;
  closeAnswerWindow();
  $("q-count").textContent = `Step ${session.turn + 1} of ${session.max_turns}`;
  room?.setCalm(false);
  room?.setPressure(q.level);
  room?.setTactic(q.tactic);
  $("caption").classList.remove("waiting");
  if (q.tactic === "hold_silence") {
    room?.setSpeaker(null);
    $("who").textContent = "Your guides";
    $("line").textContent = "They look at you, and wait.";
    $("caption").classList.add("waiting");
    await sleep(reduced ? 300 : 3000);
    if (mine !== speakToken) return;
    $("caption").classList.remove("waiting");
  }
  $("who").textContent = q.member;
  // Words appear right away, so nobody ever waits on a voice.
  typewrite($("line"), q.line, q.line.split(" ").length * 300, mine);
  room?.setSpeaker(q.member);
  await voices.speak(q.line, q.member, { sound: soundOn, isCurrent: () => mine === speakToken });
  if (mine !== speakToken) return;
  voices.stop();
  room?.setSpeaker(null);
  $("line").textContent = q.line;
  openAnswerWindow();
}

/* Responding: the time before you respond is actually measured */
let ringTimer = null;
const target = () => session?.plan?.target_pause_s || 3;
function updateMeter() {
  const s = ((firstInputAt ?? performance.now()) - answerOpenAt) / 1000;
  $("ring-text").textContent = s.toFixed(1);
  $("ring-fg").style.strokeDashoffset = String(176 * (1 - Math.min(1, s / target())));
  const pauseTest = /paus|silen/i.test(`${session.prediction} ${session.plan.steps[0].test}`);
  $("meter").classList.toggle("held", pauseTest && s >= target());
  if (firstInputAt) $("meter-title").textContent = `You took ${s.toFixed(1)} seconds before responding`;
  else if (pauseTest) $("meter-title").textContent = s >= target() ? "That's your pause. Respond when ready." : `Try holding ${target()} seconds before you respond`;
  else $("meter-title").textContent = "Take your time. There's no rush.";
}
function openAnswerWindow() {
  answerOpenAt = performance.now();
  firstInputAt = null;
  $("answer-box").classList.remove("locked");
  $("meter-sub").textContent = `Testing: ${session.plan.testable_version}`;
  $("answer").value = "";
  clearInterval(ringTimer);
  ringTimer = setInterval(updateMeter, 80);
  updateMeter();
  if (useVoice) startListening();
}
function closeAnswerWindow() {
  clearInterval(ringTimer);
  $("answer-box").classList.add("locked");
}
$("answer").addEventListener("input", () => {
  if (answerOpenAt && !firstInputAt && $("answer").value) firstInputAt = performance.now();
});

/* Voice capture */
let micStream = null;
let recorder = null;
let chunks = [];
let micCtx = null;
let micAnalyser = null;
let vadTimer = null;
let speechSeen = false;
let silentSince = null;
let waveRaf = null;
const VOICE_LEVEL = 0.035;
const END_SILENCE_MS = 2200;

async function enableMic() {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    showError("e-answer", "Voice isn't available in this browser. Type your response instead.");
    return false;
  }
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    const Ctx = window.AudioContext || window.webkitAudioContext;
    micCtx = new Ctx();
    micAnalyser = micCtx.createAnalyser();
    micAnalyser.fftSize = 1024;
    micCtx.createMediaStreamSource(micStream).connect(micAnalyser);
    return true;
  } catch {
    showError("e-answer", "Microphone access was blocked. Allow it in your browser, or type your response.");
    return false;
  }
}
function micLevel() {
  const data = new Uint8Array(micAnalyser.fftSize);
  micAnalyser.getByteTimeDomainData(data);
  let sum = 0;
  for (const v of data) sum += ((v - 128) / 128) ** 2;
  return { rms: Math.sqrt(sum / data.length), data };
}
function drawWave() {
  const c = $("wave");
  const g = c.getContext("2d");
  const paint = () => {
    if (!recorder || recorder.state !== "recording") { g.clearRect(0, 0, c.width, c.height); return; }
    const { data } = micLevel();
    g.clearRect(0, 0, c.width, c.height);
    g.strokeStyle = "#E4DCCB";
    g.lineWidth = 3;
    g.lineCap = "round";
    g.beginPath();
    for (let i = 0; i < c.width; i += 4) {
      const v = (data[Math.floor((i / c.width) * data.length)] - 128) / 128;
      const y = c.height / 2 + v * c.height * 1.2;
      if (i) g.lineTo(i, y); else g.moveTo(i, y);
    }
    g.stroke();
    waveRaf = requestAnimationFrame(paint);
  };
  paint();
}
function startListening() {
  if (!micStream || (recorder && recorder.state === "recording")) return;
  micCtx?.resume?.();
  chunks = [];
  speechSeen = false;
  silentSince = null;
  const type = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"].find((t) => MediaRecorder.isTypeSupported(t));
  recorder = type ? new MediaRecorder(micStream, { mimeType: type }) : new MediaRecorder(micStream);
  recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  recorder.start(250);
  $("mic-btn").classList.add("listening");
  setIcon($("mic-icon"), "stop-circle");
  $("mic-label").textContent = "Done";
  drawWave();
  clearInterval(vadTimer);
  const opened = performance.now();
  vadTimer = setInterval(() => {
    const { rms } = micLevel();
    const now = performance.now();
    if (rms > VOICE_LEVEL) {
      if (!speechSeen && now - opened > 150) {
        speechSeen = true;
        if (!firstInputAt) firstInputAt = now;
      }
      silentSince = null;
    } else if (speechSeen) {
      silentSince = silentSince ?? now;
      if (now - silentSince > END_SILENCE_MS) finishListening();
    }
    if (now - opened > 60000) finishListening();
  }, 60);
}
function stopRecorder() {
  clearInterval(vadTimer);
  cancelAnimationFrame(waveRaf);
  $("mic-btn").classList.remove("listening");
  setIcon($("mic-icon"), "microphone");
  $("mic-label").textContent = useVoice ? "Listening next" : "Use my voice";
  if (!recorder || recorder.state !== "recording") return Promise.resolve(null);
  return new Promise((resolve) => {
    const r = recorder;
    r.onstop = () => resolve(new Blob(chunks, { type: r.mimeType || "audio/webm" }));
    r.stop();
  });
}
async function finishListening() {
  if (inFlight) return;
  const pause = firstInputAt && answerOpenAt ? (firstInputAt - answerOpenAt) / 1000 : null;
  const blob = await stopRecorder();
  if (!blob || !speechSeen) {
    showError("e-answer", "We didn't catch any speech. Try again, or type your response.");
    return;
  }
  closeAnswerWindow();
  $("meter-title").textContent = "Transcribing what you said";
  $("meter-sub").textContent = "";
  try {
    const res = await fetch("/api/transcribe", { method: "POST", headers: { "Content-Type": (blob.type || "audio/webm").split(";")[0], "X-Session-Token": token }, body: blob });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Speech recognition failed. Type your response instead.");
    const heard = await res.json();
    if (!heard.text) throw new Error("We didn't catch that. Try again, or type your response.");
    await submitAnswer(heard.text, "voice", pause, heard.words);
  } catch (err) {
    openAnswerWindow();
    showError("e-answer", err.message);
  }
}
$("mic-btn").addEventListener("click", async () => {
  showError("e-answer");
  if (recorder && recorder.state === "recording") {
    if (speechSeen) return finishListening();
    await stopRecorder();
    useVoice = false;
    $("mic-label").textContent = "Use my voice";
    return;
  }
  if (!micStream && !(await enableMic())) return;
  useVoice = true;
  if (answerOpenAt) startListening();
});
$("sample-btn").addEventListener("click", () => {
  if (!inFlight) submitAnswer(SAMPLE_ANSWERS[session.turn % SAMPLE_ANSWERS.length], "sample", null, null);
});
$("answer-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("answer").value.trim();
  if (!text) return showError("e-answer", "Add a response first, or use your voice.");
  const pause = firstInputAt && answerOpenAt ? (firstInputAt - answerOpenAt) / 1000 : null;
  submitAnswer(text, "typed", pause, null);
});
$("answer").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("answer-form").requestSubmit(); }
});

async function submitAnswer(text, source, pause, words) {
  if (inFlight) return;
  showError("e-answer");
  inFlight = true;
  stopSpeaking();
  closeAnswerWindow();
  await stopRecorder();
  const mine = stopToken;
  $("who").textContent = "You";
  $("line").textContent = text;
  const inject = injection === "auto" ? null : injection;
  try {
    const view = await api("answer", { token, text, source, pause_s: pause, words, inject });
    if (mine !== stopToken) return;
    accept(view);
    setInjection("auto");
    await Promise.all([animateReadings(view.readings), playPipeline(view.pipeline)]);
    if (mine !== stopToken) return;
    const last = view.turn_log[view.turn_log.length - 1];
    if (last && view.readings.length) setState(last.state);
    setVerdict(view.verdict);
    renderTrail();
    renderLedger();
    inFlight = false;
    route();
  } catch (err) {
    openAnswerWindow();
    showError("e-answer", `${err.message} Your response wasn't sent.`);
  } finally {
    inFlight = false;
  }
}

/* ---------- Routing ---------- */
function leaveStage() {
  stopSpeaking();
  stopRecorder();
  clearInterval(ringTimer);
  micStream?.getTracks().forEach((t) => t.stop());
  micStream = null;
  useVoice = false;
  $("stage").hidden = true;
  document.body.classList.remove("in-stage");
}
function routeExit() {
  leaveStage();
  ["wizard", "debrief", "report"].forEach((id) => { $(id).hidden = true; });
  const titles = { referred: "Let's get you the right support", crisis: "You deserve support right now", medical: "Please look after yourself first" };
  $("exit-title").textContent = titles[session.status];
  store.helpText($("exit-text"), session.exit_message);
  // Something typed during practice pauses practice for a day, and then a new check comes first.
  // (A session only starts from a check that passed, so every exit here comes from what was typed.)
  store.setHold(session.status);
  $("exit-ended").hidden = false;
  $("exit-breathe").hidden = session.status !== "medical";
  $("exit").hidden = false;
  scrollTo(0, 0);
}
function buildPost() {
  $("symptoms").replaceChildren(...SYMPTOMS.map((name) => {
    const b = el("button", "chip", name);
    b.type = "button";
    b.setAttribute("aria-pressed", "false");
    b.addEventListener("click", () => {
      const on = !form.symptoms.has(name);
      if (name === "None of these" && on) form.symptoms.clear();
      else form.symptoms.delete("None of these");
      if (on) form.symptoms.add(name); else form.symptoms.delete(name);
      $("symptoms").querySelectorAll(".chip").forEach((c) => {
        c.classList.toggle("is-on", form.symptoms.has(c.textContent));
        c.setAttribute("aria-pressed", String(form.symptoms.has(c.textContent)));
      });
    });
    return b;
  }));
  $("q-outcome").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    form.outcome = b.dataset.v;
    $("q-outcome").querySelectorAll("button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
  }));
}
function route() {
  const s = session.status;
  if (s === "referred" || s === "crisis" || s === "medical") return routeExit();
  if (s === "paused") {
    stopSpeaking();
    room?.setCalm(true);
    room?.setSpeaker(null);
    $("who").textContent = "Your guides";
    $("line").textContent = "Everyone is waiting. Take the time you need.";
    $("caption").classList.add("waiting");
    $("pause-text").textContent = session.exit_message;
    $("breath").hidden = true;
    if (!$("pause-dlg").open) $("pause-dlg").showModal();
    return;
  }
  if (s === "debrief") {
    leaveStage();
    $("d-pred").textContent = `Your anxiety said: "${session.prediction}"`;
    $("d-note").textContent = session.exit_message
      ? `${session.exit_message} These answers help make your summary useful.`
      : "Your answers, your check and the readings all go into your summary.";
    $("d-before").textContent = `${session.belief_before}%`;
    $("f-after").value = session.belief_before;
    $("o-after").textContent = `${session.belief_before}%`;
    $("debrief").hidden = false;
    scrollTo(0, 0);
    return;
  }
  if (s === "complete") return renderReport();
  panelTurn();
}
let breathTimer = null;
async function resume(choice) {
  if (breathTimer) { clearInterval(breathTimer); breathTimer = null; }
  $("breath-btn").disabled = false;
  $("pause-dlg").close();
  try {
    accept(await api("resume", { token, choice }));
    setState("UNKNOWN");
    $("state-text").textContent = "Signal restarted after the pause.";
    renderTrail();
    route();
  } catch (err) {
    showError("e-answer", err.message);
  }
}
$("resume-go").addEventListener("click", () => resume("continue"));
$("resume-stop").addEventListener("click", () => resume("stop"));
$("pause-dlg").addEventListener("cancel", (e) => e.preventDefault());
$("breath-btn").addEventListener("click", () => {
  $("breath").hidden = false;
  $("breath-btn").disabled = true;
  let t = 0;
  const say = () => { $("breath-text").textContent = t % 10 < 4 ? "Breathe in" : "Breathe out"; };
  say();
  breathTimer = setInterval(() => {
    t += 1;
    if (t >= 30) {
      clearInterval(breathTimer);
      breathTimer = null;
      $("breath-text").textContent = "Whenever you're ready";
      $("breath-btn").disabled = false;
      return;
    }
    say();
  }, 1000);
});

async function stopSession() {
  stopToken += 1;
  stopSpeaking();
  inFlight = false;
  if ($("pause-dlg").open) $("pause-dlg").close();
  if ($("leave-dlg").open) $("leave-dlg").close();
  if (!token) return;
  try {
    accept(await api("stop", { token }));
  } catch {
    session = { ...session, status: "debrief", exit_message: "Session stopped. That's completely fine." };
  }
  renderTrail();
  route();
}
function backToPlan() {
  leaveStage();
  $("wizard").hidden = false;
  showStep("w-plan");
}
$("stop-btn").addEventListener("click", () => {
  // Before the session has started, there's nothing to end or summarise.
  if (!$("calibrate").hidden) backToPlan();
  else stopSession();
});
function openLeave() { if (!$("leave-dlg").open) $("leave-dlg").showModal(); }
$("leave-btn").addEventListener("click", () => {
  if (!$("calibrate").hidden) { backToPlan(); return; }
  openLeave();
});
$("leave-stay").addEventListener("click", () => $("leave-dlg").close());
$("leave-go").addEventListener("click", stopSession);

$("sound-btn").addEventListener("click", () => {
  soundOn = !soundOn;
  $("sound-btn").setAttribute("aria-pressed", String(soundOn));
  setIcon($("sound-icon"), soundOn ? "speaker-high" : "speaker-slash");
  if (!soundOn) voices.stop();
});

/* ---------- Inside the agent ---------- */
function fitRoom() {
  if (!room) return;
  const visible = !$("dock").hidden ? $("dock") : $("calibrate");
  const wideDrawer = !$("drawer").hidden && innerWidth > 1180;
  room.setLayout({ top: 72, bottom: visible.offsetHeight + 24, right: wideDrawer ? 432 : 0 });
}
addEventListener("resize", fitRoom);
if (window.ResizeObserver) new ResizeObserver(fitRoom).observe($("dock"));
function setDrawer(open) {
  $("drawer").hidden = !open;
  $("agent-btn").setAttribute("aria-expanded", String(open));
  $("stage").classList.toggle("drawer-open", open);
  fitRoom();
}
$("agent-btn").addEventListener("click", () => setDrawer($("drawer").hidden));
$("drawer-close").addEventListener("click", () => setDrawer(false));
document.querySelectorAll(".tabs button").forEach((tab) => tab.addEventListener("click", () => {
  document.querySelectorAll(".tabs button").forEach((t) => t.setAttribute("aria-selected", String(t === tab)));
  ["live", "evidence", "log"].forEach((name) => { $(`tab-${name}`).hidden = name !== tab.dataset.tab; });
}));
function buildInject() {
  $("inject").replaceChildren(...Object.keys(INJECT_LABELS).map((name) => {
    const b = el("button", "chip", INJECT_LABELS[name]);
    b.type = "button";
    b.dataset.v = name;
    b.setAttribute("role", "radio");
    b.addEventListener("click", () => setInjection(name));
    return b;
  }));
  setInjection("auto");
}
function setInjection(name) {
  injection = name;
  $("inject").querySelectorAll(".chip").forEach((c) => {
    c.classList.toggle("is-on", c.dataset.v === name);
    c.setAttribute("aria-checked", String(c.dataset.v === name));
  });
}
async function playPipeline(steps) {
  const list = $("pipeline");
  list.replaceChildren();
  for (const step of steps || []) {
    const li = el("li", "pipe");
    const dotClass = step.kind === "llm" ? "dot-llm" : step.kind === "model" ? "dot-model" : step.kind === "agent" ? "dot-agent" : "dot-code";
    const name = step.node === "safety" && step.kind !== "agent" ? "Safety backup rules" : NODE_NAMES[step.node] || step.node;
    li.append(
      el("i", `dot ${dotClass}`),
      el("span", "pipe-name", name + (step.source === "Scripted" ? " (scripted)" : "")),
      el("span", "pipe-ms", step.ms < 1 ? "<1 ms" : `${Math.round(step.ms)} ms`),
      el("span", "pipe-sum", step.summary),
    );
    list.append(li);
    requestAnimationFrame(() => li.classList.add("lit"));
    if (!reduced && !document.hidden && !$("drawer").hidden) await sleep(200);
  }
}
function setState(state) {
  const [label, text] = STATE_TEXT[state] || STATE_TEXT.UNKNOWN;
  $("state").dataset.state = state;
  $("state-label").textContent = label;
  $("state-text").textContent = text;
}

const CHART = { w: 400, h: 144, min: -5, max: 45 };
const yOf = (v) => 6 + (1 - (v - CHART.min) / (CHART.max - CHART.min)) * (CHART.h - 12);
const BANDS = [{ v: 5, label: "Engaged +5" }, { v: 20, label: "High +20" }, { v: 35, label: "Limit +35" }];
function drawChart(root, data = series, span = 240) {
  const NS = "http://www.w3.org/2000/svg";
  const xOf = (i) => 4 + (i / Math.max(data.length - 1, span)) * (CHART.w - 8);
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${CHART.w} ${CHART.h}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Line chart of heart rate change from baseline during the session.");
  BANDS.forEach((b) => {
    const line = document.createElementNS(NS, "line");
    line.setAttribute("x1", 0); line.setAttribute("x2", CHART.w);
    line.setAttribute("y1", yOf(b.v)); line.setAttribute("y2", yOf(b.v));
    line.setAttribute("stroke", "#C9C6BC"); line.setAttribute("stroke-width", 1);
    line.setAttribute("vector-effect", "non-scaling-stroke");
    svg.append(line);
  });
  let d = "";
  let open = false;
  data.forEach((p, i) => {
    if (p.delta === null) { open = false; return; }
    d += `${open ? "L" : "M"}${xOf(i).toFixed(1)} ${yOf(Math.max(CHART.min, Math.min(CHART.max, p.delta))).toFixed(1)}`;
    open = true;
  });
  const path = document.createElementNS(NS, "path");
  path.setAttribute("d", d);
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "#A8553D");
  path.setAttribute("stroke-width", 2.5);
  path.setAttribute("stroke-linejoin", "round");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("vector-effect", "non-scaling-stroke");
  svg.append(path);
  root.replaceChildren(svg);
  BANDS.forEach((b) => {
    const tag = el("span", "chart-tag", b.label);
    tag.style.top = `${(yOf(b.v) / CHART.h) * 100}%`;
    root.append(tag);
  });
}
function attachHover(root, getData, span) {
  root.addEventListener("pointermove", (ev) => {
    const data = getData();
    if (!data.length) return;
    const box = root.getBoundingClientRect();
    const frac = (ev.clientX - box.left) / box.width;
    const i = Math.max(0, Math.min(data.length - 1, Math.round(frac * Math.max(data.length - 1, span()))));
    const p = data[i];
    let tip = root.querySelector(".tip");
    if (!tip) { tip = el("span", "tip"); root.append(tip); }
    const value = p.delta === null ? "not trusted" : `${p.delta > 0 ? "+" : ""}${p.delta.toFixed(1)} bpm`;
    tip.textContent = `${p.t} s, ${value}, ${STATE_TEXT[p.state][0]}`;
    tip.style.left = `${Math.min(frac * box.width, box.width - 170)}px`;
  });
  root.addEventListener("pointerleave", () => root.querySelector(".tip")?.remove());
}
attachHover($("chart"), () => series, () => 240);
attachHover($("r-chart"), () => series, () => series.length);

function animateReadings(readings) {
  return new Promise((resolve) => {
    if (reduced || !readings?.length || document.hidden || $("drawer").hidden) {
      series.push(...(readings || []));
      drawChart($("chart"));
      return resolve();
    }
    const start = performance.now();
    let added = 0;
    const timer = setInterval(() => {
      const goal = document.hidden ? readings.length : Math.min(readings.length, Math.floor(((performance.now() - start) / 1400) * readings.length));
      if (goal > added) {
        series.push(...readings.slice(added, goal));
        added = goal;
        drawChart($("chart"));
      }
      if (added >= readings.length) { clearInterval(timer); resolve(); }
    }, 40);
  });
}
function trailItem(entry) {
  const li = el("li");
  li.dataset.kind = entry.who.startsWith("Safety agent") || entry.who.startsWith("Verifier") ? "agent" : entry.who.startsWith("Safety") || entry.who.startsWith("Backup") ? "governor" : entry.who.startsWith("Difficulty") ? "policy" : entry.who.startsWith("Anxiety") ? "model" : entry.who.includes("(") ? "llm" : "other";
  li.append(el("span", "t-who", entry.who), el("span", "t-ms", entry.ms ? `${entry.ms} ms` : ""), el("span", "t-what", entry.text));
  return li;
}
function renderTrail() {
  session.trail.slice(trailShown).forEach((e) => $("trail").prepend(trailItem(e)));
  trailShown = session.trail.length;
}
function renderLedger() {
  const list = $("ledger");
  if (!session.evidence.length) {
    list.replaceChildren(el("li", "empty", "Respond once. What you actually did appears here, measured rather than scored."));
    return;
  }
  list.replaceChildren(...[...session.evidence].reverse().map((e) => {
    const li = el("li");
    const head = e.source === "sample"
      ? `Step ${e.turn}: sample response, nothing measured`
      : e.pause_s === null ? `Step ${e.turn}: timing not measured` : `Step ${e.turn}: took ${e.pause_s} s before responding`;
    const bits = [];
    if (e.words) bits.push(`${e.words} words`);
    if (e.wpm) bits.push(`${e.wpm} words per minute`);
    if (e.longest_gap_s) bits.push(`longest gap ${e.longest_gap_s} s`);
    if (e.fillers) bits.push(`${e.fillers} filler${e.fillers > 1 ? "s" : ""}`);
    if (e.apologies) bits.push(`${e.apologies} apolog${e.apologies > 1 ? "ies" : "y"}`);
    li.append(el("span", "title-4", head), el("span", "caption-5 muted", bits.join(", ")));
    return li;
  }));
}

/* ---------- After the session, and the summary ---------- */
$("debrief-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  showError("e-post");
  if (!form.outcome) return showError("e-post", "Please answer whether the predicted outcome happened.");
  $("finish-btn").disabled = true;
  try {
    accept(await api("finish", {
      token,
      belief_after: Number($("f-after").value),
      post: { peak_distress: peakRated ? Number($("f-peak").value) : null, symptoms: [...form.symptoms], outcome: form.outcome, note: $("f-note").value.trim() },
    }));
    if (session.status === "complete") renderReport();
    else routeExit();
  } catch (err) {
    showError("e-post", err.message);
  } finally {
    $("finish-btn").disabled = false;
  }
});
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
function renderReport() {
  const r = session.report;
  $("debrief").hidden = true;
  $("report").hidden = false;
  $("r-date").textContent = new Date().toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" });
  $("r-source").textContent = `Readings: ${session.data_label.toLowerCase()}`;
  $("r-path").textContent = r.pathway.name;
  $("r-why").textContent = r.pathway.why;

  $("m-gad").textContent = `${r.gad.score}/21`;
  $("m-band").textContent = `${r.gad.band} range`;
  marker($("m-marker"), r.gad.score);
  $("m-verdict").textContent = r.verdict.peak === "unavailable" ? "No reading" : cap(r.verdict.peak);
  $("m-verdicts").replaceChildren(...r.verdicts.map((v) => {
    const i = el("i");
    i.dataset.v = v.label;
    return i;
  }));
  $("m-verdict-source").textContent = r.verdict.source === "Simulated" ? "Simulated, until our model is integrated" : "From our anxiety model";
  const peak = r.post.peak_distress;
  $("m-peak").textContent = peak === null || peak === undefined ? "Not rated" : `${peak}/10`;
  $("m-peak-bar").style.transform = `scaleX(${(peak || 0) / 10})`;

  $("r-pred").textContent = r.prediction;
  $("r-before").textContent = `${r.belief_before}%`;
  $("r-after").textContent = `${r.belief_after}%`;
  $("r-outcome").textContent = `${{ yes: "Yes", partly: "Partly", no: "No" }[r.post.outcome] || "Not answered"}, in the practice`;
  $("r-summary").textContent = r.summary;
  $("r-insight").textContent = r.insight;

  drawChart($("r-chart"), series, Math.max(series.length, 60));
  $("r-chart-note").textContent = `${session.data_label}. Heart rate change from your baseline, in beats per minute. It shows how the agent responds, and isn't your real data.`;
  $("r-gad").replaceChildren(...r.gad.items.map((item) => {
    const li = el("li");
    const pips = el("span", "gad-pips");
    pips.setAttribute("aria-hidden", "true");
    [1, 2, 3].forEach((n) => pips.append(el("i", n <= item.value ? "on" : "")));
    const body = el("div");
    body.append(el("span", "body-6", item.text), el("span", "caption-5 muted", item.answer));
    li.append(body, pips);
    return li;
  }));
  const symptoms = r.post.symptoms || [];
  $("r-symptoms").replaceChildren(...(symptoms.length ? symptoms.map((s) => el("span", "chip-static caption-4", s)) : [el("span", "body-6 muted", "Nothing selected")]));
  $("r-note-label").hidden = !r.post.note;
  $("r-note").hidden = !r.post.note;
  $("r-note").textContent = r.post.note || "";
  $("r-steps").replaceChildren(...[r.next_step, ...r.pathway.steps].filter((s, i, all) => all.indexOf(s) === i).map((s) => el("li", "", s)));
  $("r-mission").textContent = r.mission;
  $("r-practice").textContent = r.next_practice;
  saveToRecord(r);
  // The practice screen shows this as the week's real world step.
  store.setPrefs({ mission: { text: r.mission, next: r.next_practice, prediction: r.prediction, at: Date.now() } });
  const measured = r.evidence.filter((e) => e.source !== "sample" && e.pause_s !== null);
  const fillers = r.evidence.reduce((n, e) => n + (e.fillers || 0), 0);
  const apologies = r.evidence.reduce((n, e) => n + (e.apologies || 0), 0);
  $("r-measured").textContent = measured.length
    ? `On average you took ${(measured.reduce((n, e) => n + e.pause_s, 0) / measured.length).toFixed(1)} seconds before responding, across ${measured.length} response${measured.length > 1 ? "s" : ""}. Fillers: ${fillers}. Apologies: ${apologies}. Measured, not scored.`
    : "Nothing was measured in this session.";
  $("r-trail").replaceChildren(...[...session.trail].reverse().map(trailItem));
  $("r-note-demo").textContent = "Screening and practice support from a student research prototype, not a diagnosis or treatment. This session used demo readings, so it shows how the system runs, not that the method works.";
  scrollTo(0, 0);
}
function saveToRecord(r) {
  // The session is practice, so the real outcome stays open until the person checks it after the real event.
  const existing = fromPrediction && ledger.get(fromPrediction);
  const dueAt = existing?.due_at || Date.now() + (r.days_until || r.next_day) * 86400000;
  try {
    if (!savedToRecord) {
      savedToRecord = true;
      if (existing) ledger.update(existing.id, { practised: (existing.practised || 0) + 1 });
      else ledger.add({ text: r.prediction, event: r.event, belief_before: r.belief_before, due_at: dueAt, source: "session", sample: usedSample });
    }
    $("r-saved").textContent = `Saved to your track record. After the real event, check what really happened, by ${new Date(dueAt).toLocaleDateString(undefined, { day: "numeric", month: "short" })}.`;
    $("r-saved").hidden = false;
  } catch {
    $("r-saved").hidden = true;
  }
}
$("again-btn").addEventListener("click", () => location.assign("/practice"));
$("print-btn").addEventListener("click", () => print());
$("ics-btn").addEventListener("click", () => {
  // A real next action: the check-in lands in the person's own calendar. Nothing is stored by us.
  const r = session.report;
  const start = new Date(Date.now() + r.next_day * 86400000);
  start.setHours(18, 0, 0, 0);
  const stamp = (d) => d.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  const escape = (t) => t.replace(/[\\;,]/g, (c) => `\\${c}`).replace(/\n/g, " ");
  const ics = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//AnxiAway//Anxiety check-in//EN", "BEGIN:VEVENT",
    `UID:${Date.now()}@anxiaway`, `DTSTAMP:${stamp(new Date())}`, `DTSTART:${stamp(start)}`, `DTEND:${stamp(new Date(start.getTime() + 900000))}`,
    "SUMMARY:AnxiAway: your next practice step", `DESCRIPTION:${escape(`${r.next_practice} Rate your prediction again: ${r.prediction}`)}`,
    "END:VEVENT", "END:VCALENDAR"].join("\r\n");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([ics], { type: "text/calendar" }));
  link.download = "anxiaway-check-in.ics";
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
});

buildInject();
buildPost();
showStep("w-event", false);
if (check) $("check-basis").textContent = `The plan will use your check from ${store.agoText(check.at)}: ${check.score} of 21 on GAD-7 (${check.band.toLowerCase()} range)${check.body ? `, and a ${check.body.source === "Demo stream" ? "demo " : ""}body reading that was ${check.body.title.toLowerCase()}` : ""}.`;

/* Arriving from the track record to practise one prediction */
const practising = new URLSearchParams(location.search).get("pred");
const chosen = practising && ledger.get(practising);
if (chosen && !chosen.outcome) {
  fromPrediction = chosen.id;
  usedSample = !!chosen.sample;
  $("f-pred").value = chosen.text;
  $("f-belief").value = chosen.belief_before;
  $("o-belief").textContent = `${chosen.belief_before}%`;
  if (chosen.event) $("f-event").value = chosen.event;
  $("pred-banner").textContent = `Practising a prediction from your track record: "${chosen.text}"`;
  $("pred-banner").hidden = false;
}
