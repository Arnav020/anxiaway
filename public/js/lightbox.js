// Opens a photo uncropped, on top of anything else on the page.
let box = null;

function build() {
  box = document.createElement("dialog");
  box.className = "lightbox";
  box.setAttribute("aria-label", "Full view of the photo");
  box.innerHTML = `
    <button class="dlg-x" type="button" aria-label="Close full view"><i class="icon i20" style="--i:url(/icons/x.svg)"></i></button>
    <figure><img alt=""><figcaption class="body-6"></figcaption></figure>`;
  box.querySelector(".dlg-x").addEventListener("click", () => box.close());
  box.addEventListener("click", (e) => { if (e.target === box || e.target.tagName === "FIGURE") box.close(); });
  document.body.append(box);
}

export function openPhoto(img, caption) {
  if (!box) build();
  const shown = box.querySelector("img");
  shown.src = img.dataset.full || img.currentSrc || img.src;
  shown.alt = img.alt;
  box.querySelector("figcaption").textContent = caption;
  box.showModal();
}

// Any <button class="photo-open"> holding an image opens that image in full.
export function wirePhotos(root = document) {
  root.querySelectorAll(".photo-open").forEach((button) => {
    button.addEventListener("click", () => openPhoto(button.querySelector("img"), button.dataset.caption || ""));
  });
}
