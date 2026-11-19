"""Harmless transformations for paired checks.

``remap_ids`` renames every invoice, payment, document, ledger row and entity ID and rewrites
truth consistently; ``shuffle_rows`` reorders invoices, payments and ledger rows. Neither changes
the economics, so the oracle answer (with renamed IDs) and its reward must stay the same.
"""

from __future__ import annotations

import copy
import random

from .ids import IdFactory


def _map_ref(ref: str, mapping: dict[str, str]) -> str:
    table, sep, key = ref.partition(":")
    if sep and key in mapping:
        return f"{table}:{mapping[key]}"
    return ref


def remap_ids(public: dict, truth: dict, seed: int) -> tuple[dict, dict, dict[str, str]]:
    rng = random.Random(f"auditgym:remap:{seed}")
    pub = copy.deepcopy(public)
    tr = copy.deepcopy(truth)
    existing = set()
    for r in pub["ledger"]:
        existing.add(r["row_id"])
    for i in pub["invoices"]:
        existing.update((i["invoice_id"], i["doc_id"], i["entity_id"]))
    for p in pub["payments"]:
        existing.update((p["payment_id"], p["doc_id"], p["entity_id"]))
    ids = IdFactory(rng, used=set(existing))
    mapping: dict[str, str] = {}

    def m(old, kind):
        if old is None:
            return None
        if old not in mapping:
            mapping[old] = ids.new(kind)
        return mapping[old]

    for i in pub["invoices"]:
        i["doc_id"], i["invoice_id"], i["entity_id"] = m(i["doc_id"], "doc"), m(i["invoice_id"], "invoice"), m(i["entity_id"], "entity")
    for p in pub["payments"]:
        p["doc_id"], p["payment_id"], p["entity_id"] = m(p["doc_id"], "doc"), m(p["payment_id"], "payment"), m(p["entity_id"], "entity")
    for p in pub["payments"]:
        for ref in p["invoice_refs"]:
            ref["invoice_id"] = mapping[ref["invoice_id"]]
        if p["refund_of"]:
            p["refund_of"] = mapping[p["refund_of"]]
    for r in pub["ledger"]:
        r["row_id"] = m(r["row_id"], "row")
        r["entity_id"] = m(r["entity_id"], "entity")
        r["invoice_id"] = mapping.get(r["invoice_id"], r["invoice_id"])
        r["payment_id"] = mapping.get(r["payment_id"], r["payment_id"])
    for issue in tr["issues"]:
        issue["affected_ids"] = sorted(_map_ref(x, mapping) for x in issue["affected_ids"])
        issue["evidence_sets"] = [sorted(_map_ref(x, mapping) for x in s) for s in issue["evidence_sets"]]
    return pub, tr, mapping


def shuffle_rows(public: dict, seed: int) -> dict:
    rng = random.Random(f"auditgym:shuffle:{seed}")
    pub = copy.deepcopy(public)
    for table in ("ledger", "invoices", "payments"):
        rng.shuffle(pub[table])
    return pub
