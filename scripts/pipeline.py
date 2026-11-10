#!/usr/bin/env python3
"""Stage orchestrator.

  init     -> register inputs, create D: project tree
  analyze  -> measure reference + source (cuts, rhythm, audio, frames, voice)
  plan     -> write a rhythm-matched EDL template (Agent then fills script.md + lines)
  finish   -> validate the CONFIRMED EDL, then voice -> subtitle -> render
  status   -> show stage progress and artifact paths

Deliberately no "all" command: the narration/EDL confirmation gate is mandatory.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from common import (  # noqa: E402
    PipelineError, emit, ensure_tree, load_config, mark_stage, media_meta,
    project_state, read_json, root, run, write_json,
)
from edl import normalize, validate  # noqa: E402


def call(script: str, args: list[str], cfg, *, quiet=False):
    """Capture child output so stage logs print in order (tts.py may re-exec in venv)."""
    cmd = [sys.executable, str(SCRIPTS / script), *map(str, args)]
    p = run(cmd, cfg=cfg, capture=True, check=False)
    if p.returncode != 0:
        raise PipelineError(f"{script} failed ({p.returncode}):\n"
                            f"{(p.stdout or '')[-800:]}\n{(p.stderr or '')[-1500:]}")
    if not quiet and p.stdout:
        print(p.stdout, end="" if p.stdout.endswith("\n") else "\n")
    return p


def edl_path(paths) -> Path:
    return paths["project"] / "edl.json"


def cmd_init(args, cfg):
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in args.slug).strip("-")
    if not slug:
        raise PipelineError("invalid --slug")
    paths = ensure_tree(cfg, slug)
    ref, src = Path(args.reference).resolve(), Path(args.source).resolve()
    for label, p in (("reference", ref), ("source", src)):
        if not p.exists():
            raise PipelineError(f"{label} video not found: {p}")
    state = {
        "version": 1, "slug": slug,
        "inputs": {"reference": str(ref), "source": str(src)},
        "root": str(root(cfg)),
        "stages": {"init": {"done": True, "detail": "inputs registered"}},
        "config_snapshot": {"render": cfg["render"], "subtitle": cfg["default_subtitle"],
                            "scene_probability": cfg.get("scene_probability")},
    }
    write_json(paths["project_json"], state)
    emit("init", {"project": slug, "project_dir": str(paths["project"]),
                  "reference": media_meta(cfg, ref), "source": media_meta(cfg, src)})


def cmd_analyze(args, cfg):
    paths = ensure_tree(cfg, args.project)
    state = project_state(paths)
    if not state.get("stages", {}).get("init", {}).get("done"):
        raise PipelineError("run init first")
    from analyze import analyse
    summary = {}
    for role in ("reference", "source"):
        res = analyse(cfg, paths, role, Path(state["inputs"][role]))
        summary[role] = {k: v for k, v in res.items() if k not in ("keyframes", "contact_sheets", "voice_profile")}
        state.setdefault("analysis", {})[role] = {
            "video": res["video"], "duration_sec": res["duration_sec"],
            "shot_median_sec": res["shots"]["median_sec"],
            "keyframes": res["keyframes"], "contact_sheets": res["contact_sheets"],
        }
    write_json(paths["project_json"], state)
    mark_stage(paths, "analyze", "reference + source measured")
    emit("analyze", summary)


def cmd_plan(args, cfg):
    paths = ensure_tree(cfg, args.project)
    call("edl.py", ["template", "--project", args.project, "--out", edl_path(paths)], cfg, quiet=True)
    edl = read_json(edl_path(paths))
    script = paths["project"] / "script.md"
    if not script.exists():
        state = project_state(paths)
        a = state.get("analysis", {})
        script.write_text(
            "# 口播稿（待填写）\n\n"
            "参考案例时长 {}s、镜头中位数 {}s；素材 {}s。\n\n"
            "写作要求：\n"
            "- 每句对应 edl.json 的 lines[] 一项，一句一个意思，控制在 12-18 字以内。\n"
            "- 句子数量与镜头数、案例语速匹配；总字数约按每秒 4 字估算。\n"
            "- 写完后把每句按顺序列入 edl.json 的 lines[].text，并保留 id。\n"
            "- 案例风格参考见 reference/keyframes/ 与 contact_sheet_*.jpg。\n\n"
            "## 分句\n\n1. 【待填写】\n".format(
                a.get("reference", {}).get("duration_sec", "?"),
                a.get("reference", {}).get("shot_median_sec", "?"),
                a.get("source", {}).get("duration_sec", "?"),
            ), encoding="utf-8")
    emit("plan", {"edl": str(edl_path(paths)), "script": str(script),
                  "clips_in_template": len(edl["clips"]),
                  "note": "Agent must read contact sheets, write script.md, then fill lines[] in edl.json. "
                          "Show the plan to the user BEFORE calling finish."})


def cmd_finish(args, cfg):
    paths = ensure_tree(cfg, args.project)
    if args.edl:
        src = Path(args.edl).resolve()
        if src != edl_path(paths).resolve():
            shutil.copy2(src, edl_path(paths))
    edl = normalize(cfg, read_json(edl_path(paths)))
    errs = validate(cfg, edl, require_lines=True)
    if errs:
        raise PipelineError("EDL not ready:\n  - " + "\n  - ".join(errs))
    write_json(edl_path(paths), edl)
    mark_stage(paths, "plan", f"confirmed: {len(edl['clips'])} clips / {len(edl['lines'])} lines")

    call("tts.py", ["--project", args.project, "--edl", edl_path(paths)], cfg)
    mark_stage(paths, "voice", "synthesised")
    call("subtitle.py", ["--edl", edl_path(paths),
                         "--timing", paths["project"] / "voice" / "voice_timing.json",
                         "--out-dir", paths["project"] / "subtitles"], cfg)
    mark_stage(paths, "subtitle", "ASS + SRT generated")
    call("render.py", ["--project", args.project, "--edl", edl_path(paths)], cfg)
    emit("finish", {"final": str(paths["project"] / "output" / "final.mp4"),
                    "report": str(paths["project"] / "output" / "report.md")})


def cmd_status(args, cfg):
    paths = ensure_tree(cfg, args.project)
    state = project_state(paths)
    from common import STAGES
    out = {"project": args.project, "project_dir": str(paths["project"]),
           "inputs": state.get("inputs"),
           "stages": {s: bool(state.get("stages", {}).get(s, {}).get("done")) for s in STAGES},
           "artifacts": {
               "edl": str(edl_path(paths)), "script": str(paths["project"] / "script.md"),
               "voice_timing": str(paths["project"] / "voice" / "voice_timing.json"),
               "ass": str(paths["project"] / "subtitles" / "main.ass"),
               "final": str(paths["project"] / "output" / "final.mp4"),
           },
           "analysis": {k: {kk: vv for kk, vv in v.items() if kk not in ("keyframes", "contact_sheets")}
                        for k, v in state.get("analysis", {}).items()}}
    emit("status", out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--slug", required=True)
    p.add_argument("--reference", required=True)
    p.add_argument("--source", required=True)
    p = sub.add_parser("analyze"); p.add_argument("--project", required=True)
    p = sub.add_parser("plan"); p.add_argument("--project", required=True)
    p = sub.add_parser("finish"); p.add_argument("--project", required=True); p.add_argument("--edl")
    p = sub.add_parser("status"); p.add_argument("--project", required=True)
    args = ap.parse_args()
    cfg = load_config()
    {"init": cmd_init, "analyze": cmd_analyze, "plan": cmd_plan,
     "finish": cmd_finish, "status": cmd_status}[args.cmd](args, cfg)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (PipelineError, OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
