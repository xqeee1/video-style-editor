#!/usr/bin/env python3
"""Measure a reference or raw video: cuts, rhythm, silence, loudness, frames, voice."""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    PipelineError, emit, ffmpeg, load_config, mark_stage, media_meta, require_stage,
    root, write_json,
)
from voice_profile import profile_video  # noqa: E402

SHOWINFO = re.compile(r"n:\s*\d+.*?pts_time:([\d.]+)")
SIL_START = re.compile(r"silence_start:\s*([\d.]+)")
SIL_END = re.compile(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)")
VOLUME = re.compile(r"(mean_volume|max_volume):\s*([-\d.]+)\s*dB")


def detect_cuts(cfg, path: Path) -> dict:
    prob = float(cfg.get("scene_probability", 0.28))
    p = ffmpeg(cfg, ["-i", str(path), "-vf", f"select='gt(scene,{prob})',showinfo",
                     "-an", "-f", "null", "-"], capture=True, check=True)
    times = sorted({round(float(t), 3) for t in SHOWINFO.findall(p.stderr or "")})
    return {"threshold": prob, "cuts": times}


def detect_audio(cfg, path: Path) -> dict:
    noise = cfg.get("silence_noise_db", -35)
    mind = cfg.get("silence_min_duration", 0.25)
    p = ffmpeg(cfg, ["-i", str(path), "-af", f"silencedetect=noise={noise}dB:d={mind}",
                     "-vn", "-f", "null", "-"], capture=True, check=True)
    err = p.stderr or ""
    starts = [float(x) for x in SIL_START.findall(err)]
    ends = [(float(a), float(b)) for a, b in SIL_END.findall(err)]
    silences = [{"start": s, "end": ends[i][0] if i < len(ends) else s,
                 "duration": ends[i][1] if i < len(ends) else 0.0} for i, s in enumerate(starts)]
    v = ffmpeg(cfg, ["-i", str(path), "-af", "volumedetect", "-vn", "-f", "null", "-"],
               capture=True, check=False)
    vols = {k: float(x) for k, x in VOLUME.findall(v.stderr or "")}
    return {"silences": silences, "silence_count": len(silences), **vols}


def make_contact_sheet(cfg, path: Path, out_dir: Path, prefix: str) -> list[str]:
    cols = int(cfg.get("contact_sheet_cols", 4))
    rows = int(cfg.get("contact_sheet_rows", 4))
    interval = float(cfg.get("contact_sheet_interval_sec", 2.0))
    pattern = out_dir / f"{prefix}_sheet_%02d.jpg"
    vf = f"fps=1/{interval},scale=320:-2,tile={cols}x{rows}"
    ffmpeg(cfg, ["-i", str(path), "-vf", vf, "-frames:v", "4", "-q:v", "4", str(pattern)],
           capture=True, check=False)
    return [str(p) for p in sorted(out_dir.glob(f"{prefix}_sheet_*.jpg"))]


def make_keyframes(cfg, path: Path, out_dir: Path, cuts: list[float], duration: float, limit: int = 24) -> list[str]:
    bounds = [0.0] + cuts + [duration]
    mids = [(bounds[i] + bounds[i + 1]) / 2 for i in range(len(bounds) - 1)]
    mids = [m for m in mids if 0 < m < max(duration - 0.05, 0.1)]
    if not mids:
        mids = [duration / 2] if duration > 0 else []
    if len(mids) > limit:
        step = len(mids) / limit
        mids = [mids[int(i * step)] for i in range(limit)]
    files = []
    for i, t in enumerate(mids, 1):
        out = out_dir / f"shot_{i:03d}_{t:.2f}.jpg"
        ffmpeg(cfg, ["-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", "-q:v", "3", str(out)],
               capture=True, check=False)
        if out.exists():
            files.append(str(out))
    return files


def shot_stats(cuts: list[float], duration: float) -> dict:
    bounds = [0.0] + [c for c in cuts if 0 < c < duration] + [duration]
    bounds = sorted(set(round(b, 3) for b in bounds))
    durs = [round(bounds[i + 1] - bounds[i], 3) for i in range(len(bounds) - 1)]
    durs = [d for d in durs if d > 0.05]
    if not durs:
        return {"count": 0, "median_sec": 0, "durations": []}
    sd = sorted(durs)
    q = lambda p: round(sd[min(len(sd) - 1, int(p * len(sd)))], 2)  # noqa: E731
    med = statistics.median(durs)
    fast = [i for i, d in enumerate(durs) if d < med * 0.6]
    return {
        "count": len(durs),
        "median_sec": round(med, 2),
        "p10_sec": q(0.10), "p90_sec": q(0.90),
        "min_sec": round(min(durs), 2), "max_sec": round(max(durs), 2),
        "fast_shot_indices": fast[:30],
        "fast_shot_ratio": round(len(fast) / len(durs), 3),
        "total_sec": round(duration, 2),
        "durations": durs[:200],
    }


def analyse(cfg, paths, role: str, video: Path) -> dict:
    if not video.exists():
        raise PipelineError(f"video not found: {video}")
    outdir = paths["project"] / role
    outdir.mkdir(parents=True, exist_ok=True)
    meta = media_meta(cfg, video)
    write_json(outdir / "meta.json", meta)

    cuts = detect_cuts(cfg, video)
    stats = shot_stats(cuts["cuts"], meta["duration"])
    stats["threshold"] = cuts["threshold"]
    write_json(outdir / "shots.json", stats)

    audio = detect_audio(cfg, video) if meta["has_audio"] else {"silences": [], "note": "no audio stream"}
    write_json(outdir / "audio.json", audio)

    sheets = make_contact_sheet(cfg, video, outdir, "contact")
    keys = make_keyframes(cfg, video, outdir / "keyframes", cuts["cuts"], meta["duration"])

    summary = {
        "role": role,
        "video": meta["path"],
        "duration_sec": meta["duration"],
        "resolution": f"{meta.get('width', 0)}x{meta.get('height', 0)}",
        "fps": meta.get("fps"),
        "has_audio": meta["has_audio"],
        "shots": {k: v for k, v in stats.items() if k != "durations"},
        "audio": {k: v for k, v in audio.items() if k != "silences"},
        "silence_sample": audio.get("silences", [])[:8],
        "contact_sheets": sheets,
        "keyframes": keys,
        "artifacts": {"meta": str(outdir / "meta.json"),
                      "shots": str(outdir / "shots.json"),
                      "audio": str(outdir / "audio.json")},
    }

    if role == "reference":
        prof = profile_video(cfg, video, outdir / "_voice_work")
        write_json(outdir / "voice_profile.json", prof)
        summary["voice_profile"] = prof
        summary["artifacts"]["voice_profile"] = str(outdir / "voice_profile.json")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="project slug under ROOT/projects")
    ap.add_argument("--role", required=True, choices=["reference", "source"])
    ap.add_argument("--input", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    from common import ensure_tree, project_state
    paths = ensure_tree(cfg, args.project)
    state = project_state(paths)
    require_stage(paths, "analyze")
    result = analyse(cfg, paths, args.role, Path(args.input))
    state.setdefault("analysis", {})[args.role] = {
        "video": result["video"], "duration_sec": result["duration_sec"],
        "shot_median_sec": result["shots"]["median_sec"],
    }
    from common import write_json as wj
    wj(paths["project_json"], state)
    if args.role == "source":
        mark_stage(paths, "analyze", "reference + source measured")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        emit(f"analyze/{args.role}", result)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (PipelineError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
