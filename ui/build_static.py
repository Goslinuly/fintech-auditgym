"""Build a static mirror of the episode explorer (for GitHub Pages).

    python ui/build_static.py --out site

Writes ``ui/static`` plus every API response the UI needs as plain JSON files:
the open train/dev/sample episodes and a fixed set of pre-generated seeds per preset.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))

import server  # noqa: E402

SEEDS = list(range(10)) + [42]


def write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    out = Path(ap.parse_args().out)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(server.STATIC, out)
    (out / "config.js").write_text(f"window.AUDITGYM_STATIC = {json.dumps({'seeds': SEEDS})};\n")
    (out / ".nojekyll").write_text("")

    api = out / "api"
    write(api / "presets.json", {"presets": server.preset_names(), "generator_version": server.GENERATOR_VERSION})
    write(api / "episodes.json", {"episodes": server.episodes_index()})
    for split, name in server.episode_dirs():
        write(api / "episodes" / split / f"{name}.json", server.load_cached(split, name))
    for preset in server.preset_names():
        for seed in SEEDS:
            write(api / "generate" / preset / f"{seed}.json", server.generated(preset, seed))
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"static mirror -> {out} ({size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
