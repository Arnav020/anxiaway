// The "Integrating soon" dialog, shared by the landing page and the check-in.
import { wirePhotos } from "/js/lightbox.js";

let dialog = null;

function build() {
  dialog = document.createElement("dialog");
  dialog.className = "dlg dlg-connect clay";
  dialog.setAttribute("aria-labelledby", "connect-title");
  dialog.innerHTML = `
    <button class="dlg-x" type="button" aria-label="Close"><i class="icon i20" style="--i:url(/icons/x.svg)"></i></button>
    <span class="soon-badge caption-3">Integrating soon</span>
    <h2 class="heading-4" id="connect-title">Our wearable and app are on the way.</h2>
    <p class="body-3 muted">From our capstone: a 4 sensor prototype streams to a mobile app. Once connected, our multimodal anxiety model will read that stream, and its verdict will guide each step here.</p>
    <div class="connect-photos">
      <figure><button class="photo-open" type="button" aria-haspopup="dialog" data-caption="Our capstone prototype. ECG, PPG, skin conductance and motion on an ESP32."><img src="/img/prototype.jpg" alt="The wearable prototype: a black pouch holding the electronics, with ECG electrodes and sensor leads."><span class="photo-hint caption-3"><i class="icon i16" style="--i:url(/icons/arrows-out.svg)"></i>View full photo</span></button><figcaption class="caption-5"><strong>Prototype.</strong> ECG, PPG, skin conductance and motion on an ESP32.</figcaption></figure>
      <figure><button class="photo-open" type="button" aria-haspopup="dialog" data-caption="Our mobile app showing live telemetry from the prototype, from our mid-semester build."><img src="/img/app.jpg" alt="Screenshot of the mobile app showing live skin conductance and motion readings."><span class="photo-hint caption-3"><i class="icon i16" style="--i:url(/icons/arrows-out.svg)"></i>View full photo</span></button><figcaption class="caption-5"><strong>App.</strong> Live telemetry from the prototype, from our mid-semester build.</figcaption></figure>
    </div>
    <ul class="connect-list body-6">
      <li><strong>What's real today:</strong> the prototype and app, shown here.</li>
      <li><strong>Still in progress:</strong> training and validating our model, then linking it to this tool.</li>
      <li><strong>Until then:</strong> this demo uses a labelled synthetic stream and a simulated verdict.</li>
    </ul>
    <div class="dlg-actions"><button class="btn btn-cta" type="button" data-close>Got it</button></div>`;
  dialog.querySelector(".dlg-x").addEventListener("click", () => dialog.close());
  dialog.querySelector("[data-close]").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });
  wirePhotos(dialog);
  document.body.append(dialog);
}

export function openConnect() {
  if (!dialog) build();
  dialog.showModal();
}
