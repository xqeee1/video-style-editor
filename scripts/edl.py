#!/usr/bin/env python3
"""EDL (edit decision list): template generation, validation and timeline math."""
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PipelineError, emit, load_config, read_json, root, write_json  # noqa: E402


def scaled_subtitle_style(cfg, meta: dict) -> dict:
    """Scale the 1080x1920 baseline style to the reference video's framing."""
    st = copy.deepcopy(cfg["default_subtitle"])
    w, h = meta.get("width") or 1080, meta.get("height") or 1920
    scale = max(0.30, min(2.0, math.sqrt((w * h) / (1080 * 1920))))
    st["fontsize"] = max(12, int(round(st["fontsize"] * scale)))
    # Vertical offset should stay a constant *fraction* of frame height.
    st["margin_v"] = max(12, int(round(st["margin_v"] * (h / 1920))))
    for key in ("outline", "shadow"):
        st[key] = round(float(st[key]) * scale, 2)
    st["_scale_from_baseline"] = round(scale, 3)
    return st


def default_edl(cfg, project: str, source: str, reference_summary: dict,
                reference_meta: dict | None = None) -> dict:
    meta = reference_meta or {}
    return {
        "version": 1,
        "project": project,
        "source": str(Path(source).resolve()),
        "reference": {
            "shot_median_sec": reference_summary.get("shot_median_sec"),
            "duration_sec": reference_summary.get("duration_sec"),
        },
        "target": {
            "width": meta.get("width") or cfg["render"]["width"],
            "height": meta.get("height") or cfg["render"]["height"],
            "fps": meta.get("fps") or cfg["render"]["fps"],
            "inherited_from_reference": bool(meta.get("width")),
            "note": "Defaults to the reference video's framing; override to force a different output size.",
        },
        "clips": [],
        "lines": [],
        "voice": {"engine": "edge-tts", "voice_id": None, "rate": None,
                  "pitch": None, "note": "null = auto-match the reference voice profile"},
        "bgm": {"path": None, "volume_db": cfg["bgm_volume_db"], "ducking": cfg["ducking"]},
        "subtitle": scaled_subtitle_style(cfg, meta),
        "notes": "Agent fills: script.md narration -> lines[].text; clips chosen to match reference rhythm. "
                 "subtitle values are already scaled to the reference framing - edit them as final pixels.",
    }


def auto_clips(reference_shots: dict, source_shots: dict, source_duration: float,
               target_duration: float | None = None) -> list[dict]:
    """Rhythm-matched rough cut: replay the reference shot-length pattern on source shots."""
    ref_durs = [d for d in reference_shots.get("durations", []) if d > 0.2]
    if not ref_durs:
        ref_durs = [max(1.0, reference_shots.get("median_sec") or 2.0)]
    # Recover source cut times from its duration list.
    t = 0.0
    src_bounds = [0.0]
    for d in source_shots.get("durations", []):
        t += d
        src_bounds.append(round(min(t, source_duration), 3))
    if len(src_bounds) < 2:
        src_bounds = [0.0, source_duration]

    total = target_duration or reference_shots.get("total_sec") or sum(ref_durs)
    clips, cursor, i = [], 0.0, 0
    while cursor < total - 0.15 and i < 120:
        want = ref_durs[i % len(ref_durs)]
        # Find a source boundary near the ideal in-point; avoid reusing the exact same spot.
        ideal = (cursor * max(1.0, source_duration / max(total, 0.1))) % max(source_duration - want, 0.1)
        start = min(src_bounds, key=lambda b: abs(b - ideal))
        start = max(0.0, min(start, max(source_duration - 0.3, 0.0)))
        end = min(start + want, source_duration)
        if end - start < 0.25:
            break
        clips.append({
            "id": f"c{len(clips)+1:03d}",
            "src_start": round(start, 3),
            "src_end": round(end, 3),
            "transition": "cut",
            "note": f"rhythm slot {i+1}, target {want:.2f}s",
        })
        cursor += end - start
        i += 1
    return clips


def normalize(cfg, edl: dict) -> dict:
    e = copy.deepcopy(edl)
    render = cfg["render"]
    e.setdefault("target", {})
    for k in ("width", "height", "fps"):
        e["target"].setdefault(k, render[k])
    e.setdefault("voice", {})
    e["voice"].setdefault("engine", "edge-tts")
    e.setdefault("bgm", {"path": None, "volume_db": cfg["bgm_volume_db"], "ducking": cfg["ducking"]})
    sub = copy.deepcopy(cfg["default_subtitle"])
    sub.update(e.get("subtitle") or {})
    e["subtitle"] = sub
    e.setdefault("clips", [])
    e.setdefault("lines", [])
    return e


def validate(cfg, edl: dict, *, require_lines: bool = False) -> list[str]:
    errs = []
    if edl.get("version") != 1:
        errs.append("version must be 1")
    src = edl.get("source")
    if not src or not Path(src).exists():
        errs.append(f"source video missing: {src}")
    clips = edl.get("clips") or []
    if not clips:
        errs.append("clips is empty")
    for i, c in enumerate(clips):
        if not c.get("id"):
            errs.append(f"clip[{i}] missing id")
        a, b = c.get("src_start"), c.get("src_end")
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or b <= a:
            errs.append(f"clip[{i}] invalid src_start/src_end")
        elif b - a > 60:
            errs.append(f"clip[{i}] longer than 60s - split it")
    lines = edl.get("lines") or []
    if require_lines and not lines:
        errs.append("lines is empty - write the narration script first")
    for i, l in enumerate(lines):
        if not str(l.get("text", "")).strip():
            errs.append(f"line[{i}] empty text")
    bgm = (edl.get("bgm") or {}).get("path")
    if bgm and not Path(bgm).exists():
        errs.append(f"bgm file missing: {bgm}")
    t = edl.get("target") or {}
    if int(t.get("width", 0)) <= 0 or int(t.get("height", 0)) <= 0 or float(t.get("fps", 0)) <= 0:
        errs.append("target width/height/fps invalid")
    return errs


def timeline(edl: dict, voice_timing: dict) -> dict:
    clips = []
    cursor = 0.0
    for c in edl["clips"]:
        dur = float(c["src_end"]) - float(c["src_start"])
        clips.append({**c, "out_start": round(cursor, 3), "out_end": round(cursor + dur, 3),
                      "out_duration": round(dur, 3)})
        cursor += dur

    segs = voice_timing.get("segments", [])
    lines = edl.get("lines") or []
    if segs and lines and len(segs) != len(lines):
        raise PipelineError(f"voice segments ({len(segs)}) != EDL lines ({len(lines)}); re-run voice stage after editing lines")
    voiced = []
    for i, l in enumerate(lines):
        s = segs[i] if segs else {"start": 0.0, "duration": 0.0}
        voiced.append({**l, "id": l.get("id", f"l{i+1:03d}"),
                       "start": round(float(s["start"]), 3),
                       "end": round(float(s["start"]) + float(s["duration"]), 3),
                       "duration": round(float(s["duration"]), 3)})
    vdur = round(cursor, 3)
    adur = round(float(voice_timing.get("total_duration", voiced[-1]["end"] if voiced else 0)), 3)
    delta = round(adur - vdur, 3)
    advice = "aligned"
    if delta > 0.5:
        advice = "voice longer than video: last frame will be held (tpad clone). Shorten script or add clips."
    elif delta < -0.5:
        advice = "video longer than voice: tail will be truncated. Add narration or drop clips."
    return {"clips": clips, "lines": voiced, "video_duration": vdur,
            "voice_duration": adur, "delta_sec": delta, "advice": advice}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("template")
    p.add_argument("--project", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("validate")
    p.add_argument("--edl", required=True)
    p.add_argument("--require-lines", action="store_true")
    p = sub.add_parser("timeline")
    p.add_argument("--edl", required=True)
    p.add_argument("--voice-timing", required=True)
    p.add_argument("--out", required=True)
    args = ap.parse_args()

    cfg = load_config()
    if args.cmd == "template":
        from common import ensure_tree
        paths = ensure_tree(cfg, args.project)
        state = read_json(paths["project_json"])
        ref = state.get("analysis", {}).get("reference", {})
        src = state.get("inputs", {}).get("source")
        ref_meta = read_json(paths["project"] / "reference" / "meta.json", {})
        ref_shots = read_json(paths["project"] / "reference" / "shots.json", {})
        src_shots = read_json(paths["project"] / "source" / "shots.json", {})
        src_dur = read_json(paths["project"] / "source" / "meta.json", {}).get("duration", 0)
        edl = default_edl(cfg, args.project, src, ref, ref_meta)
        edl["clips"] = auto_clips(ref_shots, src_shots, src_dur)
        write_json(args.out, edl)
        emit("template", {"out": args.out, "clip_count": len(edl["clips"]),
                          "rough_duration": round(sum(c["src_end"] - c["src_start"] for c in edl["clips"]), 2)})
    elif args.cmd == "validate":
        e = normalize(cfg, read_json(args.edl))
        errs = validate(cfg, e, require_lines=args.require_lines)
        emit("validate", {"ok": not errs, "errors": errs, "clips": len(e["clips"]), "lines": len(e["lines"])})
        return 0 if not errs else 2
    else:
        e = normalize(cfg, read_json(args.edl))
        tl = timeline(e, read_json(args.voice_timing))
        write_json(args.out, tl)
        emit("timeline", {k: v for k, v in tl.items() if k not in ("clips", "lines")})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (PipelineError, OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
