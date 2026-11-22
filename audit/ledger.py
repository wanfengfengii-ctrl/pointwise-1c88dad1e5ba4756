from __future__ import annotations

import sqlite3

from audit.capture import AuditView, build_audit_view
from audit.history import read_receipt_history
from daily.snapshots import take_day_snapshot
from intake.stations import require_identifier


def read(db: sqlite3.Connection, *, day: str | None = None, receipt_number: str | None = None) -> AuditView | None:
    if (day is None) == (receipt_number is None):
        raise ValueError('supply a date or a receipt number')
    if receipt_number is not None:
        return read_receipt_history(db, require_identifier(receipt_number, 'receipt_number'))
    snapshot = take_day_snapshot(db, day)
    return build_audit_view(snapshot) if snapshot.established else None
