"""Read-only, copy-on-read access to the public part of one episode.

The store is the only place the environment keeps episode data, and it only ever receives
``Episode.public`` (manifest, policy, ledger, invoices, payments, fx). Every value handed out
is a fresh deep copy, so an agent cannot modify records through a returned object.

Record references use one form everywhere: ``"<table>:<id>"`` — ``ledger:<row_id>``,
``invoices:<invoice_id>``, ``payments:<payment_id>``, ``fx:<date>/<source>/<target>``.
Pagination cursors are opaque tokens derived from (episode_id, table, offset): the same
position in the same episode always gives the same token, and a token from another table,
another episode or one never issued is rejected.
"""

from __future__ import annotations

import copy
import hashlib
import json

from .errors import bad_args, not_found

TABLES = ("ledger", "invoices", "payments", "fx")
PUBLIC_KEYS = ("manifest", "policy", "ledger", "invoices", "payments", "fx")
MAX_PAGE = 50
MAX_ID_LEN = 100


def record_key(table: str, record: dict) -> str:
    if table == "ledger":
        return record["row_id"]
    if table == "invoices":
        return record["invoice_id"]
    if table == "payments":
        return record["payment_id"]
    return f"{record['date']}/{record['source_currency']}/{record['target_currency']}"


def ref(table: str, key: str) -> str:
    return f"{table}:{key}"


def _frozen_copy(value):
    """Deep, JSON-only copy: drops anything that is not plain JSON data."""
    return json.loads(json.dumps(value, ensure_ascii=False))


class PublicStore:
    def __init__(self, public: dict, episode_id: str):
        missing = [k for k in PUBLIC_KEYS if k not in public]
        if missing:
            raise ValueError(f"public episode data is missing {missing}")
        extra = sorted(set(public) - set(PUBLIC_KEYS))
        if extra:
            # Defence in depth: never accept anything beyond the published files.
            raise ValueError(f"public episode data has unexpected parts {extra}")
        self.episode_id = episode_id
        self._public = {k: _frozen_copy(public[k]) for k in PUBLIC_KEYS}
        self._index: dict[str, dict[str, int]] = {}
        for table in TABLES:
            idx: dict[str, int] = {}
            for i, rec in enumerate(self._public[table]):
                idx[record_key(table, rec)] = i
            self._index[table] = idx
        self._documents: dict[str, tuple[str, int]] = {}
        for table in ("invoices", "payments"):
            id_field = "invoice_id" if table == "invoices" else "payment_id"
            for i, doc in enumerate(self._public[table]):
                for alias in (doc["doc_id"], doc[id_field], ref(table, doc[id_field])):
                    self._documents[alias] = (table, i)
        self._cursors: dict[str, tuple[str, int]] = {}

    # --- whole-file views -------------------------------------------------------------
    @property
    def manifest(self) -> dict:
        return copy.deepcopy(self._public["manifest"])

    @property
    def policy(self) -> dict:
        return copy.deepcopy(self._public["policy"])

    def counts(self) -> dict[str, int]:
        return {t: len(self._public[t]) for t in TABLES}

    def contains(self, table: str, key: str) -> bool:
        return key in self._index.get(table, {})

    # --- cursors ------------------------------------------------------------------------
    def _cursor_for(self, table: str, offset: int) -> str:
        digest = hashlib.sha256(f"{self.episode_id}\x00{table}\x00{offset}".encode()).hexdigest()
        token = "c_" + digest[:20]
        self._cursors[token] = (table, offset)
        return token

    def _offset_from(self, table: str, cursor) -> int:
        if cursor is None or cursor == "":
            return 0
        if not isinstance(cursor, str) or len(cursor) > 64:
            raise bad_args("cursor must be a string returned by list_records")
        hit = self._cursors.get(cursor)
        if hit is None or hit[0] != table:
            raise bad_args("unknown cursor for this table")
        return hit[1]

    # --- actions ------------------------------------------------------------------------
    @staticmethod
    def _check_table(table) -> str:
        if not isinstance(table, str) or table not in TABLES:
            raise bad_args("table must be one of: ledger, invoices, payments, fx")
        return table

    def list_records(self, table, cursor=None, limit=MAX_PAGE) -> dict:
        table = self._check_table(table)
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise bad_args("limit must be an integer")
        if not 1 <= limit <= MAX_PAGE:
            raise bad_args(f"limit must be between 1 and {MAX_PAGE}")
        offset = self._offset_from(table, cursor)
        rows = self._public[table]
        page = rows[offset : offset + limit]
        end = offset + len(page)
        return {
            "table": table,
            "items": [
                {"ref": ref(table, record_key(table, r)), "record": copy.deepcopy(r)} for r in page
            ],
            "next_cursor": self._cursor_for(table, end) if end < len(rows) else None,
            "total": len(rows),
        }

    @staticmethod
    def _clean_id(value, table: str | None = None) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_ID_LEN:
            raise bad_args(f"id must be a non-empty string of at most {MAX_ID_LEN} characters")
        value = value.strip()
        if table is not None and ":" in value:
            prefix, _, rest = value.partition(":")
            if prefix.strip().lower() != table:
                raise not_found()
            value = rest.strip()
        return value

    def get_record(self, table, id) -> dict:
        table = self._check_table(table)
        key = self._clean_id(id, table)
        i = self._index[table].get(key)
        if i is None:
            raise not_found()
        rec = self._public[table][i]
        return {"ref": ref(table, key), "record": copy.deepcopy(rec)}

    def get_document(self, doc_id) -> dict:
        key = self._clean_id(doc_id)
        if ":" in key:
            prefix, _, rest = key.partition(":")
            key = f"{prefix.strip().lower()}:{rest.strip()}"
        hit = self._documents.get(key)
        if hit is None:
            raise not_found("no document with this id")
        table, i = hit
        doc = self._public[table][i]
        id_field = "invoice_id" if table == "invoices" else "payment_id"
        return {
            "doc_id": doc["doc_id"],
            "kind": "invoice" if table == "invoices" else "payment",
            "ref": ref(table, doc[id_field]),
            "document": copy.deepcopy(doc),
        }
