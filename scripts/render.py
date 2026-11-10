#!/usr/bin/env python3
"""Render the final video: cut clips -> concat -> mix narration/BGM -> burn ASS."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    PipelineError, emit, ffmpeg, load_config, mark_stage, peak_db, read_json,
    require_stage, run, write_json,
)
from edl import normalize, timeline, validate  # noqa: E402


def cut_clips(cfg, edl, tl, build: Path) -> list[Path]:
    w, h = int(edl["target"]["width"]), int(edl["target"]["height"])
    fps = float(edl["target"]["fps"])
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}")
    r = cfg["render"]
    files = []
    for c in tl["clips"]:
        out = build / f"clip_{c['id']}.mp4"
        dur = float(c["out_duration"])
        ffmpeg(cfg, [
            "-ss", f"{float(c['src_start']):.3f}", "-t", f"{dur:.3f}",
            "-i", edl["source"], "-an", "-vf", vf,
            "-c:v", r["vcodec"], "-preset", r["preset"], "-crf", str(r["crf"]),
            "-pix_fmt", "yuv420p", "-r", str(fps), str(out),
        ], capture=True, check=True)
        if out.exists() and out.stat().st_size > 1024:
            files.append(out)
    if not files:
        raise PipelineError("no clips were rendered")
    return files


def concat_clips(cfg, files: list[Path], build: Path) -> Path:
    lst = build / "concat_video.txt"
    lst.write_text("\n".join(f"file '{f.as_posix()}'" for f in files) + "\n", encoding="utf-8")
    out = build / "video_mute.mp4"
    ffmpeg(cfg, ["-f", "concat", "-safe", "0", "-i", lst.name, "-c", "copy", str(out)],
           cwd=build, capture=True, check=True)
    return out


def render_final(cfg, edl, tl, timing, paths) -> dict:
    build = paths["project"] / "build"
    subdir = paths["project"] / "subtitles"
    output = paths["project"] / "output"
    ass = subdir / "main.ass"
    if not ass.exists():
        raise PipelineError("run subtitle stage first")
    voice = Path(timing["wav"])
    if not voice.exists():
        raise PipelineError(f"voice track missing: {voice}")
    peak = peak_db(cfg, voice)
    if timing.get("engine") != "silent" and peak is not None and peak < -45:
        raise PipelineError(
            f"voice.wav is near-silent (peak {peak} dB). The TTS stage probably did not finish "
            "or an old placeholder was reused. Re-run the voice stage, then render again."
        )

    clips = cut_clips(cfg, edl, tl, build)
    muted = concat_clips(cfg, clips, build)

    vdur = float(tl["video_duration"])
    adur = float(timing["total_duration"])
    total = max(vdur, adur) + 0.05
    pad = max(0.0, adur - vdur + 0.1)
    vf_parts = []
    if pad > 0.05:
        vf_parts.append(f"tpad=stop_mode=clone:stop_duration={pad:.3f}")
    vf_parts.append("ass=main.ass")
    vf = ",".join(vf_parts)
    r = cfg["render"]
    final = output / "final.mp4"
    bgm = (edl.get("bgm") or {}).get("path")
    warnings = []
    base = ["-i", str(muted), "-i", str(voice)]

    if bgm:
        base += ["-stream_loop", "-1", "-i", str(bgm)]
        vol = float((edl["bgm"] or {}).get("volume_db", cfg["bgm_volume_db"]))
        fc = (
            f"[0:v]{vf}[v];"
            "[1:a]aresample=48000,aformat=channel_layouts=mono,asplit=2[vo][sc];"
            f"[2:a]aresample=48000,volume={vol}dB[bg];"
            "[bg][sc]sidechaincompress=threshold=0.02:ratio=6:attack=20:release=400[bgd];"
            "[vo][bgd]amix=inputs=2:duration=first:dropout_transition=0[a]"
        )
        cmd = [*base, "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
               "-c:v", r["vcodec"], "-preset", r["preset"], "-crf", str(r["crf"]),
               "-pix_fmt", "yuv420p", "-c:a", r["acodec"], "-b:a", r["abitrate"],
               "-t", f"{total:.3f}", str(final)]
    else:
        cmd = [*base, "-map", "0:v", "-map", "1:a", "-vf", vf,
               "-af", "apad", "-c:v", r["vcodec"], "-preset", r["preset"], "-crf", str(r["crf"]),
               "-pix_fmt", "yuv420p", "-c:a", r["acodec"], "-b:a", r["abitrate"],
               "-t", f"{total:.3f}", str(final)]

    res = ffmpeg(cfg, cmd, cwd=subdir, capture=True, check=False)
    if res.returncode != 0 and bgm:
        warnings.append("BGM sidechain mix failed; retried without BGM. " + (res.stderr or "")[-300:])
        cmd = ["-i", str(muted), "-i", str(voice), "-map", "0:v", "-map", "1:a",
               "-vf", vf, "-af", "apad", "-c:v", r["vcodec"], "-preset", r["preset"],
               "-crf", str(r["crf"]), "-pix_fmt", "yuv420p", "-c:a", r["acodec"],
               "-b:a", r["abitrate"], "-t", f"{total:.3f}", str(final)]
        res = ffmpeg(cfg, cmd, cwd=subdir, capture=True, check=False)
    if res.returncode != 0 or not final.exists():
        raise PipelineError("final render failed:\n" + (res.stderr or "")[-2500:])

    return {"final": str(final), "clips": len(clips), "pad_sec": round(pad, 2),
            "target_duration": round(total, 2), "bgm": bgm, "warnings": warnings}


def write_report(paths, edl, tl, timing, result, cfg) -> Path:
    ref = edl.get("reference", {})
    lines = [
        "# 剪辑成片报告", "",
        f"- 成片：`{result['final']}`",
        f"- 时长：{result['target_duration']}s（视频 {tl['video_duration']}s / 配音 {timing['total_duration']}s）",
        f"- 镜头数：{result['clips']}，案例镜头中位数 {ref.get('shot_median_sec', 'n/a')}s",
        f"- 配音：{timing.get('engine')} / {timing.get('params', {}).get('voice_id')} "
        f"({timing.get('params', {}).get('rate')}, {timing.get('params', {}).get('pitch')})",
        f"- 字幕：{len(timing.get('segments', []))} 条，字体 {edl['subtitle']['font']} "
        f"{edl['subtitle']['fontsize']}px",
        f"- 画幅：{edl['target']['width']}x{edl['target']['height']} @ {edl['target']['fps']}fps",
        "", "## 对齐提示", "", f"- {tl['advice']}",
    ]
    if result["pad_sec"] > 0.1:
        lines.append(f"- 末尾定格补齐 {result['pad_sec']}s（配音长于画面）。")
    for w in timing.get("warnings", []) + result["warnings"]:
        lines.append(f"- ⚠️ {w}")
    lines += ["", "## 复跑", "",
              "修改 `edl.json` 后依次重跑 `voice` → `subtitle` → `render`；"
              "仅改画面时重跑 `render` 即可（中间产物全在 D 盘 `build/` 下，可复用）。", ""]
    p = paths["project"] / "output" / "report.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--edl", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    from common import ensure_tree
    paths = ensure_tree(cfg, args.project)
    require_stage(paths, "render")
    edl = normalize(cfg, read_json(args.edl))
    errs = validate(cfg, edl, require_lines=True)
    if errs:
        raise PipelineError("invalid EDL:\n  - " + "\n  - ".join(errs))
    timing = read_json(paths["project"] / "voice" / "voice_timing.json")
    tl = timeline(edl, timing)
    write_json(paths["project"] / "build" / "timeline.json", tl)
    result = render_final(cfg, edl, tl, timing, paths)
    report = write_report(paths, edl, tl, timing, result, cfg)
    result["report"] = str(report)
    result["timeline"] = str(paths["project"] / "build" / "timeline.json")
    write_json(paths["project"] / "output" / "render_meta.json", result)
    mark_stage(paths, "render", result["final"])
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        emit("render", result)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (PipelineError, OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
