#!/usr/bin/env python3
"""Acoustic profiling of a reference voice + best-fit edge-tts voice mapping.

Pure standard library. This is STYLE APPROXIMATION, not voice cloning: it matches
pitch register, speaking rate, brightness and loudness to the closest available
neural voice. True cloning needs an external model (see references/voice-clone.md).
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import wave
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PipelineError, ffmpeg, write_json  # noqa: E402

# (voice id, median F0 Hz, brightness 0-1, label)
VOICE_TABLE = [
    ("zh-CN-YunjianNeural",  108, 0.25, "男声 浑厚 纪录片/解说"),
    ("zh-CN-YunyangNeural",  116, 0.35, "男声 新闻播报 沉稳"),
    ("zh-CN-YunxiNeural",    126, 0.55, "男声 青年 自然口语"),
    ("zh-CN-YunxiaNeural",   148, 0.70, "男声 少年感 轻快"),
    ("zh-CN-XiaoxiaoNeural", 198, 0.50, "女声 温柔 通用"),
    ("zh-CN-XiaoyiNeural",   212, 0.75, "女声 活泼 年轻"),
    ("zh-CN-liaoning-XiaobeiNeural", 190, 0.80, "女声 东北 生活化"),
    ("zh-CN-shaanxi-XiaoniNeural",   194, 0.65, "女声 陕西 亲和"),
    ("zh-TW-HsiaoChenNeural", 200, 0.45, "女声 台湾腔 温和"),
    ("zh-CN-YunfengNeural",   120, 0.30, "男声 低沉 成熟"),
]

RATE = 8000
FRAME = 1024
HOP = 800  # 100 ms


def extract_voice_track(cfg, video: Path, out_wav: Path) -> Path:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    # Center-ish downmix + speech band + light denoise. Keeps narration, weakens music.
    af = "highpass=f=85,lowpass=f=7500,afftdn=nf=-20,alimiter=limit=0.95"
    ffmpeg(cfg, ["-i", str(video), "-vn", "-af", af, "-ac", "1", "-ar", str(RATE),
                 "-c:a", "pcm_s16le", str(out_wav)], capture=True, check=True)
    return out_wav


def read_samples(wav_path: Path) -> array:
    with wave.open(str(wav_path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise PipelineError("voice track must be 16-bit mono")
        if w.getframerate() != RATE:
            raise PipelineError(f"voice track must be {RATE} Hz")
        data = array("h")
        data.frombytes(w.readframes(w.getnframes()))
    if sys.byteorder == "big":
        data.byteswap()
    return data


def _rms(frame: array) -> float:
    if not frame:
        return 0.0
    s = 0
    for v in frame:
        s += v * v
    return math.sqrt(s / len(frame))


def _f0(frame: array) -> float | None:
    """Normalised autocorrelation pitch estimate, 70-400 Hz."""
    n = len(frame)
    e = sum(v * v for v in frame)
    if e < 1e6:
        return None
    best, best_lag = 0.0, 0
    for lag in range(20, 115):
        acc = 0
        # step 2 keeps this fast in pure Python
        for i in range(0, n - lag, 2):
            acc += frame[i] * frame[i + lag]
        score = acc / e
        if score > best:
            best, best_lag = score, lag
    if best < 0.28 or best_lag == 0:
        return None
    return RATE / best_lag


def _zcr(frame: array) -> float:
    changes = 0
    prev = frame[0]
    for v in frame[1:]:
        if (v >= 0) != (prev >= 0):
            changes += 1
        prev = v
    return changes / max(1, len(frame))


def analyse_wav(wav_path: Path) -> dict:
    samples = read_samples(wav_path)
    total = len(samples)
    if total < FRAME * 3:
        return {"valid": False, "reason": "audio too short or no audio stream"}

    frames = []
    for start in range(0, total - FRAME, HOP):
        f = samples[start:start + FRAME]
        frames.append((start / RATE, _rms(f), f))
    rms_all = [r for _, r, _ in frames]
    floor = max(statistics.median(rms_all) * 0.55, 120.0)
    voiced = [(t, f) for t, r, f in frames if r > floor]

    # Cap work: sample up to 150 voiced frames evenly.
    if len(voiced) > 150:
        step = len(voiced) / 150
        voiced = [voiced[int(i * step)] for i in range(150)]

    pitches = [p for _, f in voiced for p in [_f0(f)] if p and 70 <= p <= 400]
    zcrs = [_zcr(f) for _, f in voiced] or [0.0]
    voiced_ratio = len(voiced) / max(1, len(frames))

    # Energy-envelope peaks as a syllable-rate proxy (20 ms hops, smoothed).
    env = []
    for start in range(0, total - 160, 160):
        env.append(_rms(samples[start:start + 160]))
    smooth = [statistics.mean(env[max(0, i - 2):i + 3]) for i in range(len(env))]
    lo = max(statistics.median(smooth) * 1.15, 100.0)
    peaks, last = 0, -99
    for i in range(1, len(smooth) - 1):
        if smooth[i] > lo and smooth[i] >= smooth[i - 1] and smooth[i] > smooth[i + 1] and i - last > 4:
            peaks += 1
            last = i
    duration = total / RATE
    syl_rate = peaks / max(duration, 0.1)

    peak_db = 20 * math.log10(max(statistics.median([r for r in rms_all if r > floor] or [1]), 1) / 32768)
    f0_med = statistics.median(pitches) if pitches else 0.0
    zcr = statistics.median(zcrs)
    brightness = min(1.0, max(0.0, (zcr - 0.02) / 0.22))

    return {
        "valid": bool(voiced),
        "method": "acoustic-approximation (NOT voice cloning)",
        "duration_sec": round(duration, 2),
        "f0_median_hz": round(f0_med, 1),
        "f0_iqr_hz": [round(statistics.quantiles(pitches, n=4)[0], 1),
                      round(statistics.quantiles(pitches, n=4)[2], 1)] if len(pitches) >= 4 else None,
        "speech_rate_syl_per_sec": round(syl_rate, 2),
        "brightness": round(brightness, 2),
        "brightness_zcr": round(zcr, 4),
        "median_level_dbfs": round(peak_db, 1),
        "voiced_ratio": round(voiced_ratio, 3),
        "pause_ratio": round(1 - voiced_ratio, 3),
        "pitch_frames_used": len(pitches),
    }


def map_voice(profile: dict) -> dict:
    f0 = profile.get("f0_median_hz") or 190.0
    bright = profile.get("brightness", 0.5)
    rate_syl = profile.get("speech_rate_syl_per_sec") or 4.0
    scored = []
    for vid, vf0, vb, label in VOICE_TABLE:
        # Register mismatch dominates; brightness is a tie-breaker.
        score = abs(f0 - vf0) / 55.0 + abs(bright - vb) * 1.0
        # Prefer standard Mandarin unless the user explicitly overrides the voice.
        if not vid.startswith("zh-CN-") or "liaoning" in vid or "shaanxi" in vid:
            score += 0.9
        scored.append((score, vid, vf0, label))
    scored.sort()
    best = scored[0]
    rate_pct = int(max(-25, min(25, (rate_syl - 4.0) * 9)))
    pitch_hz = int(max(-40, min(40, (f0 - best[2]) * 0.35)))
    return {
        "selected": best[1],
        "label": best[3],
        "rate": f"{rate_pct:+d}%",
        "pitch": f"{pitch_hz:+d}Hz",
        "volume": "+0%",
        "candidates": [{"voice": v, "label": lb, "score": round(s, 2)} for s, v, _, lb in scored[:5]],
        "note": "Closest neural voice by pitch/brightness/rate. Override in edl.json -> voice.voice_id.",
    }


def profile_video(cfg, video: Path, work_dir: Path) -> dict:
    work_dir.mkdir(parents=True, exist_ok=True)
    wav = work_dir / "voice_track.wav"
    extract_voice_track(cfg, video, wav)
    prof = analyse_wav(wav)
    prof["source_track"] = str(wav)
    prof["match"] = map_voice(prof) if prof.get("valid") else map_voice({})
    return prof


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--out", required=True, help="output JSON path (must be on D:)")
    args = ap.parse_args()
    cfg = json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8"))
    work = Path(args.out).parent / "_voice_work"
    result = profile_video(cfg, Path(args.video), work)
    write_json(args.out, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (PipelineError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
