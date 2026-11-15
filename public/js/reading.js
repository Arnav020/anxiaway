// One body reading: fetched from the server, where fixed code classifies it, then played back on screen.
import { api } from "/js/store.js";

export const SCENARIOS = [["calm", "Calm"], ["rising", "Raised"], ["high", "High"], ["movement", "Moving about"], ["sensor_loss", "ECG lead off"]];
export const STATE_SHORT = { CALIBRATING: "Baseline", LOW: "Near baseline", ENGAGED: "Above baseline", HIGH: "Well above", LIMIT: "Far above", UNKNOWN: "Not trusted" };
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

export const fetchDemo = (scenario, cursor) => api("reading", { mode: "demo", scenario, t: cursor?.t || 0, previous: cursor?.previous || 0 });
export const fetchDevice = (baseline) => api("reading", { mode: "device", baseline_hr: baseline?.hr ?? null, baseline_eda: baseline?.eda ?? null });
export const heartOf = (s) => s.ecg_hr ?? s.ppg_hr;

// Plays samples one by one. Returns a function that cancels the playback.
export function play(samples, durationMs, onSample, onDone) {
  if (reduced || document.hidden) {
    samples.forEach((s, i) => onSample(s, i));
    onDone();
    return () => {};
  }
  let shown = 0;
  let stopped = false;
  const start = performance.now();
  const tick = () => {
    if (stopped) return;
    const goal = Math.min(samples.length, Math.ceil(((performance.now() - start) / durationMs) * samples.length));
    while (shown < goal) { onSample(samples[shown], shown); shown += 1; }
    if (shown >= samples.length) onDone();
    else requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  return () => { stopped = true; };
}
