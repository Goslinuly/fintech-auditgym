"""Episode explorer: a small stdlib HTTP server for the web UI.

Not part of the official check (``make check`` never imports it). Serves ``ui/static`` and a
read-only JSON API over the open train/dev/sample episodes, plus on-the-fly generation.

    python ui/server.py            # http://localhost:8000 (PORT env var respected)

API
    GET /api/presets                       preset names
    GET /api/episodes                      open episodes with summary
    GET /api/episodes/<split>/<name>       public records + reference (open splits only)
    GET /api/generate?preset=..&seed=..    fresh episode, not written to disk
    GET /healthz
"""

from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auditgym.generator import GENERATOR_VERSION, generate, load_config, load_episode  # noqa: E402

STATIC = ROOT / "ui" / "static"
EPISODES = ROOT / "episodes"
PRESETS = ROOT / "configs" / "presets"
OPEN_SPLITS = ("sample", "train", "dev")
MAX_SEED = 10**9


def preset_names() -> list[str]:
    return sorted(p.stem for p in PRESETS.glob("*.json"))


def episode_dirs() -> list[tuple[str, str]]:
    out = []
    for split in OPEN_SPLITS:
        d = EPISODES / split
        if d.is_dir():
            out += [(split, p.name) for p in sorted(d.iterdir()) if (p / "public" / "manifest.json").exists()]
    return out


def summary(split: str, name: str, public: dict, truth: dict) -> dict:
    m = public["manifest"]
    return {
        "split": split,
        "name": name,
        "episode_id": m["episode_id"],
        "company_name": m["company_name"],
        "base_currency": m["base_currency"],
        "period_start": m["period_start"],
        "counts": m["counts"],
        "issues": len(truth["issues"]),
        "groups": sorted({i["group"] for i in truth["issues"] if i["group"]}),
    }


@lru_cache(maxsize=64)
def load_cached(split: str, name: str) -> dict:
    public, private = load_episode(EPISODES / split / name)
    return {"public": public, "truth": private["truth"], "provenance": private["provenance"]}


@lru_cache(maxsize=1)
def episodes_index() -> list[dict]:
    out = []
    for split, name in episode_dirs():
        ep = load_cached(split, name)
        out.append(summary(split, name, ep["public"], ep["truth"]))
    return out


@lru_cache(maxsize=128)
def generated(preset: str, seed: int) -> dict:
    ep = generate(seed, load_config(PRESETS / f"{preset}.json"))
    return {"public": ep.public, "truth": ep.private["truth"], "provenance": ep.private["provenance"]}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, fmt, *args):  # quieter logs
        sys.stderr.write("%s %s\n" % (self.command, self.path))

    def send_json(self, obj, status=HTTPStatus.OK):
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def error_json(self, status, message):
        self.send_json({"error": message}, status)

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        path = url.path
        if path == "/healthz":
            return self.send_json({"ok": True, "generator_version": GENERATOR_VERSION})
        if path == "/api/presets":
            return self.send_json({"presets": preset_names(), "generator_version": GENERATOR_VERSION})
        if path == "/api/episodes":
            return self.send_json({"episodes": episodes_index()})
        if path.startswith("/api/episodes/"):
            parts = path.split("/")[3:]
            if len(parts) != 2 or tuple(parts) not in set(episode_dirs()):
                return self.error_json(HTTPStatus.NOT_FOUND, "episode not found")
            return self.send_json(load_cached(*parts))
        if path == "/api/generate":
            q = parse_qs(url.query)
            preset = (q.get("preset") or [""])[0]
            try:
                seed = int((q.get("seed") or [""])[0])
            except ValueError:
                return self.error_json(HTTPStatus.BAD_REQUEST, "seed must be an integer")
            if preset not in preset_names() or not 0 <= seed <= MAX_SEED:
                return self.error_json(HTTPStatus.BAD_REQUEST, "unknown preset or seed out of range")
            try:
                return self.send_json(generated(preset, seed))
            except Exception as exc:  # generation failure is reported, never crashes the server
                return self.error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"generation failed: {exc}")
        if path.startswith("/api/"):
            return self.error_json(HTTPStatus.NOT_FOUND, "unknown endpoint")
        if path != "/" and not (STATIC / path.lstrip("/")).is_file():
            self.path = "/"  # single-page app fallback
        return super().do_GET()


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"auditgym explorer on http://0.0.0.0:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
