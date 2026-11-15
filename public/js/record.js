import * as ledger from "/js/ledger.js";
import { el, OUTCOME, COMPARED, COPED, segGroup } from "/js/ledger.js";
import * as store from "/js/store.js";
import { mountShell } from "/js/shell.js";

mountShell("record");
const practiseHref = (id) => (store.sessionCheck() ? `/session?pred=${encodeURIComponent(id)}` : `/checkin?next=session&pred=${encodeURIComponent(id)}`);

const $ = (id) => document.getElementById(id);
const DAY = 86400000;
const when = (ms) => new Date(ms).toLocaleDateString(undefined, { day: "numeric", month: "short" });
const addState = { due: null, belief: 70, worry: "", theme: "other", prediction: "", check: "" };
let lastReview = null;
let lastChecksKey = sessionStorage.getItem("toward.rec.checks") || "";
let reviewRun = 0;

function showError(id, message) {
  $(id).textContent = message || "";
  $(id).hidden = !message;
}
function showSupport(result) {
  $("support-title").textContent = result.status === "crisis" ? "You deserve support right now" : "Let's get you the right support";
  $("support-text").textContent = result.message;
  $("support-origin").textContent = "This response comes from fixed safety wording, not from a language model.";
  $("support-dlg").showModal();
}
$("support-close").addEventListener("click", () => $("support-dlg").close());
let confirmAction = null;
function askConfirm(title, text, action) {
  $("confirm-title").textContent = title;
  $("confirm-text").textContent = text;
  confirmAction = action;
  $("confirm-dlg").showModal();
}
$("confirm-no").addEventListener("click", () => $("confirm-dlg").close());
$("confirm-yes").addEventListener("click", () => {
  $("confirm-dlg").close();
  confirmAction?.();
  render();
});

/* ---------- The score ---------- */
function renderHero(stats) {
  const empty = stats.total === 0;
  $("empty-actions").hidden = !empty;
  $("fbars").hidden = stats.checked === 0;
  $("rec-stats").hidden = stats.checked === 0;
  const tag = $("sample-tag");
  tag.hidden = !stats.sample_count;
  tag.textContent = stats.sample_count === stats.total ? "Sample data, not a real person" : `Includes ${stats.sample_count} sample ${stats.sample_count === 1 ? "entry" : "entries"}`;
  if (empty) {
    $("rec-headline").textContent = "Anxiety makes predictions. Check them against what happened.";
    $("rec-state").textContent = "Log what your anxiety says will happen, then come back and record what actually did. It's kept only in this browser, with no account.";
    return;
  }
  if (stats.checked === 0) {
    $("rec-headline").textContent = `${stats.open} prediction${stats.open === 1 ? "" : "s"} waiting to be checked.`;
    $("rec-state").textContent = "Nothing has been checked yet, so there's no score. The record starts when you go back and look.";
    return;
  }
  $("rec-headline").textContent = stats.state === "too_few"
    ? `${stats.checked} checked so far. Too few to call a pattern.`
    : `It felt ${stats.forecast_pct}% likely. It happened ${stats.actual_pct}% of the time.`;
  $("rec-state").textContent = stats.state_text;
  ledger.renderBars($("fbars"), stats);
  const item = (value, label) => {
    const box = el("div");
    box.append(el("dd", "heading-5 mono", value), el("dt", "caption-4", label));
    return box;
  };
  const items = [
    item(`${stats.did_not} of ${stats.checked}`, "Didn't happen"),
    item(`${stats.came_true}`, "Came true"),
    item(`${stats.partly}`, "Came true in part"),
  ];
  if (stats.came_about) items.push(item(`${stats.got_through} of ${stats.came_about}`, "When it did happen, you got through it"));
  if (stats.belief_change !== null) items.push(item(`${stats.belief_change > 0 ? "+" : ""}${stats.belief_change}`, "Average change in how likely it feels, in points"));
  $("rec-stats").replaceChildren(...items);
}

/* ---------- Checking what happened ---------- */
function checkForm(p) {
  const form = el("form", "check-form stack");
  form.noValidate = true;
  const answer = { outcome: null, compared: null, coped: null };
  form.dataset.id = p.id;
  form.append(el("p", "title-3", p.text));
  const meta = el("p", "caption-5 muted", `Felt ${p.belief_before}% likely.${p.how_to_check ? ` You'd know by: ${p.how_to_check}` : ""}${p.sample ? " Sample entry." : ""}`);
  form.append(meta);
  const q = (label, options, key) => {
    const wrap = el("div", "stack-sm");
    const seg = el("div");
    seg.setAttribute("aria-label", label);
    segGroup(seg, options, (v) => { answer[key] = v; });
    wrap.append(el("span", "title-4", label), seg);
    return wrap;
  };
  form.append(
    q("Did it happen?", Object.entries(OUTCOME), "outcome"),
    q("Compared with what you expected, it was", [["worse", "Worse"], ["as_expected", "About the same"], ["better", "Better"]], "compared"),
    q("How did you cope?", [[0, COPED[0]], [1, COPED[1]], [2, COPED[2]]], "coped"),
  );
  const dial = el("div", "dial-inline");
  const label = el("span", "title-4", "How likely does this feel now? ");
  const out = el("output", "mono", `${p.belief_before}%`);
  label.append(out);
  const range = el("input");
  range.type = "range"; range.min = 0; range.max = 100; range.value = p.belief_before;
  range.setAttribute("aria-label", "How likely it feels now, in percent");
  range.addEventListener("input", () => { out.textContent = `${range.value}%`; });
  dial.append(label, range);
  const err = el("p", "error");
  err.hidden = true;
  err.setAttribute("role", "alert");
  const row = el("div", "row-gap");
  const save = el("button", "btn btn-cta", "Record what happened");
  save.type = "submit";
  const cancel = el("button", "btn btn-plain", "Not yet");
  cancel.type = "button";
  cancel.addEventListener("click", () => { checkingId = null; render(); });
  row.append(save, cancel);
  form.append(dial, err, row);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (answer.outcome === null || answer.compared === null || answer.coped === null) {
      err.textContent = "Please answer all three, so your record is accurate.";
      err.hidden = false;
      return;
    }
    ledger.update(p.id, { ...answer, belief_after: Number(range.value), checked_at: Date.now() });
    checkingId = null;
    render();
    scrollTo({ top: 0, behavior: "smooth" });
  });
  return form;
}
let checkingId = null;

function renderLists() {
  const now = Date.now();
  const open = ledger.openOnes();
  const due = open.filter((p) => (p.due_at && p.due_at <= now) || p.id === checkingId);
  const waiting = open.filter((p) => !due.includes(p));
  $("due-card").hidden = !due.length;
  $("due-list").replaceChildren(...due.map(checkForm));
  $("open-card").hidden = !waiting.length;
  $("open-list").replaceChildren(...waiting.map((p) => {
    const li = el("li", "open-item");
    const text = el("div", "stack-xs");
    text.append(el("span", "title-4", p.text), el("span", "caption-5 muted", `Felt ${p.belief_before}% likely · Check by ${p.due_at ? when(p.due_at) : "any time"}${p.practised ? ` · Practised ${p.practised} time${p.practised > 1 ? "s" : ""}` : ""}${p.sample ? " · Sample" : ""}`));
    const row = el("div", "row-gap");
    const check = el("button", "btn btn-white btn-sm", "Check now");
    check.type = "button";
    check.addEventListener("click", () => { checkingId = p.id; render(); $("due-card").scrollIntoView({ behavior: "smooth", block: "start" }); });
    const practise = el("a", "btn btn-plain btn-sm", "Practise this");
    practise.href = practiseHref(p.id);
    row.append(check, practise);
    li.append(text, row);
    return li;
  }));
  const checked = ledger.all().filter((p) => p.outcome).sort((a, b) => (b.checked_at || b.created_at) - (a.checked_at || a.created_at));
  $("ledger-card").hidden = !checked.length;
  $("ledger-rows").replaceChildren(...checked.map((p) => {
    const li = el("li", "ledger-row");
    li.dataset.outcome = p.outcome;
    const body = el("div", "stack-xs");
    const coped = p.coped === null || p.coped === undefined ? "" : ` · ${COPED[p.coped]}`;
    const compared = p.outcome !== "no" && p.compared ? ` · ${COMPARED[p.compared]}` : "";
    body.append(el("span", "title-4", p.text), el("span", "caption-5 muted", `${p.theme}${compared}${coped}${p.sample ? " · Sample" : ""}`));
    const belief = el("span", "ledger-belief mono caption-3", p.belief_after === null ? `${p.belief_before}%` : `${p.belief_before}% to ${p.belief_after}%`);
    const chip = el("span", "ledger-out caption-3", OUTCOME[p.outcome]);
    const del = el("button", "ledger-del no-print");
    del.type = "button";
    del.setAttribute("aria-label", `Delete: ${p.text}`);
    del.append(Object.assign(el("i", "icon"), { style: "--i:url(/icons/x.svg)" }));
    del.addEventListener("click", () => askConfirm("Delete this prediction?", p.text, () => ledger.remove(p.id)));
    li.append(body, belief, chip, del);
    return li;
  }));
  const all = ledger.all();
  const checks = store.allChecks();
  $("clear-sample").hidden = !all.some((p) => p.sample) && !checks.some((c) => c.sample);
  $("clear-all").hidden = !all.length && !checks.length;
}

/* ---------- The agent ---------- */
function targetOf(review) {
  return review.prediction_id ? ledger.get(review.prediction_id) : null;
}
function paintReview(review, animate) {
  lastReview = review;
  $("agent-loading").hidden = true;
  $("agent-empty").hidden = true;
  $("agent-body").hidden = false;
  $("agent-source").textContent = review.source === "Groq" ? "Written live by Groq" : "Scripted fallback";
  $("agent-pattern").textContent = review.pattern;
  $("agent-next").textContent = review.next_action;
  const target = targetOf(review);
  $("agent-target").textContent = target ? target.text : "";
  $("agent-target").hidden = !target;
  $("agent-why").textContent = `Why: ${review.why}`;
  const go = $("agent-go");
  const label = { check: "Record what happened", test: "Practise this situation", add: "Log a prediction", screen: "Do a check", person: "Save my record to share" }[review.kind];
  go.textContent = label;
  go.href = review.kind === "test" && target ? practiseHref(target.id) : review.kind === "add" ? "#add" : review.kind === "screen" ? "/checkin" : review.kind === "person" ? "#checks" : "#due-card";
  $("agent-ics").hidden = !target;
  $("agent-support").hidden = !review.support_note;
  $("agent-support").textContent = review.support_note || "";
  ledger.renderTrace($("agent-trace"), review.trace, animate);
}
async function runReview(force = false) {
  const list = ledger.all();
  const checks = store.checksForApi();
  const checksKey = JSON.stringify(checks);
  showError("agent-error");
  if (!list.length && !checks.length) {
    lastReview = null;
    $("agent-empty").hidden = false;
    $("agent-body").hidden = true;
    $("agent-loading").hidden = true;
    $("agent-source").textContent = "";
    return;
  }
  const cached = force || checksKey !== lastChecksKey ? null : ledger.cachedReview();
  lastChecksKey = checksKey;
  if (cached) return paintReview(cached, false);
  const mine = ++reviewRun;
  $("agent-empty").hidden = true;
  $("agent-body").hidden = true;
  $("agent-loading").hidden = false;
  try {
    const review = await ledger.post("record/review", { predictions: ledger.forApi(), checks });
    if (mine !== reviewRun) return;
    $("agent-loading").hidden = true;
    if (review.status !== "ok") return showSupport(review);
    ledger.applyFollowUps(review.follow_ups);
    ledger.saveReview(review);
    try { sessionStorage.setItem("toward.rec.checks", checksKey); } catch { /* optional */ }
    renderLists();
    paintReview(review, true);
  } catch (err) {
    if (mine !== reviewRun) return;
    $("agent-loading").hidden = true;
    $("agent-body").hidden = true;
    showError("agent-error", err.message);
  }
}
$("agent-again").addEventListener("click", () => runReview(true));
$("agent-go").addEventListener("click", (e) => {
  if (lastReview?.kind === "person") { e.preventDefault(); print(); return; }
  if (lastReview?.kind === "check" && lastReview.prediction_id) {
    e.preventDefault();
    checkingId = lastReview.prediction_id;
    renderLists();
    $("due-card").scrollIntoView({ behavior: "smooth", block: "start" });
  }
});
$("agent-ics").addEventListener("click", () => {
  const target = targetOf(lastReview);
  if (!target) return;
  const at = new Date(target.due_at && target.due_at > Date.now() ? target.due_at : Date.now() + DAY);
  at.setHours(18, 0, 0, 0);
  ledger.downloadIcs("Toward: check your prediction", `Your anxiety predicted: ${target.text} What actually happened? Open your track record to log it.`, at);
});

/* ---------- Adding ---------- */
segGroup($("f-due"), [[0.2, "Today"], [1, "Tomorrow"], [3, "In 3 days"], [7, "In a week"]], (v) => { addState.due = v; });
$("f-due").querySelectorAll("button")[2].click();
$("f-belief").addEventListener("input", (e) => { addState.belief = Number(e.target.value); $("o-belief").textContent = `${e.target.value}%`; });
$("add-chips").querySelectorAll(".chip").forEach((chip) => chip.addEventListener("click", () => {
  $("f-worry").value = chip.textContent;
  $("add-chips").querySelectorAll(".chip").forEach((c) => c.classList.toggle("is-on", c === chip));
}));
$("add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  showError("add-error");
  const worry = $("f-worry").value.trim();
  if (worry.length < 5) return showError("add-error", "Write what your anxiety says will happen, or pick one above.");
  const limit = 5;
  if (ledger.openOnes().filter((p) => !p.sample).length >= limit) {
    return showError("add-error", `You have ${limit} open already. Check one first, so tracking doesn't turn into another way of worrying.`);
  }
  $("sharpen-btn").disabled = true;
  try {
    const result = await ledger.post("record/sharpen", { worry });
    if (result.status === "unclear") return showError("add-error", result.message);
    if (result.status !== "ok") return showSupport(result);
    addState.worry = worry;
    addState.theme = result.theme;
    $("f-pred").value = result.prediction;
    $("f-check").value = result.how_to_check;
    $("confirm-source").textContent = result.source === "Groq" ? "The agent's checkable version. Change anything." : "Your words. Add how you'll know.";
    $("keep-words").hidden = result.prediction === worry;
    $("add-form").hidden = true;
    $("confirm-form").hidden = false;
    $("f-pred").focus();
  } catch (err) {
    showError("add-error", err.message);
  } finally {
    $("sharpen-btn").disabled = false;
  }
});
$("keep-words").addEventListener("click", () => { $("f-pred").value = addState.worry; });
$("confirm-back").addEventListener("click", () => { $("confirm-form").hidden = true; $("add-form").hidden = false; });
$("confirm-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("f-pred").value.trim();
  if (text.length < 5) return showError("confirm-error", "Write the prediction in a few words.");
  showError("confirm-error");
  // The edited wording gets the same fixed safety check before it's saved.
  try {
    const check = await ledger.post("record/check", { worry: text });
    if (check.status === "unclear") return showError("confirm-error", check.message);
    if (check.status !== "ok") return showSupport(check);
  } catch (err) {
    return showError("confirm-error", err.message);
  }
  ledger.add({ text, how_to_check: $("f-check").value.trim(), theme: addState.theme, belief_before: addState.belief, due_at: Date.now() + (addState.due ?? 3) * DAY });
  $("f-worry").value = "";
  $("add-chips").querySelectorAll(".chip").forEach((c) => c.classList.remove("is-on"));
  $("confirm-form").hidden = true;
  $("add-form").hidden = false;
  render();
});

/* ---------- Page ---------- */
$("sample-btn").addEventListener("click", () => { ledger.loadSample(); store.loadSampleChecks(); render(); });
$("clear-sample").addEventListener("click", () => askConfirm("Remove the sample entries?", "Your own checks and predictions stay.", () => { ledger.clearSample(); store.clearSampleChecks(); }));
$("clear-all").addEventListener("click", () => askConfirm("Delete your whole record?", "This removes every check and prediction from this browser, and it can't be undone. We don't hold a copy.", () => { ledger.clearAll(); store.clearChecks(); }));

/* ---------- Checks over time ---------- */
function renderChecks() {
  const checks = store.allChecks();
  $("checks-empty").hidden = checks.length > 0;
  $("checks-body").hidden = !checks.length;
  $("checks-tag").hidden = !checks.some((c) => c.sample);
  if (!checks.length) return;
  const shown = checks.slice(-12);
  const chart = $("checks-chart");
  store.lineChart(chart, shown.map((c) => c.score), { min: 0, max: 21, color: "#2F3E52", fill: true, dots: true, label: `GAD-7 scores across your last ${shown.length} checks` });
  const lines = el("div", "band-lines");
  [[5, "Mild"], [10, "Moderate"], [15, "Severe"]].forEach(([value, name]) => {
    const top = `${8 + (1 - value / 21) * 84}%`;
    const rule = el("i");
    rule.style.top = top;
    const tag = el("span", "", `${name} ${value}+`);
    tag.style.top = top;
    lines.append(rule, tag);
  });
  chart.append(lines);
  const first = shown[0], last = shown.at(-1);
  $("checks-note").textContent = shown.length > 1
    ? `GAD-7 score, from ${store.dateText(first.at)} to ${store.dateText(last.at)}. A change of a few points between checks is normal.`
    : "GAD-7 score. Check again in two weeks to see a trend.";
  $("check-rows").replaceChildren(...[...checks].reverse().slice(0, 8).map((c) => {
    const li = el("li", "check-row");
    const body = el("div", "stack-xs");
    body.append(el("span", "title-4", `${c.score} of 21`), el("span", "caption-5 muted", `${c.body ? `${c.body.title} · ${c.body.source}` : "No body reading"}${c.sample ? " · Sample" : ""}`));
    const chip = el("span", "band-chip caption-3", c.band);
    chip.dataset.band = c.band;
    li.append(el("span", "caption-4 muted mono", store.dateText(c.at)), body, chip);
    return li;
  }));
}
$("print-btn").addEventListener("click", () => print());

async function render() {
  showError("rec-error");
  $("storage-warn").hidden = ledger.isPersistent();
  renderLists();
  renderChecks();
  try {
    renderHero(await ledger.post("record/stats", { predictions: ledger.forApi() }));
  } catch (err) {
    showError("rec-error", err.message);
  }
  runReview();
}
if (new URLSearchParams(location.search).get("sample") === "1" && !ledger.all().length) { ledger.loadSample(); if (!store.allChecks().length) store.loadSampleChecks(); }
render();
