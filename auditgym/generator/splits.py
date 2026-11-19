"""Open train/dev/sample splits: build episodes from ``configs/<split>/episodes.json`` and
record SHA-256 checksums so anyone can confirm byte-exact reproduction."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from . import generate, load_config

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLISHED_SPLITS = ("train", "dev", "sample")


def split_spec(split: str, root: Path = REPO_ROOT) -> list[dict]:
    data = json.loads((root / "configs" / split / "episodes.json").read_text(encoding="utf-8"))
    return data["episodes"]


def iter_split(split: str, root: Path = REPO_ROOT):
    for item in split_spec(split, root):
        yield item, generate(item["seed"], load_config(root / item["config"]))


def checksums(directory: Path) -> str:
    lines = []
    for path in sorted(p for p in directory.rglob("*") if p.is_file() and p.name != "SHA256SUMS"):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(directory).as_posix()}")
    return "\n".join(lines) + "\n"


def build_split(split: str, out_root: Path, root: Path = REPO_ROOT) -> Path:
    out = out_root / split
    out.mkdir(parents=True, exist_ok=True)
    for item, ep in iter_split(split, root):
        ep.write(out / item["name"])
    (out / "SHA256SUMS").write_text(checksums(out), encoding="utf-8")
    return out


def check_split(split: str, root: Path = REPO_ROOT) -> list[str]:
    """Regenerate a split in a temp dir and compare with the committed checksums."""
    committed = root / "episodes" / split / "SHA256SUMS"
    if not committed.exists():
        return [f"{split}: {committed} missing; run `python -m auditgym.generator build`"]
    with tempfile.TemporaryDirectory() as tmp:
        fresh = build_split(split, Path(tmp), root)
        new = (fresh / "SHA256SUMS").read_text(encoding="utf-8")
    old = committed.read_text(encoding="utf-8")
    if new == old:
        return []
    diff = sorted(set(old.splitlines()) ^ set(new.splitlines()))
    return [f"{split}: regenerated files differ from committed checksums: {diff[:4]}"]
