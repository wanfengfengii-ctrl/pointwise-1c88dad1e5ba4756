from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass

from audit.provenance import collect_provenance
from audit.totals import total_litres
from daily.snapshots import DaySnapshot


@dataclass(frozen=True)
class AuditRegion:
    region: str
    litres: int
    missing_reads: int
    valid_reads: int
    provenance: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class AuditView:
    day: str
    regions: tuple[AuditRegion, ...]
    total_litres: int


def initialize_audit_views(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS audit_views (
            receipt_number TEXT PRIMARY KEY NOT NULL REFERENCES receipts(receipt_number),
            view_json TEXT NOT NULL
        )
    ''')


def build_audit_view(snapshot: DaySnapshot) -> AuditView:
    references = collect_provenance(snapshot)
    regions = tuple(
        AuditRegion(balance.region, balance.litres, balance.missing_reads, balance.valid_reads, references[balance.region])
        for balance in snapshot.balances
    )
    return AuditView(snapshot.day, regions, total_litres(snapshot.balances))


def save_audit_view(db: sqlite3.Connection, receipt_number: str, view: AuditView) -> None:
    if not db.in_transaction:
        raise RuntimeError('audit publication requires a publication transaction')
    db.execute('INSERT INTO audit_views (receipt_number, view_json) VALUES (?, ?)', (
        receipt_number, json.dumps(asdict(view), sort_keys=True, separators=(',', ':')),
    ))


def load_audit_view(db: sqlite3.Connection, receipt_number: str) -> AuditView | None:
    row = db.execute('SELECT view_json FROM audit_views WHERE receipt_number = ?', (receipt_number,)).fetchone()
    if row is None:
        return None
    stored = json.loads(row[0])
    regions = tuple(
        AuditRegion(region['region'], region['litres'], region['missing_reads'],
                    region['valid_reads'], tuple(region['provenance']))
        for region in stored['regions']
    )
    return AuditView(stored['day'], regions, stored['total_litres'])
