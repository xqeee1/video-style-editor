#!/usr/bin/env python3
"""Shared runtime helpers. Enforces the "everything on D:" rule."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_DIR / "scripts" / "config.json"
STAGES = ["init", "analyze", "plan", "voice", "subtitle", "render"]


class PipelineError(RuntimeError):
    pass


def load_config(path=None) -> dict:
    p = Path(path) if path else CONFIG_PATH
    if not p.exists():
        raise PipelineError(f"config not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def root(cfg: dict) -> Path:
    return Path(cfg["root"])


def check_drive(cfg: dict) -> None:
    """Refuse to run if the working root escapes the required drive."""
    r = root(cfg).resolve()
    drive = r.drive.rstrip(":").upper()
    want = str(cfg.get("drive_required", "D")).upper()
    if os.name == "nt" and drive != want:
        raise PipelineError(
            f"root must be on {want}: drive, got {r}. Edit scripts/config.json. "
            "All intermediate files, caches, models and outputs must stay on D:."
        )
    for key in ("TMP", "TEMP", "TMPDIR"):
        os.environ[key] = str(r / "tmp")
    os.environ["PIP_CACHE_DIR"] = str(r / "cache" / "pip")


def env(cfg: dict) -> dict:
    check_drive(cfg)
    return os.environ.copy()


def venv_python(cfg: dict) -> Path:
    r = root(cfg)
    return r / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")


def run(cmd, *, cfg=None, cwd=None, capture=True, check=True, timeout=1800):
    """Run a command with temporary paths pinned to the project drive."""
    e = env(cfg) if cfg else os.environ.copy()
    cmd = [str(c) for c in cmd]
    proc = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, env=e,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if check and proc.returncode != 0:
        tail = (proc.stderr or "")[-2500:]
        raise PipelineError(f"command failed ({proc.returncode}): {' '.join(cmd[:8])}\n{tail}")
    return proc


def ffmpeg(cfg, args, **kw):
    return run([cfg["ffmpeg"], "-hide_banner", "-nostdin", "-y", *args], cfg=cfg, **kw)


def ffprobe_json(cfg, path) -> dict:
    p = run([
        cfg["ffprobe"], "-v", "error", "-show_entries",
        "format=duration,size:stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
        "-of", "json", str(path),
    ], cfg=cfg, capture=True, check=True)
    return json.loads(p.stdout)


def media_meta(cfg, path) -> dict:
    info = ffprobe_json(cfg, path)
    out = {"path": str(Path(path).resolve()), "streams": info.get("streams", [])}
    out["duration"] = float(info.get("format", {}).get("duration") or 0)
    vs = next((s for s in out["streams"] if s.get("codec_type") == "video"), None)
    au = next((s for s in out["streams"] if s.get("codec_type") == "audio"), None)
    if vs:
        num, _, den = str(vs.get("r_frame_rate", "30/1")).partition("/")
        try:
            out["fps"] = round(float(num) / float(den or 1), 3)
        except (ValueError, ZeroDivisionError):
            out["fps"] = 30.0
        out["width"], out["height"] = int(vs.get("width") or 0), int(vs.get("height") or 0)
    out["has_audio"] = au is not None
    return out


def peak_db(cfg: dict, audio_path) -> float | None:
    """Guard against rendering a stale or silent narration track."""
    p = run([cfg["ffmpeg"], "-hide_banner", "-nostdin", "-i", str(audio_path),
             "-af", "volumedetect", "-vn", "-f", "null", "-"],
            cfg=cfg, capture=True, check=False)
    vals = re.findall(r"max_volume:\s*([-\d.]+)\s*dB", p.stderr or "")
    return float(vals[-1]) if vals else None


def ensure_tree(cfg: dict, project: str | None = None) -> dict:
    r = root(cfg)
    for sub in cfg["subdirs"]:
        (r / sub).mkdir(parents=True, exist_ok=True)
    paths = {"root": r}
    if project:
        pdir = r / "projects" / project
        for sub in cfg["project_subdirs"]:
            (pdir / sub).mkdir(parents=True, exist_ok=True)
        paths["project"] = pdir
        paths["project_json"] = pdir / "project.json"
    return paths


def read_json(path, default=None):
    p = Path(path)
    if not p.exists():
        if default is not None:
            return default
        raise PipelineError(f"missing file: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(path, data) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def project_state(paths) -> dict:
    return read_json(paths["project_json"])


def mark_stage(paths, stage: str, detail: str = "") -> None:
    state = project_state(paths)
    state.setdefault("stages", {})
    state["stages"][stage] = {"done": True, "detail": detail}
    write_json(paths["project_json"], state)


def require_stage(paths, stage: str) -> None:
    state = project_state(paths)
    idx = STAGES.index(stage)
    missing = [s for s in STAGES[:idx] if not state.get("stages", {}).get(s, {}).get("done")]
    if missing:
        raise PipelineError(f"run these stages first: {', '.join(missing)}")


def seconds(t: str) -> float:
    """Parse HH:MM:SS.mmm or plain seconds."""
    try:
        return float(t)
    except (TypeError, ValueError):
        pass
    parts = str(t).split(":")
    v = 0.0
    for p in parts:
        v = v * 60 + float(p)
    return v


def which(name: str) -> str | None:
    return shutil.which(name)


def emit(title: str, payload=None) -> None:
    print(f"[{title}]")
    if payload is not None:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
