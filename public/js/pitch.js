import * as ledger from "/js/ledger.js";
import { OUTCOME, COPED, COMPARED, segGroup } from "/js/ledger.js";

const $ = (id) => document.getElementById(id);
const ACTIVE = "toward.pitch.active";
const STEPS = ["p-setup", "p-confirm", "p-locked", "p-verdict", "p-result"];
const draft = { belief: 70, theme: "other", sample: true };
const verdict = { outcome: null, compared: null, coped: null };
let timer = null;

const getActive = () => { try { return localStorage.getItem(ACTIVE); } catch { return null; } };
const setActive = (id) => { try { if (id) localStorage.setItem(ACTIVE, id); else localStorage.removeItem(ACTIVE); } catch { /* optional */ } };
function show(id) {
  STEPS.forEach((s) => { $(s).hidden = s !== id; });
  $("reset-btn").hidden = id === "p-setup";
  scrollTo(0, 0);
  const heading = $(id).querySelector("h1");
  if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); }
}
function showError(id, message) {
  $(id).textContent = message || "";
  $(id).hidden = !message;
}
function support(result) {
  $("support-title").textContent = result.status === "crisis" ? "You deserve support right now" : "Let's get you the right support";
  $("support-text").textContent = result.message;
  $("support-dlg").showModal();
}
$("support-close").addEventListener("click", () => $("support-dlg").close());

/* 1. Setup */
$("p-chips").querySelectorAll(".chip").forEach((chip) => chip.addEventListener("click", () => {
  $("p-worry").value = chip.textContent;
  $("p-chips").querySelectorAll(".chip").forEach((c) => c.classList.toggle("is-on", c === chip));
}));
$("p-belief").addEventListener("input", (e) => { draft.belief = Number(e.target.value); $("p-belief-out").textContent = `${e.target.value}%`; });
$("p-sample").addEventListener("click", () => {
  draft.sample = !draft.sample;
  $("p-sample").setAttribute("aria-checked", String(draft.sample));
});
$("p-setup").addEventListener("submit", async (e) => {
  e.preventDefault();
  showError("p-error");
  const worry = $("p-worry").value.trim();
  if (worry.length < 5) return showError("p-error", "Write the prediction, or pick one above.");
  $("p-sharpen").disabled = true;
  try {
    const result = await ledger.post("record/sharpen", { worry });
    if (result.status === "unclear") return showError("p-error", result.message);
    if (result.status !== "ok") return support(result);
    draft.theme = result.theme;
    $("p-pred").value = result.prediction;
    $("p-check").value = result.source === "Groq" ? result.how_to_check : "The judges decide what they saw.";
    $("p-confirm-source").textContent = result.source === "Groq" ? "The agent's checkable version. Change anything." : "Your words";
    show("p-confirm");
  } catch (err) {
    showError("p-error", err.message);
  } finally {
    $("p-sharpen").disabled = false;
  }
});
$("p-back").addEventListener("click", () => show("p-setup"));

/* 2. Lock */
$("p-confirm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("p-pred").value.trim();
  if (text.length < 5) return showError("p-confirm-error", "Write the prediction in a few words.");
  showError("p-confirm-error");
  try {
    const check = await ledger.post("record/check", { worry: text });
    if (check.status === "unclear") return showError("p-confirm-error", check.message);
    if (check.status !== "ok") return support(check);
  } catch (err) {
    return showError("p-confirm-error", err.message);
  }
  // No open sample entries here, so the agent's next step comes from the live result.
  if (draft.sample) ledger.loadSample(false);
  else ledger.clearSample();
  const item = ledger.add({ text, how_to_check: $("p-check").value.trim(), theme: draft.theme, belief_before: draft.belief, source: "pitch", due_at: Date.now() + 3600000 });
  setActive(item.id);
  showLocked(item);
});

/* 3. During the pitch */
function showLocked(item) {
  $("p-locked-text").textContent = item.text;
  $("p-locked-belief").textContent = `${item.belief_before}%`;
  $("p-locked-check").textContent = item.how_to_check || "The judges decide what they saw.";
  $("p-locked-at").textContent = new Date(item.created_at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  clearInterval(timer);
  const tick = () => {
    const s = Math.max(0, Math.floor((Date.now() - item.created_at) / 1000));
    $("p-timer").textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  };
  tick();
  timer = setInterval(tick, 1000);
  show("p-locked");
}
$("p-to-verdict").addEventListener("click", () => {
  const item = ledger.get(getActive());
  if (!item) return startOver();
  clearInterval(timer);
  $("p-verdict-q").textContent = `"${item.text}"`;
  $("p-verdict-check").textContent = item.how_to_check;
  $("v-belief").value = item.belief_before;
  $("v-belief-out").textContent = `${item.belief_before}%`;
  show("p-verdict");
});

/* 4. The judges decide */
segGroup($("v-outcome"), Object.entries({ yes: "Yes", partly: "Partly", no: "No" }), (v) => { verdict.outcome = v; });
segGroup($("v-compared"), [["worse", "Worse"], ["as_expected", "About the same"], ["better", "Better"]], (v) => { verdict.compared = v; });
segGroup($("v-coped"), [[0, COPED[0]], [1, COPED[1]], [2, COPED[2]]], (v) => { verdict.coped = v; });
$("v-belief").addEventListener("input", (e) => { $("v-belief-out").textContent = `${e.target.value}%`; });
$("p-verdict").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (verdict.outcome === null || verdict.compared === null || verdict.coped === null) return showError("v-error", "Please answer all three.");
  showError("v-error");
  const id = getActive();
  ledger.update(id, { ...verdict, belief_after: Number($("v-belief").value), checked_at: Date.now() });
  await showResult(ledger.get(id));
});

/* 5. The result */
const HEADLINE = {
  no: "Anxiety predicted it. It didn't happen.",
  partly: "Anxiety predicted it. Part of it happened.",
  yes: "It happened, and the record shows what came next.",
};
async function showResult(item) {
  $("r-headline").textContent = HEADLINE[item.outcome];
  $("r-text").textContent = item.text;
  $("r-before").textContent = `${item.belief_before}%`;
  $("r-after").textContent = `${item.belief_after}%`;
  $("r-detail").textContent = `${OUTCOME[item.outcome]} · ${COMPARED[item.compared]} · ${COPED[item.coped]}`;
  $("r-loading").hidden = false;
  $("r-agent").hidden = true;
  showError("r-error");
  show("p-result");
  try {
    const stats = await ledger.post("record/stats", { predictions: ledger.forApi() });
    ledger.renderBars($("r-bars"), stats);
    $("r-state").textContent = stats.state_text;
    $("r-sample").hidden = !stats.sample_count;
    $("r-sample").textContent = `${stats.sample_count} sample ${stats.sample_count === 1 ? "entry" : "entries"} shown. Only this prediction is real.`;
    const review = await ledger.post("record/review", { predictions: ledger.forApi(), just_checked: item.id });
    $("r-loading").hidden = true;
    if (review.status !== "ok") return support(review);
    ledger.applyFollowUps(review.follow_ups);
    ledger.saveReview(review);
    $("r-source").textContent = review.source === "Groq" ? "Written live by Groq" : "Scripted fallback";
    $("r-pattern").textContent = review.pattern;
    $("r-next").textContent = review.next_action;
    $("r-why").textContent = `Why: ${review.why}`;
    $("r-support").hidden = !review.support_note;
    $("r-support").textContent = review.support_note || "";
    $("r-agent").hidden = false;
    await ledger.renderTrace($("r-trace"), review.trace, true);
  } catch (err) {
    $("r-loading").hidden = true;
    showError("r-error", err.message);
  }
}

function startOver() {
  clearInterval(timer);
  // Start over is for rehearsals, so it leaves no trace in the record.
  const id = getActive();
  if (id && ledger.get(id)) ledger.remove(id);
  setActive(null);
  ["outcome", "compared", "coped"].forEach((k) => { verdict[k] = null; });
  document.querySelectorAll("#v-outcome button, #v-compared button, #v-coped button").forEach((b) => b.setAttribute("aria-checked", "false"));
  $("p-worry").value = "";
  $("p-chips").querySelectorAll(".chip").forEach((c) => c.classList.remove("is-on"));
  show("p-setup");
}
$("reset-btn").addEventListener("click", startOver);
$("r-again").addEventListener("click", () => { setActive(null); startOver(); });

/* A refresh mid pitch must not lose the locked prediction. */
const resumed = getActive() && ledger.get(getActive());
if (resumed && !resumed.outcome) showLocked(resumed);
else if (resumed) showResult(resumed);
else show("p-setup");
