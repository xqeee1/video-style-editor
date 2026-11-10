#!/usr/bin/env python3
"""Narration synthesis. Matches the reference voice profile, then degrades gracefully.

Engines: edge-tts (default, neural), sapi (offline Windows COM), clone (optional
external voice-cloning model), file (user-supplied narration), silent (last resort).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    PipelineError, emit, ffmpeg, load_config, read_json, root, run, venv_python, write_json,
)


def ensure_edge_runtime(cfg) -> bool:
    """Run inside the D: venv when edge_tts is missing from this interpreter.

    IMPORTANT (Windows): os.execv spawns a child and exits immediately, which
    makes the caller believe TTS finished while voice.wav is still being written.
    Always use a blocking subprocess here so the pipeline cannot render a stale
    (silent) track.
    """
    try:
        import edge_tts  # noqa: F401
        return True
    except ImportError:
        pass
    py = venv_python(cfg)
    if py.exists() and Path(sys.executable).resolve() != py.resolve():
        child = subprocess.run(
            [str(py), str(Path(__file__).resolve()), *sys.argv[1:]],
            env=os.environ.copy(), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=1200,
        )
        if child.stdout:
            print(child.stdout, end="" if child.stdout.endswith("\n") else "\n")
        if child.stderr:
            print(child.stderr, end="" if child.stderr.endswith("\n") else "\n", file=sys.stderr)
        sys.exit(child.returncode)
    return False


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return round(w.getnframes() / float(w.getframerate()), 3)


def to_wav(cfg, src: Path, dst: Path) -> Path:
    ffmpeg(cfg, ["-i", str(src), "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(dst)],
           capture=True, check=True)
    return dst


def silence_wav(cfg, path: Path, seconds: float) -> Path:
    ffmpeg(cfg, ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=mono", "-t", f"{seconds:.3f}",
                 "-c:a", "pcm_s16le", str(path)], capture=True, check=True)
    return path


def concat_wavs(cfg, files: list[Path], out: Path) -> Path:
    lst = out.parent / "concat_voice.txt"
    lst.write_text("\n".join(f"file '{f.as_posix()}'" for f in files) + "\n", encoding="utf-8")
    ffmpeg(cfg, ["-f", "concat", "-safe", "0", "-i", str(lst.name), "-c:a", "pcm_s16le", str(out)],
           cwd=out.parent, capture=True, check=True)
    return out


async def _edge_synth(text, voice, rate, pitch, volume, out):
    import edge_tts
    kwargs = {"rate": rate, "volume": volume}
    try:
        comm = edge_tts.Communicate(text, voice, pitch=pitch, **kwargs)
    except TypeError:  # older edge-tts without pitch
        comm = edge_tts.Communicate(text, voice, **kwargs)
    await comm.save(str(out))


def synth_edge(cfg, lines, params, voice_dir: Path) -> list[Path]:
    voice = params["voice_id"]
    rate = params.get("rate") or "+0%"
    pitch = params.get("pitch") or "+0Hz"
    volume = params.get("volume") or "+0%"
    outs = []
    for i, l in enumerate(lines, 1):
        mp3 = voice_dir / f"line_{i:03d}.mp3"
        wav = voice_dir / f"line_{i:03d}.wav"
        try:
            asyncio.run(asyncio.wait_for(
                _edge_synth(str(l["text"]), voice, rate, pitch, volume, mp3), timeout=90))
            if not mp3.exists() or mp3.stat().st_size < 512:
                raise PipelineError("edge-tts produced an empty file")
            to_wav(cfg, mp3, wav)
            outs.append(wav)
        except Exception as e:  # noqa: BLE001 - degrade per line, do not lose the whole run
            raise PipelineError(f"edge-tts failed on line {i}: {e}") from e
    return outs


def synth_sapi(cfg, lines, voice_dir: Path) -> list[Path]:
    """Offline fallback via Windows SAPI COM (no Add-Type, no downloads)."""
    full = " ".join(str(l["text"]) for l in lines)
    out = voice_dir / "sapi_all.wav"
    ps = (
        "$v = New-Object -ComObject SAPI.SpVoice; "
        "$f = New-Object -ComObject SAPI.SpFileStream; "
        f"$f.Open('{out.as_posix()}', 3); "
        "$v.AudioOutputStream = $f; "
        f"$v.Speak('{full.replace(chr(39), chr(39)*2)}'); "
        "$f.Close();"
    )
    p = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
            cfg=cfg, capture=True, check=False, timeout=120)
    if not out.exists() or out.stat().st_size < 1024:
        raise PipelineError(f"SAPI fallback failed: {(p.stderr or '')[-400:]}")
    return [to_wav(cfg, out, voice_dir / "voice_sapi.wav")]


def synth_clone(cfg, lines, edl, voice_dir: Path, ref_wav: Path) -> list[Path]:
    engine = root(cfg) / "models" / "clone_engine.py"
    if not engine.exists():
        raise PipelineError(
            f"voice cloning requested but {engine} is missing. See references/voice-clone.md "
            "for the CosyVoice / GPT-SoVITS setup and the required interface."
        )
    texts = voice_dir / "texts.json"
    write_json(texts, [str(l["text"]) for l in lines])
    run([sys.executable, str(engine), "--reference-wav", str(ref_wav),
         "--texts", str(texts), "--out-dir", str(voice_dir)], cfg=cfg, capture=True, check=True)
    outs = [voice_dir / f"line_{i:03d}.wav" for i in range(1, len(lines) + 1)]
    if not all(p.exists() for p in outs):
        raise PipelineError("clone engine did not produce line_001.wav ... line_NNN.wav")
    return outs


def synth_silent(cfg, lines, voice_dir: Path) -> list[Path]:
    outs = []
    for i, l in enumerate(lines, 1):
        dur = max(0.8, min(8.0, len(str(l["text"])) * 0.28))
        outs.append(silence_wav(cfg, voice_dir / f"line_{i:03d}.wav", dur))
    return outs


def build_timing(lines, wavs: list[Path], gap: float, engine: str, params: dict,
                 combined: Path) -> dict:
    segs, cursor = [], 0.0
    for i, (l, w) in enumerate(zip(lines, wavs), 1):
        d = wav_duration(w)
        segs.append({"index": i - 1, "id": l.get("id", f"l{i:03d}"), "text": str(l["text"]),
                     "start": round(cursor, 3), "duration": round(d, 3), "file": str(w)})
        cursor += d + gap
    return {"engine": engine, "params": params, "gap_ms": round(gap * 1000),
            "segments": segs, "total_duration": round(cursor - gap if segs else 0, 3),
            "wav": str(combined)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--edl", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    from common import ensure_tree, mark_stage, require_stage
    paths = ensure_tree(cfg, args.project)
    require_stage(paths, "voice")
    edl = read_json(args.edl)
    lines = edl.get("lines") or []
    if not lines:
        raise PipelineError("edl.json has no lines - write the narration script first")

    voice_dir = paths["project"] / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    prof = read_json(paths["project"] / "reference" / "voice_profile.json", {})
    match = prof.get("match") or {}
    vcfg = edl.get("voice") or {}
    params = {
        "voice_id": vcfg.get("voice_id") or match.get("selected") or cfg["default_voice"],
        "rate": vcfg.get("rate") or match.get("rate") or "+0%",
        "pitch": vcfg.get("pitch") or match.get("pitch") or "+0Hz",
        "volume": vcfg.get("volume") or "+0%",
        "matched_from_reference": not bool(vcfg.get("voice_id")),
    }
    engine = vcfg.get("engine") or "edge-tts"
    gap = float(cfg.get("line_gap_ms", 150)) / 1000.0
    warnings = []

    if engine == "edge-tts" and not ensure_edge_runtime(cfg):
        engine, note = "sapi", "edge_tts unavailable -> offline SAPI fallback"
        warnings.append(note)

    try:
        if engine == "edge-tts":
            wavs = synth_edge(cfg, lines, params, voice_dir)
        elif engine == "clone":
            ref_wav = Path(prof.get("source_track") or (paths["project"] / "reference" / "_voice_work" / "voice_track.wav"))
            wavs = synth_clone(cfg, lines, edl, voice_dir, ref_wav)
        elif engine == "sapi":
            wavs = synth_sapi(cfg, lines, voice_dir)
        elif engine == "file":
            src = Path(vcfg.get("source_audio") or "")
            if not src.exists():
                raise PipelineError(f"voice.source_audio missing: {src}")
            wavs = [to_wav(cfg, src, voice_dir / "voice_file.wav")]
        else:
            wavs = synth_silent(cfg, lines, voice_dir)
    except PipelineError as e:
        warnings.append(str(e))
        engine, wavs = "silent", synth_silent(cfg, lines, voice_dir)
        warnings.append("CRITICAL: narration is silent placeholder; subtitles still render. "
                        "Fix TTS before publishing.")

    # One wav per line is the normal case; single-file engines split by character weight.
    if len(wavs) != len(lines):
        whole = wavs[0]
        total = wav_duration(whole)
        weights = [max(1, len(str(l["text"]))) for l in lines]
        s = sum(weights)
        segs, cursor, pieces = [], 0.0, []
        for i, (l, wt) in enumerate(zip(lines, weights), 1):
            d = total * wt / s
            part = voice_dir / f"line_{i:03d}.wav"
            ffmpeg(cfg, ["-ss", f"{cursor:.3f}", "-t", f"{d:.3f}", "-i", str(whole),
                         "-c:a", "pcm_s16le", str(part)], capture=True, check=True)
            pieces.append(part)
            cursor += d
        wavs = pieces

    # Insert inter-line pauses, then concatenate.
    sequence = []
    for i, w in enumerate(wavs):
        sequence.append(w)
        if gap > 0 and i < len(wavs) - 1:
            sequence.append(silence_wav(cfg, voice_dir / f"gap_{i:03d}.wav", gap))
    combined = concat_wavs(cfg, sequence, voice_dir / "voice.wav")

    timing = build_timing(lines, wavs, gap, engine, params, combined)
    timing["warnings"] = warnings
    write_json(voice_dir / "voice_timing.json", timing)
    mark_stage(paths, "voice", f"{engine} / {params['voice_id']}")
    out = {k: v for k, v in timing.items() if k != "segments"}
    out["segments_preview"] = timing["segments"][:3]
    if args.json:
        print(json.dumps(timing, ensure_ascii=False, indent=2))
    else:
        emit("voice", out)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (PipelineError, OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
