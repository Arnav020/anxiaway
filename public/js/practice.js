import { mountShell } from "/js/shell.js";
import * as ledger from "/js/ledger.js";
import * as store from "/js/store.js";
import { el } from "/js/store.js";

const $ = (id) => document.getElementById(id);
mountShell("practice");

/* A guided session rests on a recent check */
const latest = store.latestCheck();
const ready = store.sessionCheck();
const params = new URLSearchParams(location.search);
const pred = params.get("pred");
const sessionHref = (id) => (id ? `/session?pred=${encodeURIComponent(id)}` : "/session");
const checkHref = (id) => `/checkin?next=session${id ? `&pred=${encodeURIComponent(id)}` : ""}`;

if (!ready) {
  const hold = store.activeHold();
  const held = !!hold?.active;
  const blocked = held || (latest && latest.status !== "ok" && Date.now() - latest.at < store.SESSION_HOURS * 3600000);
  $("need-check").hidden = false;
  $("need-title").textContent = blocked ? "Guided sessions are paused for now" : "A two-minute check comes first";
  const until = held ? new Date(hold.until).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "";
  const text = held
    ? `Guided sessions are paused until ${until}, because ${hold.reason}. After that, a new check comes first. Please talk to someone you trust today. In India, Tele-MANAS is free on 14416, and in an emergency call 112. Breathing and your record stay open.`
    : blocked
      ? "Your latest check pointed to a person as the better next step. Breathing and your record stay open."
      : hold ? "Before a guided session, please do a new check, because the safety questions come first."
        : latest ? "Guided sessions need a check from the last 24 hours, because the safety questions come first." : "Guided sessions start from an anxiety check, because the safety questions come first.";
  store.helpText($("need-text"), text);
  $("need-go").hidden = blocked;
  $("need-go").href = checkHref(pred);
  const start = $("start-session");
  if (blocked) { start.removeAttribute("href"); start.setAttribute("aria-disabled", "true"); start.style.opacity = ".45"; start.style.pointerEvents = "none"; }
  else { start.href = checkHref(pred); start.firstChild.textContent = "Do a check, then start"; }
  if (params.get("need") === "check") $("need-check").scrollIntoView({ block: "center" });
} else {
  $("start-session").href = sessionHref(pred);
}

/* The week's real world step, from the last session */
const mission = store.prefs().mission;
if (mission) {
  $("mission-text").textContent = mission.text;
  $("mission-meta").textContent = `From your session ${store.agoText(mission.at)}. Afterwards, record what actually happened.`;
  $("mission-go").textContent = "Record what happened";
  $("mission-go").href = "/record#due-card";
} else {
  $("mission-text").textContent = "After a guided session, the agent sets one small real action here that tests your prediction in real life.";
  $("mission-meta").textContent = "Rehearsing alone doesn't test a fear. The real step does.";
}

/* Open predictions */
const open = ledger.openOnes().slice(0, 3);
$("open-empty").hidden = open.length > 0;
$("open-list").replaceChildren(...open.map((p) => {
  const li = el("li", "open-item");
  const go = el("a", "btn btn-white btn-sm", ready ? "Practise this" : "Check, then practise");
  go.href = ready ? sessionHref(p.id) : checkHref(p.id);
  go.style.alignSelf = "flex-start";
  li.append(el("span", "title-4", p.text), el("span", "caption-5 muted", `Felt ${p.belief_before}% likely${p.sample ? " · Sample" : ""}`), go);
  return li;
}));

/* Breathing: in for four, out for six, for two minutes */
const TOTAL_S = 120;
let timer = null;
function stopBreathing(message = "Ready when you are") {
  clearInterval(timer);
  timer = null;
  $("orb").classList.remove("in", "out");
  $("orb-count").textContent = "4";
  $("breath-say").textContent = message;
  $("breath-start").textContent = "Start";
  $("breath-bar").style.transform = "scaleX(0)";
}
function startBreathing() {
  let t = 0;
  const paint = () => {
    const phase = t % 10;
    const breathingIn = phase < 4;
    $("orb").classList.toggle("in", breathingIn);
    $("orb").classList.toggle("out", !breathingIn);
    $("orb-count").textContent = String(breathingIn ? 4 - phase : 10 - phase);
    $("breath-say").textContent = breathingIn ? "Breathe in" : "Breathe out, slowly";
    $("breath-bar").style.transform = `scaleX(${t / TOTAL_S})`;
  };
  paint();
  $("breath-start").textContent = "Stop";
  timer = setInterval(() => {
    t += 1;
    if (t >= TOTAL_S) return stopBreathing("Done. Notice how you feel now.");
    paint();
  }, 1000);
}
$("breath-open").addEventListener("click", () => $("breath-dlg").showModal());
$("breath-start").addEventListener("click", () => (timer ? stopBreathing() : startBreathing()));
$("breath-close").addEventListener("click", () => $("breath-dlg").close());
$("breath-dlg").addEventListener("close", () => stopBreathing());
if (location.hash === "#breathe") $("breath-dlg").showModal();
