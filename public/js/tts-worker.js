// Kokoro speech synthesis, off the main thread so the interface never stutters.
import { KokoroTTS } from "https://cdn.jsdelivr.net/npm/kokoro-js@1.2.1/dist/kokoro.web.js";

let tts = null;
const MODEL = "onnx-community/Kokoro-82M-v1.0-ONNX";

async function hasGpu(force) {
  if (force === "wasm") return false;
  try {
    const adapter = await navigator.gpu?.requestAdapter();
    return !!adapter && !adapter.info?.isFallbackAdapter;
  } catch {
    return false;
  }
}

self.onmessage = async ({ data }) => {
  try {
    if (data.type === "load") {
      const gpu = await hasGpu(data.force);
      tts = await KokoroTTS.from_pretrained(MODEL, gpu ? { dtype: "fp32", device: "webgpu" } : { dtype: "q8", device: "wasm" });
      // Measure real speed on this device, so slow machines don't keep people waiting.
      const started = performance.now();
      const sample = await tts.generate("How are you feeling right now?", { voice: "am_fenrir" });
      const seconds = sample.audio.length / sample.sampling_rate;
      self.postMessage({ type: "ready", device: gpu ? "webgpu" : "wasm", speed: ((performance.now() - started) / 1000) / seconds });
    } else if (data.type === "speak") {
      const audio = await tts.generate(data.text, { voice: data.voice });
      const samples = audio.audio;
      self.postMessage({ type: "audio", id: data.id, samples, rate: audio.sampling_rate }, [samples.buffer]);
    }
  } catch (error) {
    self.postMessage({ type: "error", id: data.id, message: String(error) });
  }
};
