"""Shared case state, ownership and current permission checks."""

from __future__ import annotations

import json
from pathlib import Path


class Workspace:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.read()

    def read(self) -> dict:
        data = json.loads(self.path.read_text())
        if not isinstance(data.get("cases"), list) or not isinstance(data.get("audit"), list):
            raise ValueError("invalid case workspace")
        return data

    def save(self, data: dict):
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps(data, indent=2) + "\n")
        temp.replace(self.path)

    @staticmethod
    def _case(data: dict, case_id: str) -> dict:
        row = next((item for item in data["cases"] if item["id"] == case_id), None)
        if row is None:
            raise ValueError("unknown case")
        return row

    def get(self, case_id: str) -> dict:
        return self._case(self.read(), case_id)

    def assign(self, case_id: str, specialist: str) -> dict:
        if specialist not in ("billing", "account"):
            raise ValueError("unknown specialist")
        data = self.read(); row = self._case(data, case_id)
        row["owner"] = specialist
        row["revision"] += 1
        row["handoffs"].append(specialist)
        data["audit"].append({"case_id": case_id, "event": "handoff", "to": specialist})
        self.save(data)
        return row

    def change_permissions(self, case_id: str, *, billing_write: bool | None = None,
                           account_write: bool | None = None) -> dict:
        data = self.read(); row = self._case(data, case_id)
        if billing_write is not None:
            row["billing_write"] = billing_write
        if account_write is not None:
            row["account_write"] = account_write
        row["revision"] += 1
        self.save(data)
        return row

    def credit(self, case_id: str, expected_revision: int, amount_cents: int) -> dict:
        data = self.read(); row = self._case(data, case_id)
        if row["owner"] != "billing":
            return {"error": "wrong_owner"}
        if not row["billing_write"] or not row["verified"]:
            return {"error": "permission_denied"}
        if row["revision"] != expected_revision:
            return {"error": "stale_revision"}
        if row["credit_issued"]:
            return {"error": "already_credited"}
        if amount_cents != row["charge_cents"] or amount_cents <= 0 or amount_cents > 5000:
            return {"error": "amount_not_supported"}
        row["credit_issued"] = True; row["revision"] += 1
        data["audit"].append({"case_id": case_id, "event": "credit_issued", "amount_cents": amount_cents})
        self.save(data)
        return {"case_id": case_id, "credit_cents": amount_cents, "revision": row["revision"]}

    def unlock(self, case_id: str, expected_revision: int) -> dict:
        data = self.read(); row = self._case(data, case_id)
        if row["owner"] != "account":
            return {"error": "wrong_owner"}
        if not row["account_write"] or not row["verified"]:
            return {"error": "permission_denied"}
        if row["revision"] != expected_revision:
            return {"error": "stale_revision"}
        if not row["account_locked"]:
            return {"error": "already_unlocked"}
        row["account_locked"] = False; row["revision"] += 1
        data["audit"].append({"case_id": case_id, "event": "account_unlocked"})
        self.save(data)
        return {"case_id": case_id, "unlocked": True, "revision": row["revision"]}
