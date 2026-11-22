from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Receipt:
    acceptance_id: str
    receipt_number: str
    package_id: str
    source_batch: str
    day: str


def initialize_receipts(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS receipts (
            acceptance_id TEXT PRIMARY KEY NOT NULL,
            receipt_number TEXT UNIQUE NOT NULL,
            package_id TEXT NOT NULL,
            source_batch TEXT NOT NULL,
            day TEXT NOT NULL
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS receipts_by_day ON receipts(day)')


def find_receipt_by_acceptance(db: sqlite3.Connection, acceptance_id: str) -> Receipt | None:
    row = db.execute('''
        SELECT acceptance_id, receipt_number, package_id, source_batch, day
        FROM receipts WHERE acceptance_id = ?
    ''', (acceptance_id,)).fetchone()
    return None if row is None else Receipt(*row)


def find_receipt_by_number(db: sqlite3.Connection, receipt_number: str) -> Receipt | None:
    row = db.execute('''
        SELECT acceptance_id, receipt_number, package_id, source_batch, day
        FROM receipts WHERE receipt_number = ?
    ''', (receipt_number,)).fetchone()
    return None if row is None else Receipt(*row)


def has_day_receipt(db: sqlite3.Connection, day: str) -> bool:
    return db.execute('SELECT 1 FROM receipts WHERE day = ? LIMIT 1', (day,)).fetchone() is not None


def bind_receipt(db: sqlite3.Connection, receipt: Receipt) -> None:
    if not db.in_transaction:
        raise RuntimeError('receipt binding requires a publication transaction')
    db.execute('''
        INSERT INTO receipts (acceptance_id, receipt_number, package_id, source_batch, day)
        VALUES (?, ?, ?, ?, ?)
    ''', (receipt.acceptance_id, receipt.receipt_number, receipt.package_id, receipt.source_batch, receipt.day))
