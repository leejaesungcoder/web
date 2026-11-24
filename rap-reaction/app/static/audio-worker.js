// 음원 분석을 별도 스레드에서 돌려 화면이 멈추지 않게 한다.
importScripts("audio-analyzer.js");

self.onmessage = (e) => {
  try {
    const samples = new Float32Array(e.data.samples);
    const result = self.RapAudio.analyzeSamples(samples, (p) => self.postMessage({ type: "progress", p }));
    self.postMessage({ type: "done", result });
  } catch (err) {
    self.postMessage({ type: "error", message: err.message || String(err) });
  }
};
