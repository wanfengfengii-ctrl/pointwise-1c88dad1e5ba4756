from __future__ import annotations

import sqlite3

from audit.capture import AuditView, load_audit_view
from receive.receipts import find_receipt_by_number


def read_receipt_history(db: sqlite3.Connection, receipt_number: str) -> AuditView | None:
    receipt = find_receipt_by_number(db, receipt_number)
    if receipt is None:
        return None
    view = load_audit_view(db, receipt.receipt_number)
    if view is None:
        raise RuntimeError(f'published receipt {receipt_number} has no audit view')
    return view
