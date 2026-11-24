/*
 * 브라우저에서 음원을 분석한다. 음원 파일은 서버로 보내지 않고, 여기서 뽑은 수치만 보낸다.
 *
 * librosa로 만들었던 서버 분석(BPM, 조성, 저음 비중, 구간 구조, 훅 후보, 에너지 흐름)을
 * 같은 방식으로 옮긴 것이다. 입력은 16kHz 모노 Float32Array.
 *
 * 브라우저: Web Worker 안에서 importScripts로 불러 RapAudio.analyzeSamples(samples) 호출
 * Node(테스트): require("./audio-analyzer.js").analyzeSamples(samples)
 */
(function (root) {
  "use strict";

  const SR = 16000;
  const N_FFT = 1024;
  const HOP = 512;
  const FINE_HOP = HOP / 2;
  const N_BINS = N_FFT / 2 + 1;
  const N_MELS = 64;
  const HPSS_MAX_HZ = 4000;
  const MAX_SECONDS = 420;

  const MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88];
  const MINOR = [6.33, 2.68, 3.52, 5.38, 2.6, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17];
  const NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

  function mmss(seconds) {
    const s = Math.round(seconds);
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }
  const round = (x, d) => Math.round(x * 10 ** d) / 10 ** d;

  // ---------- FFT ----------
  function makeFFT(n) {
    const levels = Math.log2(n);
    const rev = new Uint32Array(n);
    for (let i = 0; i < n; i++) {
      let r = 0;
      for (let b = 0; b < levels; b++) r |= ((i >> b) & 1) << (levels - 1 - b);
      rev[i] = r;
    }
    const cos = new Float64Array(n / 2), sin = new Float64Array(n / 2);
    for (let i = 0; i < n / 2; i++) {
      cos[i] = Math.cos((2 * Math.PI * i) / n);
      sin[i] = -Math.sin((2 * Math.PI * i) / n);
    }
    const re = new Float64Array(n), im = new Float64Array(n);
    // 실수 입력 -> 파워 스펙트럼 (0..n/2)
    return function power(input, out) {
      for (let i = 0; i < n; i++) { re[rev[i]] = input[i]; im[rev[i]] = 0; }
      for (let size = 2; size <= n; size <<= 1) {
        const half = size >> 1, step = n / size;
        for (let start = 0; start < n; start += size) {
          for (let j = 0; j < half; j++) {
            const k = j * step, a = start + j, b = a + half;
            const tr = re[b] * cos[k] - im[b] * sin[k];
            const ti = re[b] * sin[k] + im[b] * cos[k];
            re[b] = re[a] - tr; im[b] = im[a] - ti;
            re[a] += tr; im[a] += ti;
          }
        }
      }
      for (let i = 0; i <= n / 2; i++) out[i] = re[i] * re[i] + im[i] * im[i];
    };
  }

  // ---------- 필터뱅크 (librosa와 같은 정의) ----------
  const fftFreqs = Float64Array.from({ length: N_BINS }, (_, i) => (i * SR) / N_FFT);

  function hzToMel(f) {
    const fsp = 200 / 3, logstep = Math.log(6.4) / 27;
    return f < 1000 ? f / fsp : 15 + Math.log(f / 1000) / logstep;
  }
  function melToHz(m) {
    const fsp = 200 / 3, logstep = Math.log(6.4) / 27;
    return m < 15 ? fsp * m : 1000 * Math.exp(logstep * (m - 15));
  }
  function melFilters() {
    const lo = hzToMel(0), hi = hzToMel(SR / 2);
    const melF = Array.from({ length: N_MELS + 2 }, (_, i) => melToHz(lo + ((hi - lo) * i) / (N_MELS + 1)));
    const w = Array.from({ length: N_MELS }, () => new Float64Array(N_BINS));
    for (let m = 0; m < N_MELS; m++) {
      const enorm = 2 / (melF[m + 2] - melF[m]);
      for (let k = 0; k < N_BINS; k++) {
        const lower = (fftFreqs[k] - melF[m]) / (melF[m + 1] - melF[m]);
        const upper = (melF[m + 2] - fftFreqs[k]) / (melF[m + 2] - melF[m + 1]);
        w[m][k] = Math.max(0, Math.min(lower, upper)) * enorm;
      }
    }
    return w;
  }
  function chromaFilters() {
    const nC = 12, A0 = 440 / 16;
    const frq = [];
    for (let i = 1; i < N_FFT; i++) frq.push(nC * Math.log2(((i * SR) / N_FFT) / A0));
    frq.unshift(frq[0] - 1.5 * nC);
    const width = frq.map((v, i) => (i < frq.length - 1 ? Math.max(frq[i + 1] - v, 1) : 1));
    const w = Array.from({ length: nC }, () => new Float64Array(N_FFT));
    for (let k = 0; k < N_FFT; k++) {
      let norm = 0;
      for (let c = 0; c < nC; c++) {
        let d = frq[k] - c;
        d = ((d + 6 + 10 * nC) % nC) - 6;
        const v = Math.exp(-0.5 * ((2 * d) / width[k]) ** 2);
        w[c][k] = v; norm += v * v;
      }
      norm = Math.sqrt(norm) || 1;
      const oct = Math.exp(-0.5 * ((frq[k] / nC - 5) / 2) ** 2);
      for (let c = 0; c < nC; c++) w[c][k] = (w[c][k] / norm) * oct;
    }
    // A 기준 -> C 기준으로 회전, 앞쪽 N_BINS만 사용
    return Array.from({ length: nC }, (_, c) => w[(c + 3) % nC].slice(0, N_BINS));
  }

  // ---------- 보조 함수 ----------
  function percentile(arr, p) {
    const a = Float64Array.from(arr).sort();
    if (!a.length) return 0;
    const idx = (p / 100) * (a.length - 1), lo = Math.floor(idx), hi = Math.ceil(idx);
    return a[lo] + (a[hi] - a[lo]) * (idx - lo);
  }
  function powerToDb(mat) {
    let mx = -Infinity;
    for (let i = 0; i < mat.length; i++) {
      mat[i] = 10 * Math.log10(Math.max(1e-10, mat[i]));
      if (mat[i] > mx) mx = mat[i];
    }
    const floor = mx - 80;
    for (let i = 0; i < mat.length; i++) if (mat[i] < floor) mat[i] = floor;
    return mat;
  }
  function median(buf, n) {
    const a = buf.subarray(0, n).sort();
    return a[n >> 1];
  }
  // scipy.ndimage 'reflect' 경계 (d c b a | a b c d)
  const reflect = (i, n) => {
    while (i < 0 || i >= n) i = i < 0 ? -i - 1 : 2 * n - i - 1;
    return i;
  };
  function corr(a, b) {
    const n = a.length;
    const ma = a.reduce((s, v) => s + v, 0) / n, mb = b.reduce((s, v) => s + v, 0) / n;
    let num = 0, da = 0, db = 0;
    for (let i = 0; i < n; i++) {
      num += (a[i] - ma) * (b[i] - mb); da += (a[i] - ma) ** 2; db += (b[i] - mb) ** 2;
    }
    return num / Math.sqrt(da * db || 1);
  }

  // ---------- 템포 (librosa.feature.tempo와 같은 방식, 간격을 두고 샘플링) ----------
  function estimateTempo(env) {
    const win = Math.round((8 * SR) / FINE_HOP); // 8초 자기상관 창
    const hann = Float64Array.from({ length: win }, (_, i) => 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / win));
    const acc = new Float64Array(win);
    const seg = new Float64Array(win);
    let frames = 0;
    const step = 8;
    for (let c = 0; c + win <= env.length || (c === 0 && frames === 0); c += step) {
      for (let i = 0; i < win; i++) seg[i] = (c + i < env.length ? env[c + i] : 0) * hann[i];
      const ac = new Float64Array(win);
      for (let lag = 0; lag < win; lag++) {
        let s = 0;
        for (let i = 0; i + lag < win; i++) s += seg[i] * seg[i + lag];
        ac[lag] = s;
      }
      let mx = 0;
      for (let lag = 0; lag < win; lag++) mx = Math.max(mx, Math.abs(ac[lag]));
      if (mx > 0) for (let lag = 0; lag < win; lag++) acc[lag] += ac[lag] / mx;
      frames++;
      if (c + win > env.length) break;
    }
    let best = 0, bestScore = -Infinity;
    for (let lag = 1; lag < win; lag++) {
      const bpm = (60 * SR) / (FINE_HOP * lag);
      if (bpm > 320) continue;
      const prior = -0.5 * ((Math.log2(bpm) - Math.log2(120)) / 1) ** 2;
      const score = Math.log1p(1e6 * (acc[lag] / frames)) + prior;
      if (score > bestScore) { bestScore = score; best = bpm; }
    }
    return best;
  }

  function onsetDetect(env) {
    let mn = Infinity, mx = -Infinity;
    for (const v of env) { mn = Math.min(mn, v); mx = Math.max(mx, v); }
    const x = env.map((v) => (v - mn) / (mx - mn || 1));
    const preMax = Math.floor((0.03 * SR) / FINE_HOP), postMax = 1;
    const preAvg = Math.floor((0.1 * SR) / FINE_HOP), postAvg = Math.floor((0.1 * SR) / FINE_HOP) + 1;
    const wait = Math.floor((0.03 * SR) / FINE_HOP), delta = 0.07;
    const peaks = [];
    let last = -Infinity;
    for (let n = 0; n < x.length; n++) {
      let m = -Infinity;
      for (let i = Math.max(0, n - preMax); i < Math.min(x.length, n + postMax); i++) m = Math.max(m, x[i]);
      if (x[n] !== m) continue;
      let s = 0, c = 0;
      for (let i = Math.max(0, n - preAvg); i < Math.min(x.length, n + postAvg); i++) { s += x[i]; c++; }
      if (x[n] >= s / c + delta && n - last > wait) { peaks.push(n); last = n; }
    }
    return peaks.length;
  }

  // ---------- 구간 나누기 (시간 순서를 지키는 Ward 병합) ----------
  function segment(cols, k) {
    // cols: 각 원소가 특징 벡터 하나 (시간 순서)
    let clusters = cols.map((v, i) => ({ start: i, end: i + 1, n: 1, sum: Float64Array.from(v) }));
    const cost = (a, b) => {
      let d = 0;
      for (let j = 0; j < a.sum.length; j++) d += (a.sum[j] / a.n - b.sum[j] / b.n) ** 2;
      return ((a.n * b.n) / (a.n + b.n)) * d;
    };
    while (clusters.length > k) {
      let best = 0, bestCost = Infinity;
      for (let i = 0; i < clusters.length - 1; i++) {
        const c = cost(clusters[i], clusters[i + 1]);
        if (c < bestCost) { bestCost = c; best = i; }
      }
      const a = clusters[best], b = clusters[best + 1];
      const sum = a.sum.map((v, j) => v + b.sum[j]);
      clusters.splice(best, 2, { start: a.start, end: b.end, n: a.n + b.n, sum });
    }
    return clusters.map((c) => [c.start, c.end]);
  }

  function labelRepeats(vecs, threshold = 0.92) {
    const labels = [], protos = [];
    for (const v0 of vecs) {
      const nrm = Math.sqrt(v0.reduce((s, x) => s + x * x, 0)) + 1e-9;
      const v = v0.map((x) => x / nrm);
      let bi = -1, bs = -Infinity;
      protos.forEach((p, i) => {
        const s = p.reduce((acc, x, j) => acc + x * v[j], 0);
        if (s > bs) { bs = s; bi = i; }
      });
      if (bi >= 0 && bs >= threshold) labels.push(String.fromCharCode(65 + bi));
      else { protos.push(v); labels.push(protos.length <= 26 ? String.fromCharCode(64 + protos.length) : "?"); }
    }
    return labels;
  }

  // ---------- 메인 ----------
  function analyzeSamples(input, onProgress = () => {}) {
    const y = input.length > MAX_SECONDS * SR ? input.subarray(0, MAX_SECONDS * SR) : input;
    if (y.length < SR * 5) throw new Error("음원이 너무 짧습니다 (5초 미만).");
    const duration = y.length / SR;

    const fft = makeFFT(N_FFT);
    const hann = Float64Array.from({ length: N_FFT }, (_, i) => 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / N_FFT));
    const melW = melFilters();
    const nFine = 1 + Math.floor(y.length / FINE_HOP);
    const nCoarse = Math.ceil(nFine / 2);

    const melFine = new Float64Array(nFine * N_MELS);           // [t * N_MELS + m]
    const mag = new Float32Array(nCoarse * N_BINS);             // [t * N_BINS + k], 진폭
    const frame = new Float64Array(N_FFT), pw = new Float64Array(N_BINS);
    const pad = N_FFT / 2;

    for (let t = 0; t < nFine; t++) {
      const off = t * FINE_HOP - pad;
      for (let i = 0; i < N_FFT; i++) {
        const idx = off + i;
        frame[i] = idx >= 0 && idx < y.length ? y[idx] * hann[i] : 0;
      }
      fft(frame, pw);
      for (let m = 0; m < N_MELS; m++) {
        const w = melW[m];
        let s = 0;
        for (let k = 0; k < N_BINS; k++) s += w[k] * pw[k];
        melFine[t * N_MELS + m] = s;
      }
      if (t % 2 === 0) {
        const base = (t / 2) * N_BINS;
        for (let k = 0; k < N_BINS; k++) mag[base + k] = Math.sqrt(pw[k]);
      }
      if (t % 2000 === 0) onProgress(0.5 * (t / nFine));
    }

    // 코스 프레임(512 간격) mel 은 촘촘한 mel 에서 한 칸씩 건너뛰어 가져온다
    const melCoarse = new Float64Array(nCoarse * N_MELS);
    for (let t = 0; t < nCoarse; t++)
      for (let m = 0; m < N_MELS; m++) melCoarse[t * N_MELS + m] = melFine[2 * t * N_MELS + m];

    // 온셋 강도 (librosa.onset.onset_strength)
    powerToDb(melFine);
    const lagPad = 1 + Math.floor(2048 / (2 * FINE_HOP));
    const env = new Float64Array(nFine);
    for (let t = 1; t < nFine; t++) {
      let s = 0;
      for (let m = 0; m < N_MELS; m++) s += Math.max(0, melFine[t * N_MELS + m] - melFine[(t - 1) * N_MELS + m]);
      const dst = t - 1 + lagPad;
      if (dst < nFine) env[dst] = s / N_MELS;
    }

    // 템포: 곡 중간 90초 구간
    const win = Math.floor((90 * SR) / FINE_HOP);
    const tStart = Math.max(0, Math.min(nFine - win, Math.floor(nFine * 0.25)));
    const bpm = estimateTempo(env.subarray(tStart, tStart + win));
    const onsetCount = onsetDetect(Array.from(env));
    onProgress(0.6);

    // 음량, 대역 에너지, 밝기
    const rms = new Float64Array(nCoarse);
    let total = 0, bass = 0, high = 0, centroidSum = 0;
    for (let t = 0; t < nCoarse; t++) {
      let p = 0, num = 0, den = 0;
      for (let k = 0; k < N_BINS; k++) {
        const v = mag[t * N_BINS + k] ** 2;
        const half = k === 0 || k === N_BINS - 1 ? 0.5 : 1;
        p += v * half;
        total += v;
        if (fftFreqs[k] < 150) bass += v;
        if (fftFreqs[k] > 5000) high += v;
        num += fftFreqs[k] * v; den += v;
      }
      rms[t] = Math.sqrt((2 * p) / (N_FFT * N_FFT));
      centroidSum += den > 0 ? num / den : 0;
    }
    let maxDb = -Infinity;
    const rmsDb = Array.from(rms, (v) => { const d = 20 * Math.log10(Math.max(1e-5, v)); maxDb = Math.max(maxDb, d); return d; })
      .map((d) => Math.max(d, maxDb - 80));
    const loud = rmsDb.filter((d) => d > -60);

    // 타악기/화성 분리 (4kHz 아래만, kernel 17, soft mask)
    const nLow = fftFreqs.filter((f) => f < HPSS_MAX_HZ).length;
    const harm = new Float32Array(nCoarse * N_BINS); // 고음역은 0
    const buf = new Float32Array(17);
    let hE = 0, pE = 0;
    for (let t = 0; t < nCoarse; t++) {
      for (let k = 0; k < nLow; k++) {
        for (let j = -8; j <= 8; j++) buf[j + 8] = mag[reflect(t + j, nCoarse) * N_BINS + k];
        const H = median(buf, 17);
        for (let j = -8; j <= 8; j++) buf[j + 8] = mag[t * N_BINS + reflect(k + j, nLow)];
        const P = median(buf, 17);
        const S = mag[t * N_BINS + k];
        const Z = Math.max(H, P);
        let mh = 0.5, mp = 0.5;
        if (Z > 1e-38) {
          const a = (H / Z) ** 2, b = (P / Z) ** 2;
          mh = a / (a + b); mp = b / (a + b);
        }
        harm[t * N_BINS + k] = S * mh;
        hE += (S * mh) ** 2; pE += (S * mp) ** 2;
      }
      if (t % 1000 === 0) onProgress(0.6 + 0.25 * (t / nCoarse));
    }
    const percRatio = pE / (hE + pE + 1e-12);

    // 크로마 + 조성
    const chW = chromaFilters();
    const chroma = Array.from({ length: 12 }, () => new Float64Array(nCoarse));
    for (let t = 0; t < nCoarse; t++) {
      let mx = 0;
      for (let c = 0; c < 12; c++) {
        let s = 0;
        for (let k = 0; k < N_BINS; k++) s += chW[c][k] * harm[t * N_BINS + k] ** 2;
        chroma[c][t] = s; mx = Math.max(mx, Math.abs(s));
      }
      if (mx > 0) for (let c = 0; c < 12; c++) chroma[c][t] /= mx;
    }
    const profile = chroma.map((row) => row.reduce((s, v) => s + v, 0) / nCoarse);
    let key = "", keyScore = -Infinity;
    for (let i = 0; i < 12; i++) {
      for (const [name, tmpl] of [["major", MAJOR], ["minor", MINOR]]) {
        const rolled = tmpl.map((_, j) => tmpl[(j - i + 12) % 12]);
        const s = corr(profile, rolled);
        if (s > keyScore) { keyScore = s; key = `${NOTES[i]} ${name}`; }
      }
    }

    // MFCC (13개)
    powerToDb(melCoarse);
    const mfcc = Array.from({ length: 13 }, () => new Float64Array(nCoarse));
    for (let t = 0; t < nCoarse; t++) {
      for (let c = 0; c < 13; c++) {
        let s = 0;
        for (let m = 0; m < N_MELS; m++) s += melCoarse[t * N_MELS + m] * Math.cos((Math.PI * c * (2 * m + 1)) / (2 * N_MELS));
        mfcc[c][t] = s * (c === 0 ? Math.sqrt(1 / N_MELS) : Math.sqrt(2 / N_MELS));
      }
    }
    onProgress(0.9);

    // 2초 단위 에너지 곡선
    const fpb = Math.max(1, Math.floor((2 * SR) / HOP));
    const nb = Math.floor(rms.length / fpb);
    let curve = Array.from({ length: nb }, (_, i) => {
      let s = 0;
      for (let j = 0; j < fpb; j++) s += rms[i * fpb + j];
      return s / fpb;
    });
    const cmax = Math.max(...curve, 0) + 1e-12;
    curve = curve.map((v) => v / cmax);

    // 비트가 들어오는 시점
    const target = 0.45 * percentile(curve, 80);
    const firstIdx = curve.findIndex((v) => v >= target);
    const introSeconds = (firstIdx >= 0 ? firstIdx : 0) * 2;

    // 에너지가 크게 오르는 순간
    const rises = [];
    if (nb > 4) {
      const sm = curve.map((v, i) => (v + (i > 0 ? curve[i - 1] : 0)) / 2);
      const jump = sm.slice(2).map((v, i) => v - sm[i]);
      const order = jump.map((v, i) => i).sort((a, b) => jump[b] - jump[a]);
      for (const idx of order) {
        if (jump[idx] < 0.12) break;
        const t = (idx + 2) * 2;
        if (rises.every((r) => Math.abs(t - r.seconds) > 15))
          rises.push({ seconds: t, time: mmss(t), energy_jump: round(jump[idx], 2) });
        if (rises.length >= 4) break;
      }
      rises.sort((a, b) => a.seconds - b.seconds);
    }

    // 구간 구조: 크로마 + MFCC 를 0.5초 단위로 평균 내서 나눈다
    const rowNorm = (rows) => rows.map((r) => {
      let mx = 0;
      for (const v of r) mx = Math.max(mx, Math.abs(v));
      return mx > 0 ? r.map((v) => v / mx) : r;
    });
    const feats = [...rowNorm(chroma), ...rowNorm(mfcc)];
    const syncHop = Math.max(1, Math.floor((0.5 * SR) / HOP));
    const cols = [], rmsS = [];
    for (let a = 0; a < nCoarse; a += syncHop) {
      const b = Math.min(nCoarse, a + syncHop);
      cols.push(feats.map((r) => { let s = 0; for (let t = a; t < b; t++) s += r[t]; return s / (b - a); }));
      let s = 0;
      for (let t = a; t < b; t++) s += rms[t];
      rmsS.push(s / (b - a));
    }
    const nSegments = Math.max(1, Math.min(Math.floor(Math.max(3, Math.min(12, duration / 18))), cols.length - 1));
    const segs = segment(cols, nSegments);
    const segVecs = segs.map(([a, b]) => cols[0].map((_, j) => {
      let s = 0;
      for (let i = a; i < b; i++) s += cols[i][j];
      return s / (b - a);
    }));
    const labels = labelRepeats(segVecs);
    const rmsMax = Math.max(...rmsS) + 1e-12;
    const sections = [];
    segs.forEach(([a, b], i) => {
      const start = (a * syncHop * HOP) / SR, end = Math.min(duration, (b * syncHop * HOP) / SR);
      if (end - start < 3) return;
      let s = 0;
      for (let j = a; j < b; j++) s += rmsS[j];
      sections.push({
        start: mmss(start), end: mmss(end),
        start_seconds: round(start, 1), end_seconds: round(end, 1),
        label: labels[i], energy: round(s / (b - a) / rmsMax, 2),
      });
    });

    // 훅 후보: 가장 많이 반복되면서 에너지가 높은 구간
    const groups = {};
    for (const s of sections) (groups[s.label] = groups[s.label] || []).push(s);
    let hook = null, hookScore = null;
    for (const [lab, list] of Object.entries(groups)) {
      if (list.length < 2) continue;
      const e = list.reduce((acc, s) => acc + s.energy, 0) / list.length;
      if (!hookScore || list.length > hookScore[0] || (list.length === hookScore[0] && e > hookScore[1])) {
        hookScore = [list.length, e];
        hook = { label: lab, occurrences: list.length, first_at: list[0].start, first_at_seconds: list[0].start_seconds };
      }
    }

    let bpmNote = null;
    if (bpm >= 120) bpmNote = `하프타임으로 느끼면 약 ${Math.round(bpm / 2)} BPM`;
    else if (bpm <= 85) bpmNote = `더블타임으로 느끼면 약 ${Math.round(bpm * 2)} BPM`;
    onProgress(1);

    const mean = (a) => a.reduce((s, v) => s + v, 0) / a.length;
    return {
      duration: mmss(duration),
      duration_seconds: round(duration, 1),
      bpm: round(bpm, 1),
      bpm_note: bpmNote,
      key,
      loudness_db: loud.length ? round(mean(loud), 1) : null,
      dynamic_range_db: loud.length ? round(percentile(loud, 95) - percentile(loud, 10), 1) : null,
      bass_ratio: round(bass / (total + 1e-12), 3),
      high_freq_ratio: round(high / (total + 1e-12), 3),
      brightness_hz: Math.round(centroidSum / nCoarse),
      percussive_ratio: round(percRatio, 3),
      onsets_per_second: round(onsetCount / duration, 2),
      intro_seconds: introSeconds,
      energy_rises: rises,
      sections,
      hook_candidate: hook,
      energy_curve: curve.map((v) => round(v, 3)),
      energy_curve_step_seconds: 2,
    };
  }

  const api = { analyzeSamples, SR, MAX_SECONDS };
  root.RapAudio = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof self !== "undefined" ? self : globalThis);
