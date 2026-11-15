import { mountShell, deviceStatus } from "/js/shell.js";
import * as ledger from "/js/ledger.js";
import * as store from "/js/store.js";
import { gauge, maskChips, runAgent } from "/js/ui.js";

const $ = (id) => document.getElementById(id);
mountShell("home");

function renderToday(checks) {
  const latest = checks.at(-1);
  $("today-date").textContent = `Today · ${store.dateText(Date.now(), { weekday: "long", day: "numeric", month: "long" })}`;
  const cta = $("today-cta").firstElementChild;
  const more = $("today-more");
  if (!latest) {
    $("today-title").textContent = "Start with a two-minute check.";
    $("today-sub").textContent = "Seven screening questions, an optional body reading, and one clear result. Then the agent plans what comes next.";
    cta.textContent = "Start a check";
    more.textContent = "See it with sample data";
    more.onclick = loadSample;
    gauge($("today-gauge"), null);
    return;
  }
  const days = store.daysSince(latest.at);
  const due = days >= store.RECHECK_DAYS;
  const previous = checks.at(-2);
  const change = previous ? latest.score - previous.score : null;
  const moved = change === null ? "That was your first check." : change >= 3 ? "That's higher than the check before." : change <= -3 ? "That's lower than the check before." : "That's about the same as the check before.";
  $("today-title").textContent = `Your last check was ${store.agoText(latest.at)}, in the ${latest.band.toLowerCase()} range.`;
  $("today-sub").textContent = due ? `${moved} It's been two weeks or more, so it's time to check again.` : `${moved} Screening looks back over two weeks, so the next one is due in ${store.RECHECK_DAYS - days} day${store.RECHECK_DAYS - days === 1 ? "" : "s"}.`;
  cta.textContent = due ? "Check again" : "New check";
  more.textContent = "View my record";
  more.onclick = () => location.assign("/record");
  gauge($("today-gauge"), latest.score);
}

function renderTiles(checks, status) {
  const latest = checks.at(-1);
  $("t-score").textContent = latest ? `${latest.score}/21` : "–";
  $("t-score-text").textContent = latest ? `${latest.band} range on GAD-7, ${store.agoText(latest.at)}` : "No check yet. GAD-7 takes about a minute.";
  const scores = checks.slice(-8).map((c) => c.score);
  $("t-spark").hidden = scores.length < 2;
  if (scores.length >= 2) store.lineChart($("t-spark"), scores, { min: 0, max: 21, color: "#2F3E52", fill: true, dots: true, label: `GAD-7 scores over your last ${scores.length} checks` });

  const withBody = [...checks].reverse().find((c) => c.body);
  $("t-body").textContent = withBody ? withBody.body.title : status.configured ? "No reading yet" : "Wearable: integrating soon";
  $("t-body-text").textContent = withBody
    ? `${withBody.body.source}, ${store.agoText(withBody.at)}${withBody.body.hr_delta === null || withBody.body.hr_delta === undefined ? "" : `, heart rate ${withBody.body.hr_delta > 0 ? "+" : ""}${withBody.body.hr_delta} bpm from baseline`}`
    : "Our 4 sensor prototype reads heart, skin and movement. Until it's linked, a labelled demo reading shows how it works.";
  maskChips($("t-mask"), status.sensors.map((s) => ({ ...s, present: withBody ? (withBody.body.present || []).includes(s.sensor) : s.present })));
}

async function renderPredictions() {
  const list = ledger.all();
  if (!list.length) {
    $("t-pred").textContent = "Nothing logged yet";
    $("t-pred-text").textContent = "Write down what your anxiety says will happen, then come back and record what did.";
    $("t-bars").hidden = true;
    return;
  }
  try {
    const stats = await store.api("record/stats", { predictions: ledger.forApi() });
    $("t-pred").textContent = stats.overdue ? `${stats.overdue} due to check` : stats.open ? `${stats.open} waiting` : `${stats.checked} checked`;
    $("t-pred-text").textContent = stats.checked ? stats.state_text : "Nothing checked yet, so there's no score. The record starts when you go back and look.";
    $("t-bars").hidden = !stats.checked;
    if (stats.checked) {
      $("t-forecast").textContent = `${stats.forecast_pct}%`;
      $("t-actual").textContent = `${stats.actual_pct}%`;
      requestAnimationFrame(() => requestAnimationFrame(() => {
        $("t-forecast-bar").style.transform = `scaleX(${stats.forecast_pct / 100})`;
        $("t-actual-bar").style.transform = `scaleX(${stats.actual_pct / 100})`;
      }));
    }
  } catch (err) {
    $("t-pred").textContent = "Couldn't load";
    $("t-pred-text").textContent = err.message;
  }
}

function loadSample() {
  store.loadSampleChecks();
  ledger.loadSample();
  render();
}
$("sample-on").addEventListener("click", loadSample);
$("sample-off").addEventListener("click", () => {
  store.clearSampleChecks();
  ledger.clearSample();
  render();
});

async function render() {
  const checks = store.allChecks();
  const predictions = ledger.all();
  const hasSample = checks.some((c) => c.sample) || predictions.some((p) => p.sample);
  $("sample-tag").hidden = !hasSample;
  $("sample-on").hidden = hasSample;
  $("sample-off").hidden = !hasSample;
  const fresh = checks.length === 0 && predictions.length === 0;
  $("start").hidden = !fresh;
  // With nothing logged there's nothing for the agent to read, and the button above already starts a check.
  $("agent").hidden = fresh;
  renderToday(checks);
  let status = { configured: false, sensors: ["ECG", "PPG", "EDA", "IMU", "EEG"].map((sensor) => ({ sensor, text: "", on_device: sensor !== "EEG", present: false })) };
  try { status = await deviceStatus(); } catch { /* the tile still renders */ }
  renderTiles(checks, status);
  renderPredictions();
  if (!fresh) runAgent($("agent"), { quietScreen: true });
}
render();
