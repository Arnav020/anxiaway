import { Panel } from "/js/panel.js";

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const setIcon = (el, name) => el.style.setProperty("--i", `url(/icons/${name}.svg)`);

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
const STATE_TEXT = {
  CALIBRATING: ["Baseline", "The first answer starts the trace."],
  LOW: ["Low", "Little change from baseline. The next question gets harder."],
  ENGAGED: ["Engaged", "Clear change from baseline. The level holds and the test continues."],
  HIGH: ["High", "The level holds. High arousal alone doesn't lower difficulty, and stopping stays one tap away."],
  LIMIT: ["Limit", "Safety limit reached. The Governor paused the room."],
  UNKNOWN: ["Unknown", "Movement or a poor signal, so body data isn't trusted or used."],
};
const NODE_NAMES = {
  governor: "Safety Governor", signal: "Signal Layer", policy: "Difficulty policy",
  director: "Director", scene: "Scene Agent", check: "Output check", planner: "Ladder Planner",
};
const INJECT_LABELS = { auto: "Scripted", calm: "Calm", rising: "Rising", high: "High", limit: "Safety limit", movement: "Movement", sensor_loss: "ECG lost" };
const SAMPLE_ANSWERS = [
  "It looks at how well students recover after a stressful exam, using sleep and mood data.",
  "I chose a smaller sample so I could follow each person for longer.",
  "The weakest part is the sample size. I would recruit from more than one college.",
  "The main assumption is that sleep drives mood. If that's wrong, the direction reverses.",
  "That's a fair challenge. Let me think. My data can't separate those two effects yet.",
  "The established approach uses surveys only. Mine adds daily measures, which is what is new.",
  "I don't know that detail right now, but here is how I would check it.",
  "I would defend it by showing the pilot results and being clear about their limits.",
];
const SAMPLE_PROFILE = { event: "Final year project viva", days: 6, pred: "If I pause to think, the panel will decide I don't know my subject.", belief: 80, gad: [1, 1, 1, 1, 0, 1, 1], harm: false, heart: false, crisis: false };

/* ---------- App state ---------- */
const form = { gad: Array(7).fill(null), self_harm: null, heart_condition: null, crisis: null, consent: false };
let session = null;
let token = null;
let room = null;
let series = [];
let trailShown = 0;
let injection = "auto";
let soundOn = true;
let inFlight = false;
let stopToken = 0;
let answerOpenAt = null;
let firstInputAt = null;
let useVoice = false;
let serverVoice = true;

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
function accept(view) {
  session = view;
  token = view.token;
}
function showError(id, message) {
  $(id).textContent = message || "";
  $(id).hidden = !message;
}

/* ---------- Wizard navigation, with real back buttons ---------- */
const STEPS = ["w-event", "w-fear", "w-gad", "w-safety", "w-consent", "w-plan"];
const BACK_LABEL = { "w-event": "Home", "w-fear": "Event", "w-gad": "Prediction", "w-safety": "Questionnaire", "w-consent": "Safety", "w-plan": "Edit answers" };
let current = "w-event";

function showStep(id, push = true) {
  [...STEPS, "w-loading"].forEach((s) => { $(s).hidden = s !== id; });
  current = id;
  const index = STEPS.indexOf(id);
  $("wiz-back-label").textContent = BACK_LABEL[id] || "Back";
  $("wiz-back").hidden = id === "w-loading";
  $("wiz-sample").hidden = index !== 0;
  const bar = $("wiz-bar");
  bar.hidden = index < 0 || index > 4;
  $("wizard").classList.toggle("wide", id === "w-plan");
  bar.setAttribute("aria-valuenow", index + 1);
  [...bar.children].forEach((pip, i) => pip.classList.toggle("on", i <= index));
  if (push) history.pushState({ step: id }, "");
  scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" });
  $(id).querySelector("h1")?.focus?.({ preventScroll: true });
}
function goBack() {
  const index = STEPS.indexOf(current);
  if (index <= 0) location.href = "/";
  else if (current === "w-plan") { setConsent(false); showStep("w-event"); }
  else showStep(STEPS[index - 1]);
}
$("wiz-back").addEventListener("click", goBack);
addEventListener("popstate", (e) => {
  if (!$("stage").hidden) {
    // The browser back button leaves the room only after confirming.
    history.pushState({ stage: true }, "");
    openLeave();
    return;
  }
  if (e.state?.step && !$("wizard").hidden && e.state.step !== "w-loading") showStep(e.state.step, false);
});
history.replaceState({ step: "w-event" }, "");

/* Step 1 and 2 */
const daysText = (d) => (Number(d) === 1 ? "Tomorrow" : `In ${d} days`);
$("f-days").addEventListener("input", (e) => { $("o-days").textContent = daysText(e.target.value); });
$("f-belief").addEventListener("input", (e) => { $("o-belief").textContent = `${e.target.value}%`; });
$("f-after").addEventListener("input", (e) => { $("o-after").textContent = `${e.target.value}%`; });
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
  if ($("f-event").value.trim().length < 2) return showError("e-event", "Name your event, or pick one above.");
  showError("e-event");
  showStep("w-fear");
});
$("w-fear").addEventListener("submit", (e) => {
  e.preventDefault();
  if ($("f-pred").value.trim().length < 5) return showError("e-fear", "Write what you fear will happen, or pick one above.");
  showError("e-fear");
  showStep("w-gad");
});

/* Step 3: GAD-7 */
GAD_ITEMS.forEach((item, i) => {
  const li = document.createElement("li");
  const label = document.createElement("span");
  label.className = "title-3";
  label.textContent = item;
  const seg = document.createElement("div");
  seg.className = "seg";
  seg.setAttribute("role", "radiogroup");
  seg.setAttribute("aria-label", item);
  GAD_OPTIONS.forEach((option, value) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = option;
    b.setAttribute("role", "radio");
    b.setAttribute("aria-checked", "false");
    b.addEventListener("click", () => {
      form.gad[i] = value;
      seg.querySelectorAll("button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
    });
    seg.append(b);
  });
  li.append(label, seg);
  $("gad").append(li);
});
$("w-gad").addEventListener("submit", (e) => {
  e.preventDefault();
  if (form.gad.includes(null)) return showError("e-gad", "Please answer all seven, so we can check this suits you today.");
  showError("e-gad");
  showStep("w-safety");
});

/* Step 4: safety questions need an explicit answer */
const yes = { "q-harm": "self_harm", "q-heart": "heart_condition", "q-crisis": "crisis" };
function setYn(id, value) {
  form[yes[id]] = value;
  $(id).querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.v === String(value))));
}
Object.keys(yes).forEach((id) => {
  $(id).querySelectorAll("button").forEach((b) => b.addEventListener("click", () => setYn(id, b.dataset.v === "true")));
});
$("w-safety").addEventListener("submit", (e) => {
  e.preventDefault();
  if (Object.values(yes).some((k) => form[k] === null)) return showError("e-safety", "Please answer all three questions.");
  showError("e-safety");
  showStep("w-consent");
});

/* Step 5: consent is its own moment, not one checkbox among many */
function setConsent(on) {
  form.consent = on;
  $("agree").setAttribute("aria-checked", String(on));
  $("build-btn").disabled = !on;
}
$("agree").addEventListener("click", () => setConsent(!form.consent));

$("wiz-sample").addEventListener("click", () => {
  const p = SAMPLE_PROFILE;
  $("f-event").value = p.event;
  $("f-days").value = p.days;
  $("o-days").textContent = daysText(p.days);
  $("f-pred").value = p.pred;
  $("f-belief").value = p.belief;
  $("o-belief").textContent = `${p.belief}%`;
  document.querySelectorAll("#gad .seg").forEach((seg, i) => seg.querySelectorAll("button")[p.gad[i]].click());
  setYn("q-harm", p.harm);
  setYn("q-heart", p.heart);
  setYn("q-crisis", p.crisis);
  // Consent is never skipped, even for the sample path.
  showStep("w-consent");
});

$("w-consent").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!form.consent) return;
  showError("e-consent");
  showStep("w-loading", false);
  const started = performance.now();
  try {
    const view = await api("session", {
      event: $("f-event").value.trim(),
      days_until: Number($("f-days").value),
      prediction: $("f-pred").value.trim(),
      belief_before: Number($("f-belief").value),
      gad7: form.gad.reduce((a, b) => a + b, 0),
      self_harm: form.self_harm,
      heart_condition: form.heart_condition,
      crisis: form.crisis,
      consent: true,
    });
    accept(view);
    if (view.status === "planned") renderPlan(performance.now() - started);
    else routeExit();
  } catch (err) {
    showStep("w-consent", false);
    showError("e-consent", err.message);
  }
});

function renderPlan(ms) {
  const plan = session.plan;
  const source = (session.trail.find((t) => t.who.startsWith("Ladder Planner")) || { who: "" }).who;
  $("plan-meta").textContent = source.includes("Groq") ? `· written live by Groq in ${(ms / 1000).toFixed(1)} s` : "· from the scripted planner";
  $("plan-title").textContent = `${plan.steps.length} steps toward your ${session.event.toLowerCase()}`;
  $("plan-said").textContent = session.prediction;
  $("plan-test").textContent = plan.testable_version;
  $("ladder").replaceChildren(...plan.steps.map((step, i) => {
    const li = document.createElement("li");
    li.className = `rung rung-${i + 1}`;
    li.style.animationDelay = `${i * 90}ms`;
    const day = document.createElement("span");
    day.className = "rung-day mono";
    day.textContent = `${i === 0 ? "Today · " : ""}Day ${step.day} · Level ${step.difficulty}`;
    const title = document.createElement("strong");
    title.className = "title-3";
    title.textContent = step.title;
    const test = document.createElement("span");
    test.className = "body-6 muted";
    test.textContent = step.test;
    li.append(day, title, test);
    return li;
  }));
  showStep("w-plan");
}

/* ---------- Entering the room ---------- */
let audioCtx = null;
let analyser = null;

function ensureAudio() {
  // Created on a click, so browsers allow sound.
  if (!audioCtx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (Ctx) {
      audioCtx = new Ctx();
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      analyser.connect(audioCtx.destination);
    }
  }
  audioCtx?.resume?.();
}

$("enter-btn").addEventListener("click", () => {
  ensureAudio();
  $("wizard").hidden = true;
  $("stage").hidden = false;
  history.pushState({ stage: true }, "");
  room = room || new Panel($("room-panel"));
  $("read-text").textContent = session.read_aloud;
  $("calibrate").hidden = false;
  $("cal-btn").hidden = false;
  $("cal-progress").hidden = true;
  buildInject();
  renderPips(session.level);
  fitRoom();
});

$("cal-btn").addEventListener("click", async () => {
  ensureAudio();
  $("cal-btn").hidden = true;
  $("cal-progress").hidden = false;
  requestAnimationFrame(() => { $("cal-fill").style.transform = "scaleX(1)"; });
  try {
    const [view] = await Promise.all([api("calibrate", { token }), sleep(reduced ? 200 : 4000)]);
    accept(view);
    series = [];
    trailShown = 0;
    $("trail").replaceChildren();
    $("ledger").replaceChildren();
    $("ev-pred").textContent = session.plan.testable_version;
    drawChart();
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

/* ---------- The panel speaks ---------- */
function renderPips(level) {
  $("pips").replaceChildren(...[1, 2, 3, 4, 5].map((n) => {
    const p = document.createElement("span");
    p.className = n <= level ? "pip on" : "pip";
    return p;
  }));
  $("pips").setAttribute("aria-label", `Panel difficulty ${level} of 5`);
}
function typewrite(el, text, durationMs, token_) {
  if (reduced) { el.textContent = text; return; }
  const words = text.split(" ");
  const each = Math.max(40, durationMs / words.length);
  el.textContent = "";
  words.forEach((w, i) => setTimeout(() => {
    if (token_ === speakToken) el.textContent = words.slice(0, i + 1).join(" ");
  }, i * each));
}

let speakToken = 0;
let ampTimer = null;
let currentAudio = null;
let currentUtterance = false;

function stopSpeaking() {
  speakToken += 1;
  clearInterval(ampTimer);
  if (currentAudio) { try { currentAudio.stop(); } catch { /* already stopped */ } currentAudio = null; }
  if (currentUtterance && "speechSynthesis" in window) speechSynthesis.cancel();
  currentUtterance = false;
  room?.setAmp(0);
}

function fakeAmp() {
  const start = performance.now();
  clearInterval(ampTimer);
  ampTimer = setInterval(() => {
    const t = performance.now() - start;
    room?.setAmp(Math.abs(Math.sin(t / 95)) * (0.45 + 0.4 * Math.abs(Math.sin(t / 310))));
  }, 40);
}

async function playServerVoice(line, member, mine) {
  if (!serverVoice || !audioCtx || !soundOn) return false;
  try {
    const res = await fetch("/api/speak", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: line, member }) });
    if (!res.ok) { serverVoice = false; return false; }
    const buffer = await audioCtx.decodeAudioData(await res.arrayBuffer());
    if (mine !== speakToken) return true;
    const src = audioCtx.createBufferSource();
    src.buffer = buffer;
    src.connect(analyser);
    currentAudio = src;
    const data = new Uint8Array(analyser.fftSize);
    clearInterval(ampTimer);
    ampTimer = setInterval(() => {
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (const v of data) sum += ((v - 128) / 128) ** 2;
      room?.setAmp(Math.min(1, Math.sqrt(sum / data.length) * 5));
    }, 30);
    typewrite($("line"), line, buffer.duration * 1000, mine);
    await new Promise((resolve) => { src.onended = resolve; src.start(); });
    return true;
  } catch {
    serverVoice = false;
    return false;
  }
}

function browserVoice(line, member, mine) {
  return new Promise((resolve) => {
    const est = line.split(" ").length * 340;
    fakeAmp();
    typewrite($("line"), line, est, mine);
    if (!soundOn || !("speechSynthesis" in window)) { setTimeout(resolve, est); return; }
    const u = new SpeechSynthesisUtterance(line);
    const voices = speechSynthesis.getVoices().filter((v) => v.lang.startsWith("en"));
    const i = ["Dr Rao", "Prof Ellis", "Ms Kim"].indexOf(member);
    if (voices.length) u.voice = voices[i % voices.length];
    u.rate = 1.02;
    u.pitch = [0.95, 1.05, 1.15][i] || 1;
    let done = false;
    const finish = () => { if (!done) { done = true; resolve(); } };
    u.onend = finish;
    u.onerror = finish;
    currentUtterance = true;
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
    setTimeout(finish, est + 5000);
  });
}

async function panelTurn() {
  const q = session.question;
  if (!q) return;
  const mine = ++speakToken;
  closeAnswerWindow();
  $("q-count").textContent = `Question ${session.turn + 1} of ${session.max_turns}`;
  renderPips(q.level);
  room?.setCalm(false);
  room?.setPressure(q.level);
  room?.setTactic(q.tactic);
  $("caption").classList.remove("waiting");

  if (q.tactic === "hold_silence") {
    // The panel deliberately says nothing, on the Director's choice.
    room?.setSpeaker(null);
    $("who").textContent = "The panel";
    $("line").textContent = "They look at you, and wait.";
    $("caption").classList.add("waiting");
    await sleep(reduced ? 300 : 3200);
    if (mine !== speakToken) return;
    $("caption").classList.remove("waiting");
  }
  $("who").textContent = q.member;
  $("line").textContent = "";
  room?.setSpeaker(q.member);
  const voiced = await playServerVoice(q.line, q.member, mine);
  if (mine !== speakToken) return;
  if (!voiced) await browserVoice(q.line, q.member, mine);
  if (mine !== speakToken) return;
  clearInterval(ampTimer);
  room?.setAmp(0);
  room?.setSpeaker(null);
  $("line").textContent = q.line;
  openAnswerWindow();
}

/* ---------- Answering: the pause is actually measured ---------- */
let ringTimer = null;
const target = () => session?.plan?.target_pause_s || 3;

function updateMeter() {
  const held = (firstInputAt ?? performance.now()) - answerOpenAt;
  const s = held / 1000;
  $("ring-text").textContent = s.toFixed(1);
  const pct = Math.min(1, s / target());
  $("ring-fg").style.strokeDashoffset = String(176 * (1 - pct));
  $("meter").classList.toggle("held", s >= target());
  $("meter-title").textContent = firstInputAt
    ? s >= target() ? `You held ${s.toFixed(1)} seconds` : `You started after ${s.toFixed(1)} seconds`
    : s >= target() ? "That's your pause. Answer when ready." : `Hold ${target()} seconds before you answer`;
}
function openAnswerWindow() {
  answerOpenAt = performance.now();
  firstInputAt = null;
  $("answer-box").classList.remove("locked");
  $("meter-sub").textContent = session.plan.testable_version;
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
    showError("e-answer", "Voice isn't available in this browser. Type your answer instead.");
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
    showError("e-answer", "Microphone access was blocked. Allow it in your browser, or type your answer.");
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
    g.strokeStyle = "#2F3E52";
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
    showError("e-answer", "We didn't catch any speech. Try again, or type your answer.");
    return;
  }
  closeAnswerWindow();
  $("meter-title").textContent = "Whisper is transcribing your answer";
  $("meter-sub").textContent = "";
  try {
    const res = await fetch("/api/transcribe", { method: "POST", headers: { "Content-Type": (blob.type || "audio/webm").split(";")[0] }, body: blob });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Speech recognition failed. Type your answer instead.");
    const heard = await res.json();
    if (!heard.text) throw new Error("We didn't catch that. Try again, or type your answer.");
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
  if (inFlight) return;
  submitAnswer(SAMPLE_ANSWERS[session.turn % SAMPLE_ANSWERS.length], "sample", null, null);
});
$("answer-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("answer").value.trim();
  if (!text) return showError("e-answer", "Add an answer first, or use your voice.");
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
    renderTrail();
    renderLedger();
    route();
  } catch (err) {
    openAnswerWindow();
    showError("e-answer", `${err.message} Your answer wasn't sent.`);
  } finally {
    inFlight = false;
  }
}

/* ---------- Routing by session status ---------- */
function leaveStage() {
  stopSpeaking();
  stopRecorder();
  clearInterval(ringTimer);
  micStream?.getTracks().forEach((t) => t.stop());
  micStream = null;
  useVoice = false;
  $("stage").hidden = true;
}
function routeExit() {
  leaveStage();
  $("wizard").hidden = true;
  const titles = { referred: "Let's get you the right support", crisis: "You deserve support right now", medical: "Please look after yourself first" };
  $("exit-title").textContent = titles[session.status];
  $("exit-text").textContent = session.exit_message;
  $("exit").hidden = false;
  scrollTo(0, 0);
}
function route() {
  const s = session.status;
  if (s === "referred" || s === "crisis" || s === "medical") return routeExit();
  if (s === "paused") {
    stopSpeaking();
    room?.setCalm(true);
    room?.setSpeaker(null);
    $("who").textContent = "The panel";
    $("line").textContent = "Everyone is waiting. Take the time you need.";
    $("caption").classList.add("waiting");
    $("pause-text").textContent = session.exit_message;
    if (!$("pause-dlg").open) $("pause-dlg").showModal();
    return;
  }
  if (s === "debrief") {
    leaveStage();
    $("d-pred").textContent = session.prediction;
    $("d-note").textContent = session.exit_message
      ? `${session.exit_message} You can still rate your prediction.`
      : "You've finished this session. Rate your prediction again, based on what really happened.";
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
async function resume(choice) {
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
$("stop-btn").addEventListener("click", stopSession);
function openLeave() { if (!$("leave-dlg").open) $("leave-dlg").showModal(); }
$("leave-btn").addEventListener("click", () => {
  // Before the session starts there's nothing to end.
  if (!$("calibrate").hidden) {
    leaveStage();
    $("wizard").hidden = false;
    showStep("w-plan");
    return;
  }
  openLeave();
});
$("leave-stay").addEventListener("click", () => $("leave-dlg").close());
$("leave-go").addEventListener("click", stopSession);

/* ---------- Sound ---------- */
$("sound-btn").addEventListener("click", () => {
  soundOn = !soundOn;
  $("sound-btn").setAttribute("aria-pressed", String(soundOn));
  setIcon($("sound-icon"), soundOn ? "speaker-high" : "speaker-slash");
  if (!soundOn && "speechSynthesis" in window) speechSynthesis.cancel();
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
  room?.setShift(open && innerWidth > 1180 ? 216 : 0);
  fitRoom();
}
$("agent-btn").addEventListener("click", () => setDrawer($("drawer").hidden));
$("drawer-close").addEventListener("click", () => setDrawer(false));
document.querySelectorAll(".tabs button").forEach((tab) => tab.addEventListener("click", () => {
  document.querySelectorAll(".tabs button").forEach((t) => t.setAttribute("aria-selected", String(t === tab)));
  ["live", "evidence", "log"].forEach((name) => { $(`tab-${name}`).hidden = name !== tab.dataset.tab; });
}));

function buildInject() {
  const names = ["auto", "calm", "rising", "high", "limit", "movement", "sensor_loss"];
  $("inject").replaceChildren(...names.map((name) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip";
    b.dataset.v = name;
    b.setAttribute("role", "radio");
    b.textContent = INJECT_LABELS[name];
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
    const li = document.createElement("li");
    li.className = "pipe";
    const dot = document.createElement("i");
    dot.className = `dot ${step.kind === "llm" ? "dot-llm" : "dot-code"}`;
    const name = document.createElement("span");
    name.className = "pipe-name";
    name.textContent = NODE_NAMES[step.node] + (step.source === "Scripted" ? " (scripted)" : "");
    const ms = document.createElement("span");
    ms.className = "pipe-ms";
    ms.textContent = step.ms < 1 ? "<1 ms" : `${Math.round(step.ms)} ms`;
    const sum = document.createElement("span");
    sum.className = "pipe-sum";
    sum.textContent = step.summary;
    li.append(dot, name, ms, sum);
    list.append(li);
    requestAnimationFrame(() => li.classList.add("lit"));
    if (!reduced && !document.hidden) await sleep(240);
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
const xOf = (i, n) => 4 + (i / Math.max(n - 1, 240)) * (CHART.w - 8);
const BANDS = [{ v: 5, label: "Engaged +5" }, { v: 20, label: "High +20" }, { v: 35, label: "Limit +35" }];
function drawChart() {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${CHART.w} ${CHART.h}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Line chart of heart rate change from baseline. Each answer's state is listed in the reasoning tab.");
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
  series.forEach((p, i) => {
    if (p.delta === null) { open = false; return; }
    d += `${open ? "L" : "M"}${xOf(i, series.length).toFixed(1)} ${yOf(Math.max(CHART.min, Math.min(CHART.max, p.delta))).toFixed(1)}`;
    open = true;
  });
  const path = document.createElementNS(NS, "path");
  path.setAttribute("d", d);
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "#2F3E52");
  path.setAttribute("stroke-width", 2);
  path.setAttribute("stroke-linejoin", "round");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("vector-effect", "non-scaling-stroke");
  svg.append(path);
  const root = $("chart");
  root.replaceChildren(svg);
  BANDS.forEach((b) => {
    const tag = document.createElement("span");
    tag.className = "chart-tag";
    tag.textContent = b.label;
    tag.style.top = `${(yOf(b.v) / CHART.h) * 100}%`;
    root.append(tag);
  });
}
$("chart").addEventListener("pointermove", (ev) => {
  if (!series.length) return;
  const box = $("chart").getBoundingClientRect();
  const frac = (ev.clientX - box.left) / box.width;
  const i = Math.max(0, Math.min(series.length - 1, Math.round(frac * Math.max(series.length - 1, 240))));
  const p = series[i];
  let tip = $("chart").querySelector(".tip");
  if (!tip) { tip = document.createElement("span"); tip.className = "tip"; $("chart").append(tip); }
  const value = p.delta === null ? "not trusted" : `${p.delta > 0 ? "+" : ""}${p.delta.toFixed(1)} bpm`;
  tip.textContent = `${p.t} s, ${value}, ${STATE_TEXT[p.state][0]}`;
  tip.style.left = `${Math.min(frac * box.width, box.width - 170)}px`;
});
$("chart").addEventListener("pointerleave", () => $("chart").querySelector(".tip")?.remove());

function animateReadings(readings) {
  return new Promise((resolve) => {
    if (reduced || !readings?.length || document.hidden || $("drawer").hidden) {
      series.push(...(readings || []));
      drawChart();
      return resolve();
    }
    const duration = 1500;
    const start = performance.now();
    let added = 0;
    const timer = setInterval(() => {
      const goal = document.hidden ? readings.length : Math.min(readings.length, Math.floor(((performance.now() - start) / duration) * readings.length));
      if (goal > added) {
        series.push(...readings.slice(added, goal));
        added = goal;
        drawChart();
      }
      if (added >= readings.length) { clearInterval(timer); resolve(); }
    }, 40);
  });
}

function trailItem(entry) {
  const li = document.createElement("li");
  li.dataset.kind = entry.who.startsWith("Safety") ? "governor" : entry.who.startsWith("Difficulty") ? "policy" : entry.who.includes("(") ? "llm" : "other";
  const who = document.createElement("span");
  who.className = "t-who";
  who.textContent = entry.who;
  const ms = document.createElement("span");
  ms.className = "t-ms";
  ms.textContent = entry.ms ? `${entry.ms} ms` : "";
  const what = document.createElement("span");
  what.className = "t-what";
  what.textContent = entry.text;
  li.append(who, ms, what);
  return li;
}
function renderTrail() {
  session.trail.slice(trailShown).forEach((e) => $("trail").prepend(trailItem(e)));
  trailShown = session.trail.length;
}
function renderLedger() {
  const list = $("ledger");
  if (!session.evidence.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "Answer a question. What you actually did appears here, measured rather than scored.";
    list.replaceChildren(li);
    return;
  }
  list.replaceChildren(...[...session.evidence].reverse().map((e) => {
    const li = document.createElement("li");
    if (e.held) li.className = "held";
    const head = document.createElement("span");
    head.className = "title-4";
    head.textContent = e.source === "sample"
      ? `Question ${e.turn}: sample answer, nothing measured`
      : e.pause_s === null ? `Question ${e.turn}: pause not measured`
        : `Question ${e.turn}: paused ${e.pause_s} s${e.held ? `, target of ${e.target_s} s held` : `, target ${e.target_s} s`}`;
    const detail = document.createElement("span");
    detail.className = "caption-5 muted";
    const bits = [];
    if (e.words) bits.push(`${e.words} words`);
    if (e.wpm) bits.push(`${e.wpm} words per minute`);
    if (e.longest_gap_s) bits.push(`longest gap ${e.longest_gap_s} s`);
    if (e.fillers) bits.push(`${e.fillers} filler${e.fillers > 1 ? "s" : ""}`);
    if (e.apologies) bits.push(`${e.apologies} apolog${e.apologies > 1 ? "ies" : "y"}`);
    detail.textContent = bits.join(", ");
    li.append(head, detail);
    return li;
  }));
}

/* ---------- Debrief and report ---------- */
$("debrief-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("finish-btn").disabled = true;
  try {
    accept(await api("finish", { token, belief_after: Number($("f-after").value) }));
    renderReport();
  } catch (err) {
    $("d-note").textContent = err.message;
  } finally {
    $("finish-btn").disabled = false;
  }
});
function renderReport() {
  const r = session.report;
  $("debrief").hidden = true;
  $("report").hidden = false;
  $("r-pred").textContent = r.prediction;
  $("r-before").textContent = `${r.belief_before}%`;
  $("r-after").textContent = `${r.belief_after}%`;
  $("r-summary").textContent = r.summary;
  $("r-mission").textContent = r.mission;
  $("r-next").textContent = r.next_step;
  const facts = [["Questions", r.questions_answered], ["Measured pauses held", r.measured_tests], ["Top difficulty", `${r.highest_level}/5`], ["Safety pauses", r.limit_events]];
  $("r-facts").replaceChildren(...facts.map(([k, v]) => {
    const wrap = document.createElement("div");
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = v;
    wrap.append(dt, dd);
    return wrap;
  }));
  $("r-note").textContent = `One demo session on ${session.data_label.toLowerCase()} data. It shows how the system runs, not that the method works.`;
  $("r-trail").replaceChildren(...[...session.trail].reverse().map(trailItem));
  scrollTo(0, 0);
}
$("again-btn").addEventListener("click", () => location.assign("/room"));

/* ---------- Start ---------- */
buildInject();
showStep("w-event", false);
