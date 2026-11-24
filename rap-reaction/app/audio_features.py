"""음원에서 청자 반응과 관련된 음악적 특징을 뽑는다.

Claude는 오디오를 직접 듣지 못하므로, 여기서 계산한 수치와 구간 정보를
텍스트로 넘겨 반응을 추론하게 한다. 모든 값은 신호 처리 기반 추정치다.
"""

from __future__ import annotations

import subprocess

import numpy as np
import librosa

# 무료 호스팅(메모리 512MB)에서도 돌아가도록 16kHz 모노로 분석한다.
SR = 16000
HOP = 512
N_FFT = 1024
MAX_SECONDS = 420  # 7분 넘는 음원은 앞부분만 분석
HPSS_MAX_HZ = 4000  # 타악기/화성 분리는 이 주파수 아래에서만 (메모리 절약)


def load_audio(path: str) -> np.ndarray:
    """ffmpeg로 바로 16kHz 모노 float32로 디코딩한다 (librosa.load보다 메모리를 훨씬 적게 씀)."""
    cmd = [
        "ffmpeg", "-nostdin", "-v", "error", "-i", path, "-t", str(MAX_SECONDS),
        "-ac", "1", "-ar", str(SR), "-f", "f32le", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-300:])
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()

# Krumhansl-Kessler 조성 프로파일
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
_NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def mmss(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _estimate_key(chroma: np.ndarray) -> str:
    profile = chroma.mean(axis=1)
    best, best_score = "", -np.inf
    for i in range(12):
        for name, tmpl in (("major", _MAJOR), ("minor", _MINOR)):
            score = np.corrcoef(profile, np.roll(tmpl, i))[0, 1]
            if score > best_score:
                best, best_score = f"{_NOTES[i]} {name}", score
    return best


def _segment(features: np.ndarray, duration: float) -> list[tuple[int, int]]:
    """특징 행렬을 비슷한 구간끼리 나눈다. (시작 프레임, 끝 프레임) 목록."""
    n_frames = features.shape[1]
    k = int(np.clip(duration / 18, 3, 12))
    k = min(k, n_frames - 1)
    bounds = librosa.segment.agglomerative(features, k)
    bounds = sorted(set(int(b) for b in bounds) | {0, n_frames})
    return [(a, b) for a, b in zip(bounds[:-1], bounds[1:]) if b > a]


def _label_repeats(seg_vecs: list[np.ndarray], threshold: float = 0.92) -> list[str]:
    """평균 특징이 비슷한 구간에 같은 글자를 붙인다 (A, B, A, C, B ...)."""
    labels: list[str] = []
    protos: list[np.ndarray] = []
    for v in seg_vecs:
        v = v / (np.linalg.norm(v) + 1e-9)
        sims = [float(v @ p) for p in protos]
        if sims and max(sims) >= threshold:
            labels.append(chr(ord("A") + int(np.argmax(sims))))
        else:
            protos.append(v)
            labels.append(chr(ord("A") + len(protos) - 1) if len(protos) <= 26 else "?")
    return labels


def analyze_audio(path: str) -> dict:
    y, sr = load_audio(path), SR
    if y.size < sr * 5:
        raise ValueError("음원이 너무 짧습니다 (5초 미만).")
    duration = y.size / sr

    # 스펙트로그램은 한 번만 계산해서 모든 특징에 같이 쓴다 (메모리 절약).
    # 템포 추정은 촘촘한 간격(16ms)이 필요해서 HOP/2로 계산하고, 나머지는 한 칸씩 건너뛰어 쓴다.
    fine_hop = HOP // 2
    mag_fine = np.abs(librosa.stft(y, n_fft=N_FFT, hop_length=fine_hop))
    mel_fine = librosa.feature.melspectrogram(S=mag_fine ** 2, sr=sr, n_mels=64)

    # 템포 / 박자
    onset_env = librosa.onset.onset_strength(S=librosa.power_to_db(mel_fine), sr=sr, hop_length=fine_hop)
    # 템포는 곡 중간 90초 구간에서만 추정한다. 곡 전체로 하면 메모리를 수백 MB 더 쓴다.
    win = int(90 * sr / fine_hop)
    start = max(0, min(len(onset_env) - win, int(len(onset_env) * 0.25)))
    tempo = librosa.feature.tempo(onset_envelope=onset_env[start:start + win], sr=sr, hop_length=fine_hop)
    bpm = float(np.atleast_1d(tempo)[0])
    onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, hop_length=fine_hop)

    mag = np.ascontiguousarray(mag_fine[:, ::2])
    mel = np.ascontiguousarray(mel_fine[:, ::2])
    del mag_fine, mel_fine, onset_env

    # 음량
    rms = librosa.feature.rms(S=mag, frame_length=N_FFT)[0]
    rms_db = librosa.amplitude_to_db(rms, ref=1.0)
    loud = rms_db[rms_db > -60]

    # 대역별 에너지
    spec = mag ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=N_FFT)
    total = spec.sum() + 1e-12
    bass_ratio = float(spec[freqs < 150].sum() / total)
    high_ratio = float(spec[freqs > 5000].sum() / total)
    centroid = float(librosa.feature.spectral_centroid(S=spec, sr=sr).mean())

    # 타악기 / 화성 비중 (이미 계산한 스펙트로그램에서 분리해 시간을 아낀다)
    del spec
    low = mag[freqs < HPSS_MAX_HZ]
    harm_s, perc_s = librosa.decompose.hpss(low, kernel_size=17)
    h_e, p_e = float((harm_s ** 2).sum()), float((perc_s ** 2).sum())
    perc_ratio = p_e / (h_e + p_e + 1e-12)
    del perc_s, low

    # 조성 (화성 성분만으로 계산). 잘라낸 고음역은 0으로 채워 원래 크기로 맞춘다.
    harm_full = np.zeros_like(mag)
    harm_full[: harm_s.shape[0]] = harm_s
    del harm_s, mag
    chroma = librosa.feature.chroma_stft(S=harm_full ** 2, sr=sr, n_fft=N_FFT, hop_length=HOP)
    del harm_full
    key = _estimate_key(chroma)

    # 2초 단위 에너지 곡선 (0~1)
    frames_per_bin = max(1, int(2 * sr / HOP))
    n_bins = len(rms) // frames_per_bin
    curve = rms[: n_bins * frames_per_bin].reshape(n_bins, frames_per_bin).mean(axis=1)
    curve = curve / (curve.max() + 1e-12)

    # 인트로 길이: 비트가 본격적으로 들어오는 시점.
    # 에너지가 곡의 높은 수준(80퍼센타일)의 45%에 처음 도달하는 때로 본다.
    target = 0.45 * np.percentile(curve, 80)
    first = int(np.argmax(curve >= target)) if np.any(curve >= target) else 0
    intro_seconds = first * 2.0

    # 에너지가 크게 올라가는 순간 (드롭 / 훅 진입 후보)
    rises = []
    if n_bins > 4:
        diff = np.convolve(curve, np.ones(2) / 2, mode="same")
        jump = diff[2:] - diff[:-2]
        for idx in np.argsort(jump)[::-1]:
            t = (idx + 2) * 2.0
            if jump[idx] < 0.12:
                break
            if all(abs(t - r["seconds"]) > 15 for r in rises):
                rises.append({"seconds": t, "time": mmss(t), "energy_jump": round(float(jump[idx]), 2)})
            if len(rises) >= 4:
                break
        rises.sort(key=lambda r: r["seconds"])

    # 구간 나누기 + 반복 구간 찾기
    mfcc = librosa.feature.mfcc(S=librosa.power_to_db(mel), n_mfcc=13)
    del mel
    stack = np.vstack([librosa.util.normalize(chroma, axis=1), librosa.util.normalize(mfcc, axis=1)])
    # 0.5초 단위로 묶어서 계산량을 줄인다
    sync_hop = max(1, int(0.5 * sr / HOP))
    stack_s = librosa.util.sync(stack, np.arange(0, stack.shape[1], sync_hop), aggregate=np.mean)
    rms_s = librosa.util.sync(rms[None, :], np.arange(0, len(rms), sync_hop), aggregate=np.mean)[0]
    segs = _segment(stack_s, duration)
    seg_vecs = [stack_s[:, a:b].mean(axis=1) for a, b in segs]
    labels = _label_repeats(seg_vecs)
    rms_max = rms_s.max() + 1e-12
    sections = []
    for (a, b), label in zip(segs, labels):
        start, end = a * sync_hop * HOP / sr, min(duration, b * sync_hop * HOP / sr)
        if end - start < 3:  # 3초 미만 조각은 경계 오차로 보고 버린다
            continue
        sections.append({
            "start": mmss(start), "end": mmss(end),
            "start_seconds": round(start, 1), "end_seconds": round(end, 1),
            "label": label,
            "energy": round(float(rms_s[a:b].mean() / rms_max), 2),
        })

    # 가장 많이 반복되면서 에너지가 높은 구간 = 훅 후보
    counts: dict[str, list[dict]] = {}
    for s in sections:
        counts.setdefault(s["label"], []).append(s)
    repeated = {k: v for k, v in counts.items() if len(v) >= 2}
    hook = None
    if repeated:
        k = max(repeated, key=lambda k: (len(repeated[k]), np.mean([s["energy"] for s in repeated[k]])))
        hook = {
            "label": k,
            "occurrences": len(repeated[k]),
            "first_at": repeated[k][0]["start"],
            "first_at_seconds": repeated[k][0]["start_seconds"],
        }

    half_time_note = None
    if bpm >= 120:
        half_time_note = f"하프타임으로 느끼면 약 {bpm / 2:.0f} BPM"
    elif bpm <= 85:
        half_time_note = f"더블타임으로 느끼면 약 {bpm * 2:.0f} BPM"

    return {
        "duration": mmss(duration),
        "duration_seconds": round(duration, 1),
        "bpm": round(bpm, 1),
        "bpm_note": half_time_note,
        "key": key,
        "loudness_db": round(float(np.mean(loud)), 1) if loud.size else None,
        "dynamic_range_db": round(float(np.percentile(loud, 95) - np.percentile(loud, 10)), 1) if loud.size else None,
        "bass_ratio": round(bass_ratio, 3),
        "high_freq_ratio": round(high_ratio, 3),
        "brightness_hz": round(centroid),
        "percussive_ratio": round(perc_ratio, 3),
        "onsets_per_second": round(len(onsets) / duration, 2),
        "intro_seconds": intro_seconds,
        "energy_rises": rises,
        "sections": sections,
        "hook_candidate": hook,
        "energy_curve": [round(float(v), 3) for v in curve],
        "energy_curve_step_seconds": 2,
    }
