// Shared pieces drawn on more than one screen: the score gauge, the sensor mask and the agent's next step.
import * as ledger from "/js/ledger.js";
import { el, icon, api, checksForApi, sessionCheck, bandOf } from "/js/store.js";

const NS = "http://www.w3.org/2000/svg";
const BANDS = [[0, 5, "#A9C2B1"], [5, 10, "#D9CFB9"], [10, 15, "#D3A06F"], [15, 21, "#A8553D"]];

/* The GAD-7 score on its four bands. An empty gauge shows the bands with no marker. */
export function gauge(root, score, { light = false, caption = "" } = {}) {
  root.classList.add("gauge");
  root.classList.toggle("gauge-light", light);
  const cx = 150, cy = 150, r = 120;
  const point = (f) => {
    const a = Math.PI - f * Math.PI;
    return [cx + r * Math.cos(a), cy - r * Math.sin(a)];
  };
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 300 168");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", score === null ? "No check yet" : `GAD-7 score ${score} out of 21, ${bandOf(score).toLowerCase()} range`);
  BANDS.forEach(([from, to, color]) => {
    const [x1, y1] = point(from / 21 + 0.012);
    const [x2, y2] = point(to / 21 - 0.012);
    const arc = document.createElementNS(NS, "path");
    arc.setAttribute("d", `M${x1.toFixed(1)} ${y1.toFixed(1)}A${r} ${r} 0 0 1 ${x2.toFixed(1)} ${y2.toFixed(1)}`);
    arc.setAttribute("fill", "none");
    arc.setAttribute("stroke", color);
    arc.setAttribute("stroke-width", 20);
    arc.setAttribute("stroke-linecap", "round");
    arc.setAttribute("opacity", score === null ? ".45" : "1");
    svg.append(arc);
  });
  if (score !== null) {
    const needle = document.createElementNS(NS, "g");
    needle.setAttribute("class", "gauge-needle");
    const ring = document.createElementNS(NS, "circle");
    ring.setAttribute("cx", cx - r); ring.setAttribute("cy", cy); ring.setAttribute("r", 15);
    ring.setAttribute("fill", light ? "#1D2024" : "#F6F4EE");
    const core = document.createElementNS(NS, "circle");
    core.setAttribute("cx", cx - r); core.setAttribute("cy", cy); core.setAttribute("r", 7);
    core.setAttribute("fill", light ? "#FBFAF7" : "#252B33");
    needle.append(ring, core);
    svg.append(needle);
    setTimeout(() => { needle.style.transform = `rotate(${(score / 21) * 180}deg)`; }, 60);
  }
  const read = el("div", "gauge-read");
  read.append(el("strong", "big", score === null ? "–" : String(score)), el("small", "", caption || (score === null ? "No check yet" : `of 21 · ${bandOf(score)}`)));
  root.replaceChildren(svg, read);
}

/* The model's five inputs, and which of them are really present */
export function maskChips(root, mask) {
  root.classList.add("mask");
  root.replaceChildren(...mask.map((m) => {
    const chip = el("span", "mask-chip");
    chip.dataset.present = String(m.present);
    chip.dataset.device = String(m.on_device);
    chip.title = m.on_device ? m.text : `${m.text}. In our model, not on this device, so it's marked absent.`;
    chip.append(el("i"), el("span", "", m.on_device ? m.sensor : `${m.sensor} · not on device`));
    return chip;
  }));
}

/* The care agent: reads the checks and the predictions, then decides the next step */
const CACHE = "toward.agent.v1";
const keyOf = (predictions, checks) => JSON.stringify([predictions.map((p) => [p.id, p.outcome, p.belief_after, p.practised, p.due_at]), checks.map((c) => [c.at, c.score, c.impact, c.body_state])]);
const GO = {
  screen: () => ["Start a check", "/checkin"],
  person: () => ["Open my record to share", "/record"],
  check: () => ["Record what happened", "/record#due-card"],
  add: () => ["Log a prediction", "/record#add"],
  test: (id) => (sessionCheck() ? ["Start a guided session on this", `/session?pred=${encodeURIComponent(id)}`] : ["Go to practice", "/practice"]),
};

export async function runAgent(card, { force = false, animate = true, quietScreen = false } = {}) {
  const main = card.querySelector(".agent-main");
  const side = card.querySelector(".agent-trace");
  const predictions = ledger.forApi();
  const checks = checksForApi();
  const key = keyOf(predictions, checks);
  let review = null;
  if (!force) {
    try {
      const saved = JSON.parse(sessionStorage.getItem(CACHE) || "null");
      if (saved?.key === key) review = saved.review;
    } catch { /* no cache */ }
  }
  const fresh = !review;
  if (fresh) {
    main.replaceChildren(el("span", "caption-3", "The agent's next step"), el("div", "agent-skeleton"), el("p", "body-6 agent-why", "The agent is reading your checks and your record."));
    side.replaceChildren();
    try {
      review = await api("record/review", { predictions, checks });
      if (review.status === "ok") {
        ledger.applyFollowUps(review.follow_ups);
        try { sessionStorage.setItem(CACHE, JSON.stringify({ key, review })); } catch { /* optional */ }
      }
    } catch (err) {
      main.replaceChildren(el("span", "caption-3", "The agent's next step"), el("p", "title-2", err.message));
      return null;
    }
  }
  if (review.status !== "ok") {
    main.replaceChildren(el("span", "caption-3", "From fixed safety wording, not a language model"), el("p", "heading-6", review.message));
    side.hidden = true;
    return review;
  }
  side.hidden = false;
  const target = review.prediction_id ? ledger.get(review.prediction_id) : null;
  const [label, href] = GO[review.kind](review.prediction_id);
  const go = el("a", "btn btn-white", label);
  go.href = href;
  const again = el("button", "link-quiet caption-4", "Review again");
  again.type = "button";
  again.style.color = "#D9CFB9";
  again.addEventListener("click", () => runAgent(card, { force: true, quietScreen }));
  const actions = el("div", "row");
  // On Home the main button already starts a check, so the agent doesn't repeat it.
  if (!(quietScreen && review.kind === "screen")) actions.append(go);
  actions.append(again);
  main.replaceChildren(
    el("span", "caption-3", `The agent's next step · ${review.source === "Groq" ? "decided live" : "scripted fallback"}`),
    el("p", "title-2", review.pattern),
    el("h2", "heading-5", review.next_action),
    ...(target ? [el("p", "body-5", `"${target.text}"`)] : []),
    el("p", "body-6 agent-why", `Why: ${review.why}`),
    ...(review.support_note ? [el("p", "body-5", review.support_note)] : []),
    actions,
  );
  const list = el("ol", "trace");
  const legend = el("div", "row caption-4");
  const code = el("span", "row"); code.append(el("i", "dot dot-code"), el("span", "", "Fixed code"));
  const llm = el("span", "row"); llm.append(el("i", "dot dot-llm"), el("span", "", "Chosen by the language model"));
  const agent = el("span", "row"); agent.append(el("i", "dot dot-agent"), el("span", "", "Checking agent"));
  legend.append(code, llm, agent);
  side.replaceChildren(el("span", "caption-3", "How it decided"), legend, list);
  await ledger.renderTrace(list, review.trace, animate && fresh);
  return review;
}
export { icon };
