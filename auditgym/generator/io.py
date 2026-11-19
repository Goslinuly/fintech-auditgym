"""Reading and writing episode directories.

Layout::

    <episode>/public/manifest.json  policy.json  ledger.csv  invoices.jsonl  payments.jsonl  fx.csv
    <episode>/private/truth.json    provenance.json

``public/`` is what the environment may expose; ``private/`` is for the grader only.
CSV: UTF-8, ``\\n`` line endings, empty cell = null. JSONL: one compact object per line.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from .render import LEDGER_COLUMNS

FX_COLUMNS = ("date", "source_currency", "target_currency", "rate")
PUBLIC_FILES = ("manifest.json", "policy.json", "ledger.csv", "invoices.jsonl", "payments.jsonl", "fx.csv")
PRIVATE_FILES = ("truth.json", "provenance.json")


def dump_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def dump_jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows)


def dump_csv(rows: list[dict], columns: tuple[str, ...]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for r in rows:
        w.writerow(["" if r[c] is None else r[c] for c in columns])
    return buf.getvalue()


def public_files(public: dict) -> dict[str, str]:
    return {
        "manifest.json": dump_json(public["manifest"]),
        "policy.json": dump_json(public["policy"]),
        "ledger.csv": dump_csv(public["ledger"], LEDGER_COLUMNS),
        "invoices.jsonl": dump_jsonl(public["invoices"]),
        "payments.jsonl": dump_jsonl(public["payments"]),
        "fx.csv": dump_csv(public["fx"], FX_COLUMNS),
    }


def write_episode(public: dict, private: dict, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    (out / "public").mkdir(parents=True, exist_ok=True)
    (out / "private").mkdir(parents=True, exist_ok=True)
    for name, text in public_files(public).items():
        (out / "public" / name).write_text(text, encoding="utf-8")
    (out / "private" / "truth.json").write_text(dump_json(private["truth"]), encoding="utf-8")
    (out / "private" / "provenance.json").write_text(dump_json(private["provenance"]), encoding="utf-8")
    return out


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return [{k: (v if v != "" else None) for k, v in row.items()} for row in csv.DictReader(f)]


def load_public(episode_dir: str | Path) -> dict:
    pub = Path(episode_dir) / "public"
    ledger = _read_csv(pub / "ledger.csv")
    for r in ledger:
        r["debit_minor"] = int(r["debit_minor"])
        r["credit_minor"] = int(r["credit_minor"])
    return {
        "manifest": json.loads((pub / "manifest.json").read_text(encoding="utf-8")),
        "policy": json.loads((pub / "policy.json").read_text(encoding="utf-8")),
        "ledger": ledger,
        "invoices": [json.loads(x) for x in (pub / "invoices.jsonl").read_text(encoding="utf-8").splitlines() if x],
        "payments": [json.loads(x) for x in (pub / "payments.jsonl").read_text(encoding="utf-8").splitlines() if x],
        "fx": _read_csv(pub / "fx.csv"),
    }


def load_private(episode_dir: str | Path) -> dict:
    priv = Path(episode_dir) / "private"
    return {
        "truth": json.loads((priv / "truth.json").read_text(encoding="utf-8")),
        "provenance": json.loads((priv / "provenance.json").read_text(encoding="utf-8")),
    }
