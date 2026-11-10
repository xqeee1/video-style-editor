#!/usr/bin/env python3
"""Generate ASS (burn-in) + SRT subtitles from EDL lines and real TTS timings."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import emit, load_config, read_json, write_json  # noqa: E402
from edl import normalize  # noqa: E402

ASS_FORMAT = ("Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
              "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
              "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding")
EVENT_FORMAT = "Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
PUNCT = "，。！？；：、,.!?;: "


def split_line(text: str, max_chars: int, max_lines: int = 2) -> str:
    text = re.sub(r"\s+", " ", str(text)).strip()
    if len(text) <= max_chars:
        return text.replace("{", "(").replace("}", ")")
    per = max_chars
    chunks, rest = [], text
    while rest and len(chunks) < max_lines - 1:
        if len(rest) <= per:
            break
        cut = -1
        for i in range(per, max(1, per - 6), -1):
            if rest[i - 1] in PUNCT:
                cut = i
                break
        cut = cut if cut > 0 else per
        chunks.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        chunks.append(rest[:per])
    out = "\\N".join(chunks)
    return out.replace("{", "(").replace("}", ")")


def ass_time(t: float) -> str:
    t = max(0.0, float(t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def srt_time(t: float) -> str:
    t = max(0.0, float(t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", ",")


def build_ass(edl: dict, timing: dict) -> str:
    st = edl["subtitle"]
    w, h = int(edl["target"]["width"]), int(edl["target"]["height"])
    style = (
        f"Style: Default,{st['font']},{int(st['fontsize'])},{st['primary_color']},"
        f"&H000000FF,{st['outline_color']},&H80000000,{int(st['bold'])},0,0,0,100,100,0,0,"
        f"1,{float(st['outline'])},{float(st['shadow'])},{int(st['alignment'])},"
        f"10,10,{int(st['margin_v'])},1"
    )
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]", f"Format: {ASS_FORMAT}", style, "",
        "[Events]", f"Format: {EVENT_FORMAT}",
    ]
    # Never let a line exceed the frame: CJK glyphs are roughly 1em wide.
    fit_chars = max(6, int(w * 0.90 / max(1, int(st["fontsize"]))))
    max_chars = min(int(st.get("max_chars_per_line", 18)), fit_chars)
    events = []
    for s in timing["segments"]:
        txt = split_line(s["text"], max_chars)
        events.append(f"Dialogue: 0,{ass_time(s['start'])},{ass_time(s['start'] + s['duration'])},"
                      f"Default,,0,0,0,,{txt}")
    return "\n".join(head + events) + "\n", max_chars


def build_srt(timing: dict) -> str:
    blocks = []
    for i, s in enumerate(timing["segments"], 1):
        txt = str(s["text"]).replace("\\N", "\n")
        blocks.append(f"{i}\n{srt_time(s['start'])} --> {srt_time(s['start'] + s['duration'])}\n{txt}\n")
    return "\n".join(blocks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edl", required=True)
    ap.add_argument("--timing", required=True, help="voice_timing.json or timeline.json")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    edl = normalize(cfg, read_json(args.edl))
    timing = read_json(args.timing)
    segs = timing.get("segments") or timing.get("lines")
    if not segs:
        raise SystemExit("ERROR: timing has no segments")
    timing = {**timing, "segments": segs}
    outdir = Path(args.out_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    ass, srt = outdir / "main.ass", outdir / "main.srt"
    ass_text, max_chars = build_ass(edl, timing)
    ass.write_text(ass_text, encoding="utf-8")
    srt.write_text(build_srt(timing), encoding="utf-8")
    write_json(outdir / "subtitle_meta.json", {
        "ass": str(ass), "srt": str(srt), "line_count": len(segs),
        "style": edl["subtitle"], "effective_max_chars": max_chars,
        "note": "burn in with: -vf ass=main.ass (run ffmpeg with cwd=this dir)",
    })
    result = {"ass": str(ass), "srt": str(srt), "line_count": len(segs),
              "first_cue": f"{segs[0]['start']}s -> {segs[0]['start'] + segs[0]['duration']}s"}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        emit("subtitle", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
