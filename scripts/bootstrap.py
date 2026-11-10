#!/usr/bin/env python3
"""Create the D:-only workspace, venv and dependencies. Safe to re-run."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    PipelineError, check_drive, emit, ensure_tree, load_config, root, run, venv_python, which,
)


def tool_versions(cfg):
    out = {}
    for key in ("ffmpeg", "ffprobe"):
        exe = cfg.get(key, key)
        found = which(exe) or (str(exe) if Path(exe).exists() else None)
        out[key] = {"path": found, "ok": bool(found)}
        if found:
            p = run([found, "-version"], cfg=cfg, capture=True, check=False)
            out[key]["version"] = (p.stdout or "").splitlines()[0] if p.stdout else ""
    return out


def cjk_fonts():
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    if not fonts.exists():
        return []
    return sorted(
        f.name for f in fonts.iterdir()
        if f.suffix.lower() in (".ttf", ".ttc", ".otf")
        and any(k in f.name.lower() for k in ("msyh", "simhei", "notosanssc", "deng", "simsun"))
    )[:12]


def ensure_venv(cfg, install: bool = True) -> dict:
    py = venv_python(cfg)
    info = {"python": str(py), "exists": py.exists()}
    if not py.exists():
        created = run([sys.executable, "-m", "venv", str(root(cfg) / "venv")], cfg=cfg, capture=True, check=False)
        if created.returncode != 0:
            info["error"] = (created.stderr or "")[-800:]
            return info
        info["created"] = True
    info["exists"] = py.exists()
    if not py.exists():
        return info
    probe = run([py, "-c", "import edge_tts, sys; print(edge_tts.__version__ if hasattr(edge_tts,'__version__') else 'ok')"],
                cfg=cfg, capture=True, check=False)
    info["edge_tts_installed"] = probe.returncode == 0
    if install and not info["edge_tts_installed"]:
        res = run([py, "-m", "pip", "install", "--disable-pip-version-check", cfg.get("tts_dep", "edge-tts")],
                  cfg=cfg, capture=True, check=False, timeout=600)
        info["pip_exit"] = res.returncode
        info["pip_output_tail"] = (res.stdout or "")[-600:]
        probe2 = run([py, "-c", "import edge_tts; print('ok')"], cfg=cfg, capture=True, check=False)
        info["edge_tts_installed"] = probe2.returncode == 0
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-install", action="store_true", help="only create dirs and check tools")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    check_drive(cfg)
    paths = ensure_tree(cfg)
    report = {
        "root": str(paths["root"]),
        "drive_rule": f"all temp/cache/model/output paths are under {cfg['drive_required']}:",
        "tools": tool_versions(cfg),
        "venv": ensure_venv(cfg, install=not args.no_install) if not args.no_install else
                {"python": str(venv_python(cfg)), "exists": venv_python(cfg).exists(), "skipped_install": True},
        "cjk_fonts": cjk_fonts(),
        "whisper_models": [f.name for f in (root(cfg) / "models").glob("ggml-*.bin")],
        "directories": [str(root(cfg) / d) for d in cfg["subdirs"]],
    }
    missing = [k for k, v in report["tools"].items() if not v["ok"]]
    if missing:
        report["error"] = f"missing executables: {missing}; install ffmpeg and add to PATH, or set absolute paths in config.json"
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        emit("bootstrap", report)
    return 1 if missing else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PipelineError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
